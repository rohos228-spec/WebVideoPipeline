"""Шаг img_pr: промты картинок через apply-ops (DB SoT)."""

from __future__ import annotations

from typing import Any, cast

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.db_busy import is_db_busy
from app.services.step_cancel import StepCancelledError, raise_if_cancelled
from app.services.xlsx_step_runners import _is_real_shot  # единый отбор, не дублировать
from app.storage import for_project as _sheet_for_project

# Перенос из int/chat-v1 (8d1fa5c0, 144b05d7): промт, собранный из одних
# заглушек «нет исходных данных для заполнения», считается отсутствующим.
# Шаг картинок такой промт не примет, а img_pr без этого правила повторным
# запуском сразу выходил «уже в БД» — тупик для пользователя.
_HOLLOW_PLACEHOLDER = "нет исходных данных для заполнения"


def _is_scene_prompt_hollow(prompt: str, *, min_placeholders: int = 3) -> bool:
    try:
        from app.generation_options import is_scene_prompt_hollow
    except ImportError:
        return (prompt or "").lower().count(_HOLLOW_PLACEHOLDER) >= min_placeholders
    return is_scene_prompt_hollow(prompt, min_placeholders=min_placeholders)


def _image_prompt_missing(prompt: str | None) -> bool:
    """Промта нет: пусто или одни заглушки (если заглушки не разрешены настройкой).

    Один детектор заглушек на img_pr, картинки и чат: generation_options.image_prompt_missing
    (img_pr_guard.prompt_is_hollow по разделам Фон/Действие/…).
    """
    from app.generation_options import image_prompt_missing

    return image_prompt_missing(prompt)


def _frames_needing_image_prompt(frames: list[Frame]) -> list[Frame]:
    return [fr for fr in frames if _is_real_shot(fr) and _image_prompt_missing(fr.image_prompt)]


_STAGING_ATTRS = (
    "place",
    "shot01_bg",
    "shot01_action",
    "shot01_description",
    "lighting",
    "scene_lighting",
    "персонажи",
    "characters",
    "continuity",
    "meaning",
)


def _frame_has_staging(fr: Frame) -> bool:
    """У кадра есть постановочные данные (не голый закадр)."""
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    if str(getattr(fr, "meaning", None) or "").strip():
        return True
    return any(str(attrs.get(k) or "").strip() for k in _STAGING_ATTRS)


def _frames_with_image_prompt(frames: list[Frame]) -> list[Frame]:
    return [fr for fr in frames if _is_real_shot(fr) and (fr.image_prompt or "").strip()]


async def _reload_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project_id)
                .order_by(Frame.number)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _scene_fingerprint(session: AsyncSession, frames: list[Frame], numbers: set[int]) -> str:
    """Слепок входа сцен для кадров ``numbers`` (поля кадра + их строки Scene)."""
    from app.models import Scene
    from app.services import img_pr_batches as ipb

    picked = [fr for fr in frames if fr.number in numbers]
    scene_ids = {fr.scene_id for fr in picked if fr.scene_id}
    scenes: list = []
    if scene_ids:
        scenes = list((await session.execute(select(Scene).where(Scene.id.in_(scene_ids)))).scalars().all())
    return ipb.scene_input_fingerprint(picked, scenes)


async def _raise_if_no_source_unchanged(
    session: AsyncSession, project: Project, frames: list[Frame], need: list[Frame]
) -> None:
    """Прошлый запуск встал на «нет исходных данных», а данные сцен те же — модель не зовём.

    Повтор того же входа даёт тот же ответ (платный вызов впустую). Шаг сразу
    сообщает ту же причину, политика отказов снова ставит проект на паузу.
    """
    from app.services import img_pr_batches as ipb

    meta = project.meta if isinstance(project.meta, dict) else {}
    fs = cast(dict[str, Any], meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else {})
    guard = fs.get(ipb.NO_SOURCE_GUARD_KEY)
    if not isinstance(guard, dict):
        return
    if not guard.get("fingerprint"):
        logger.warning(
            "[#{}] img_pr: пауза «нет исходных данных» (кадры {}) без слепка данных сцен — "
            "проверка до вызова модели отключена, шаг пойдёт в модель",
            project.id,
            guard.get("frame_numbers"),
        )
        return
    try:
        nums = {int(n) for n in guard.get("frame_numbers") or []}
    except (TypeError, ValueError):
        return
    if not nums or not nums <= {fr.number for fr in need}:
        return
    fp = await _scene_fingerprint(session, frames, nums)
    if fp != guard.get("fingerprint"):
        logger.info(
            "[#{}] img_pr: данные сцен изменились после паузы «нет исходных данных» — запускаю",
            project.id,
        )
        return
    logger.warning(
        "[#{}] img_pr: данные сцен кадров {} не менялись с паузы — без вызова модели",
        project.id,
        sorted(nums),
    )
    raise ipb.ImgPrNoSourceData(sorted(nums), scene_fingerprint=fp)


async def _finish_success(session: AsyncSession, project: Project, frames: list[Frame]) -> None:
    filled = _frames_with_image_prompt(frames)
    still_missing = _frames_needing_image_prompt(frames)
    if still_missing:
        missing_nums = [fr.number for fr in still_missing]
        raise RuntimeError(f"GPT не заполнил image_prompt для кадров: {missing_nums}")
    if not filled:
        raise RuntimeError("GPT не заполнил ни одного image_prompt")

    # Расстановка (continuity) посчитана кодом и обязана быть в промте. Агент
    # её игнорировал на живом прогоне #2 (1 промт из 24) — здесь требование
    # стоит проверкой, а не только просьбой в мастер-промте.
    from app.services.img_pr_continuity import enforce_continuity_in_prompts

    await enforce_continuity_in_prompts(session, project)

    # Второй герой и фон — та же болезнь, что была у расстановки. Фотография
    # у провайдера одна на кадр, поэтому сходство второго держит только текст,
    # а текст модель сочиняет заново в каждом батче: c02 из первых кадров и
    # c02 из последних описаны разными словами. Канон собирается из паспорта
    # героя и фона сцены один раз и вставляется дословно.
    from app.services.img_pr_canon import enforce_canon_in_prompts

    await enforce_canon_in_prompts(session, project)

    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="img_pr")

    for fr in filled:
        fr.status = FrameStatus.image_prompt_ready
    project.status = ProjectStatus.image_prompts_ready
    await session.flush()
    await session.commit()

    from app.services import img_pr_batches as ipb

    try:
        ipb.clear_checkpoint(project.data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] clear img_pr checkpoint: {}", project.id, e)

    logger.info(
        "[#{}] generate_image_prompts complete: {} промтов (DB)",
        project.id,
        len(filled),
    )


async def _apply_img_pr_ops_once(project_id: int, ops: list[dict]) -> None:
    """apply-ops img_pr в DB одной короткой сессией; повтор при занятой базе."""
    import asyncio

    from app.db import SessionLocal
    from app.services import db_apply

    for apply_try in range(1, 6):
        try:
            async with SessionLocal() as apply_session:
                proj = await apply_session.get(Project, project_id)
                if proj is None:
                    raise RuntimeError("project gone during img_pr apply")
                await db_apply.apply_ops(
                    apply_session,
                    proj,
                    ops,
                    export_xlsx=False,
                    node_kind="img_pr",
                )
                await apply_session.commit()
            return
        except Exception as apply_err:  # noqa: BLE001
            if not is_db_busy(apply_err) or apply_try >= 5:
                raise
            wait_s = min(2 * apply_try, 10)
            logger.warning(
                "[#{}] img_pr final apply locked try {}/5 — sleep {}s",
                project_id,
                apply_try,
                wait_s,
            )
            await asyncio.sleep(wait_s)


async def run(session: AsyncSession, project: Project, bot: Any = None) -> None:
    if project.status is not ProjectStatus.generating_image_prompts:
        return
    logger.info("[#{}] generate_image_prompts (db-first) starting", project.id)

    from app.services import db_v2

    await db_v2.backfill_project_v2(session, project)

    frames = await _reload_frames(session, project.id)
    if not frames:
        raise RuntimeError("нет кадров — нечего составлять промты")

    need = _frames_needing_image_prompt(frames)
    if not need:
        # Уже всё в DB (прошлый apply успел, а шаг упал на snapshot/greenlet).
        if _frames_with_image_prompt(frames):
            logger.info(
                "[#{}] generate_image_prompts: prompts already in DB — finish",
                project.id,
            )
            await _finish_success(session, project, frames)
            return
        raise RuntimeError("нет кадров с закадром — нечего составлять промты")

    # Префлайт: пустая постановка = тихий дженерик по голому закадру.
    # Лучше упасть с понятной ошибкой, чем сжечь деньги на мусор.
    staged = [fr for fr in need if _frame_has_staging(fr)]
    if not staged:
        raise RuntimeError(
            f"у {len(need)} кадров нет постановки (место/фон/действие/свет/"
            "персонажи/meaning) — прогони enrich/scene до img_pr, иначе "
            "получится дженерик по голому закадру"
        )
    if len(staged) < len(need):
        bare = [fr.number for fr in need if fr not in staged]
        logger.warning(
            "[#{}] generate_image_prompts: у кадров {} нет постановки — по ним будет дженерик",
            project.id,
            bare[:12],
        )

    # До любого вызова модели: пауза «нет исходных данных» и вход сцен не менялся.
    await _raise_if_no_source_unchanged(session, project, frames, need)

    uuid_map = "\n".join(f"кадр {fr.number} = {fr.uuid}" for fr in need if fr.uuid)
    if not uuid_map.strip():
        raise RuntimeError("у кадров нет uuid — backfill не сработал")

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_initialized(project_id=project.id, slug=project.slug)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] ensure_initialized: {}", project.id, e)

    cancelled = False
    last_err: Exception | None = None
    # Кадры «нет данных сцены» с 1-й попытки: во 2-ю их не шлём (тот же вход — тот же
    # ответ, лишний платный батч); пауза потом называет их вместе с остальными.
    no_source_skip: set[int] = set()
    # Отпустить write-txn на время GPT — иначе SQLite lock / UI 500.
    await session.commit()
    for attempt in range(1, 3):
        try:
            raise_if_cancelled(project.id)
        except StepCancelledError as e:
            logger.info("[#{}] generate_image_prompts: {} — выхожу", project.id, e)
            cancelled = True
            break
        try:
            from app.services import xlsx_step_runners as xsr

            run_kwargs: dict = {}
            if no_source_skip:
                run_kwargs["skip_frame_numbers"] = set(no_source_skip)
            result = await xsr.run_img_pr_xlsx(
                project,
                n_frames=len(need),
                project_id=project.id,
                uuid_map_text=uuid_map,
                **run_kwargs,
            )
            ops = list(result.apply_ops or [])
            if ops:
                from app.services.node_write_contract import filter_ops_for_node

                ops = filter_ops_for_node(ops, node_kind="img_pr")
            if not ops and not result.ops_applied_inline:
                raise RuntimeError("пустой apply-ops после GPT")
            # Один apply в конце (не по батчам) — отдельная короткая сессия.
            if ops and not result.ops_applied_inline:
                await _apply_img_pr_ops_once(project.id, ops)
                logger.info(
                    "[#{}] generate_image_prompts: single apply ops={}",
                    project.id,
                    len(ops),
                )
            else:
                logger.info(
                    "[#{}] generate_image_prompts: ops уже в DB (inline), пропуск повторного apply (ops={})",
                    project.id,
                    len(ops),
                )

            # Apply в другой сессии — подтянуть project/frames без expire_all
            # (expire_all + lazy project.data_dir → MissingGreenlet).
            await session.refresh(project)
            try:
                from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

                await snapshot_and_bind_node_xlsx(session, project, node_type="image_prompts")
            except Exception as snap_err:  # noqa: BLE001
                logger.warning(
                    "[#{}] image_prompts snapshot failed: {}",
                    project.id,
                    snap_err,
                )

            frames = await _reload_frames(session, project.id)
            need = _frames_needing_image_prompt(frames)
            if not need:
                break
            missing = [fr.number for fr in need]
            if no_source_skip and set(missing) <= no_source_skip:
                from app.services.img_pr_batches import ImgPrNoSourceData

                raise ImgPrNoSourceData(sorted(set(missing)))
            raise RuntimeError(
                f"после apply-ops промты не заполнены (попытка {attempt}): "
                f"кадры {missing[:20]}{'…' if len(missing) > 20 else ''} "
                f"(осталось {len(missing)}; чекпоинт img_pr сохранит прогресс)"
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "[#{}] generate_image_prompts attempt {} failed: {}",
                project.id,
                attempt,
                e,
            )
            from app.services.img_pr_batches import ImgPrNoSourceData

            no_source = isinstance(e, ImgPrNoSourceData)
            ns_err = cast(ImgPrNoSourceData, e)
            if no_source and no_source_skip and not no_source_skip <= set(ns_err.frame_numbers):
                # 2-я попытка: свои «нет данных» + пропущенные с 1-й — одна пауза на все.
                ns_err = ImgPrNoSourceData(
                    sorted(set(ns_err.frame_numbers) | no_source_skip), apply_ops=ns_err.apply_ops
                )
                e = ns_err
                last_err = e
            only_no_source = False
            if no_source and ns_err.apply_ops:
                # Готовые промты остальных кадров (прогон + чекпоинт) — в базу до
                # решения о повторе: иначе 2-я попытка видит их пустыми и снова
                # платно спрашивает кадры «нет данных».
                from app.services.node_write_contract import filter_ops_for_node

                good_ops = filter_ops_for_node(list(ns_err.apply_ops), node_kind="img_pr")
                if good_ops:
                    await _apply_img_pr_ops_once(project.id, good_ops)
                    # Промты уже в базе — чекпоинт своё отработал. Иначе кадр, который
                    # человек потом очистил, при повторе получил бы промт из чекпоинта
                    # вместо новой генерации.
                    from app.services import img_pr_batches as ipb

                    ipb.clear_checkpoint(project.data_dir)
                    logger.info(
                        "[#{}] generate_image_prompts: no-source, applied ready ops={}",
                        project.id,
                        len(good_ops),
                    )
            if no_source:
                # 2-ю попытку пропускаем, только если ВСЕ недостающие кадры — «нет
                # данных сцены»; пропущенные моделью кадры переспрашиваются как раньше.
                try:
                    await session.refresh(project)
                    still = _frames_needing_image_prompt(await _reload_frames(session, project.id))
                    only_no_source = {fr.number for fr in still} <= set(ns_err.frame_numbers)
                except Exception as reload_err:  # noqa: BLE001
                    logger.warning(
                        "[#{}] img_pr: reload after no-source failed: {}",
                        project.id,
                        reload_err,
                    )
            if no_source and not only_no_source:
                no_source_skip |= set(ns_err.frame_numbers)
            if attempt >= 2 or only_no_source:
                if no_source:
                    # Свой код ошибки (error_catalog: img_pr_no_scene_data) и
                    # короткий текст для человека — без обёртки с именами шагов.
                    # Слепок входа сцен: по нему следующий запуск не зовёт модель зря.
                    try:
                        ns_err.context["scene_fingerprint"] = await _scene_fingerprint(
                            session,
                            await _reload_frames(session, project.id),
                            set(ns_err.frame_numbers),
                        )
                    except Exception as fp_err:  # noqa: BLE001
                        logger.warning(
                            "[#{}] img_pr: слепок данных сцен не снят: {}",
                            project.id,
                            fp_err,
                        )
                    raise e
                raise RuntimeError(f"generate_image_prompts: не удалось получить промты: {e}") from e

    if cancelled:
        try:
            await session.refresh(project)
        except Exception:  # noqa: BLE001
            pass
        return

    frames = await _reload_frames(session, project.id)
    await _finish_success(session, project, frames)
    _ = last_err
