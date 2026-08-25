"""Главный pipeline в ручном режиме (управляется из Telegram-меню).

Никаких авто-переходов между шагами. Воркер видит только «running»-статусы и
запускает соответствующий шаг. После шага статус становится «*_ready», и
проект ждёт действия пользователя из бота. Все «ready»-статусы воркером
пропускаются.

Маппинг running-status → step.run:
  planning                       → make_plan
  scripting                      → make_script
  splitting                      → split_frames
  scene_designing                → scene_design.run (5 агентов параллельно)
  scene_assembling               → scene_design.run_assemble (сборщик)
  generating_hero                → generate_hero
  generating_image_prompts       → generate_image_prompts (только промты)
  generating_images              → generate_images        (только картинки)
  generating_animation_prompts   → make_animation_prompts
  generating_videos              → generate_videos
  generating_audio               → generate_audio
  assembling                     → assemble
  publishing                     → publish

Переходы между шагами инициирует пользователь, тыкая кнопки в бот-меню.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.steps import (
    assemble,
    enrich_xlsx,
    generate_audio,
    generate_hero,
    generate_image_prompts,
    generate_images,
    generate_items,
    generate_videos,
    make_animation_prompts,
    make_plan,
    make_script,
    publish,
    split_frames,
)


async def _sync_storage_after_advance(
    session: AsyncSession, project: Project, running_status: ProjectStatus
) -> None:
    """После любого шага — забрать артефакты во все storage по стрелкам.

    plan/script/split/enrich уже синкают сами; повтор идемпотентен (fingerprint).
    Без этого hero/img/video/anim_pr/audio «успешно» бежали, а хранилище пустое.
    """
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.services.excel_gpt_node import slot_from_running_status
    from app.services.storage_step_sync import sync_storage_after_step

    # excel_gpt: sync внутри enrich_xlsx по конкретному node_key
    if slot_from_running_status(running_status) is not None:
        return
    node_type = RUNNING_TO_NODE_TYPE.get(running_status)
    if not node_type:
        return
    try:
        await sync_storage_after_step(
            session,
            project,
            node_type,
            log_prefix=f"advance/{node_type}",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "advance #{}: storage sync after {} failed",
            project.id,
            node_type,
            exc_info=True,
        )


async def advance_project(session: AsyncSession, project: Project, bot: Bot) -> None:
    """Один такт стейт-машины. Запускает шаг, если статус — «running»; иначе
    ничего не делает (ждём, пока пользователь нажмёт кнопку в боте)."""
    import asyncio

    from app.services.step_cancel import (
        abort_if_cancelled,
        register_advance_task,
        unregister_advance_task,
    )

    # Номер проекта — в локальную переменную ДО работы. После `rollback()` в
    # `finally` ORM-объект протухает, и `project.id` идёт в базу синхронно —
    # вне greenlet: «greenlet_spawn has not been called». На живом сервере
    # это заменило собой настоящую ошибку шага и сорвало освобождение lease
    # — проект простоял час. В тестах объект был свежесозданным и не
    # протухал, поэтому они молчали.
    pid = project.id
    task = asyncio.current_task()
    if task is not None:
        register_advance_task(pid, task)
    ran_status: ProjectStatus | None = None
    _step_lock_cm = None
    _step_lease: tuple[str, str] | None = None
    _lease_renewer: asyncio.Task | None = None
    _step_failed = False
    try:
        abort_if_cancelled(project.id)
        status = project.status
        ran_status = status
        logger.debug("advance #{} status={}", project.id, status.value)

        from app.services.llm_override import bind_project_llm

        # Параллельные проекты + SQLite: split — строго по одному (иначе db is locked).
        from app.services.step_global_lock import (
            acquire_step_lock,
            step_code_from_status,
        )

        _step_lock_cm = acquire_step_lock(step_code_from_status(status))
        await _step_lock_cm.__aenter__()

        # Этап 2 (D.2a): step-level lease — БД-видимый признак «шаг живой»
        # для ВСЕХ шагов (критерий осиротевшести реконсайлеров/startup_guard).
        # Занят живым lease (другой процесс) → пропуск такта, не второй запуск.
        from app.orchestrator.node_registry import (
            NODE_TYPE_TO_STEP_CODE,
            RUNNING_TO_NODE_TYPE,
        )
        from app.services import work_lease as _wl

        _step_code = NODE_TYPE_TO_STEP_CODE.get(RUNNING_TO_NODE_TYPE.get(status, ""), "")
        if _step_code:
            _lease_key = f"step:{_step_code}"
            _lease_me = _wl.current_owner()
            if not await _wl.acquire(project.id, _lease_key, owner=_lease_me, ttl_s=3600):
                logger.info(
                    "[#{}] advance: {} занят живым step-lease (другой процесс) — пропуск такта",
                    project.id,
                    status.value,
                )
                return
            _step_lease = (_lease_key, _lease_me)

            async def _renew_loop() -> None:
                while True:
                    await asyncio.sleep(600)
                    if not await _wl.renew(project.id, _lease_key, owner=_lease_me, ttl_s=3600):
                        logger.warning(
                            "[#{}] advance: step-lease {} потерян",
                            project.id,
                            _lease_key,
                        )
                        return

            _lease_renewer = asyncio.create_task(_renew_loop())

        # UI SSoT: NodeRun → running, иначе на ноде нет «в работе».
        try:
            from app.orchestrator.auto_advance import _prepare_node_run_for_status

            await _prepare_node_run_for_status(session, project, status, allow_restart=True)
        except Exception:  # noqa: BLE001
            logger.debug(
                "advance #{}: prepare NodeRun for {} failed",
                project.id,
                status.value,
                exc_info=True,
            )

        with bind_project_llm(project, status):
            if status is ProjectStatus.planning:
                await make_plan.run(session, project, bot)
            elif status is ProjectStatus.scripting:
                await make_script.run(session, project, bot)
            elif status is ProjectStatus.splitting:
                await split_frames.run(session, project)
            elif status is ProjectStatus.scene_designing:
                from app.orchestrator.steps import scene_design

                await scene_design.run(session, project, bot)
            elif status is ProjectStatus.scene_assembling:
                from app.orchestrator.steps import scene_design

                await scene_design.run_assemble(session, project, bot)
            elif status is ProjectStatus.generating_hero:
                await generate_hero.run(session, project, bot)
            elif status is ProjectStatus.generating_items:
                await generate_items.run(session, project, bot)
            elif status in (
                ProjectStatus.enriching_1,
                ProjectStatus.enriching_2,
                ProjectStatus.enriching_3,
                ProjectStatus.enriching_4,
                ProjectStatus.enriching_5,
            ):
                await enrich_xlsx.run(session, project, bot)
            elif status is ProjectStatus.generating_image_prompts:
                await generate_image_prompts.run(session, project, bot)
            elif status is ProjectStatus.generating_images:
                await generate_images.run(session, project, bot)
            elif status is ProjectStatus.generating_animation_prompts:
                await make_animation_prompts.run(session, project, bot)
            elif status is ProjectStatus.generating_videos:
                await generate_videos.run(session, project, bot)
            elif status is ProjectStatus.generating_music:
                from app.orchestrator.steps import generate_music

                await generate_music.run(session, project, bot)
            elif status is ProjectStatus.sfx_planning:
                from app.orchestrator.steps import plan_sfx

                await plan_sfx.run(session, project, bot)
            elif status is ProjectStatus.generating_sfx:
                from app.orchestrator.steps import generate_sfx

                await generate_sfx.run(session, project, bot)
            elif status is ProjectStatus.generating_audio:
                await generate_audio.run(session, project, bot)
            elif status is ProjectStatus.assembling:
                await assemble.run(session, project, bot)
            elif status is ProjectStatus.publishing:
                await publish.run(session, project, bot)
            else:
                ran_status = None

        if ran_status is not None:
            await _sync_storage_after_advance(session, project, ran_status)
    except BaseException:
        # Не для обработки — только чтобы finally знал, каким путём мы уходим:
        # от этого зависит, можно ли освобождать lease сессией вызывающего.
        _step_failed = True
        raise
    finally:
        if _lease_renewer is not None:
            # Ревью [3/4]: без await renew в полёте доигрывал после release
            # и писал ложный «lease потерян» WARNING.
            _lease_renewer.cancel()
            try:
                await _lease_renewer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if _step_lease is not None:
            try:
                from app.services import work_lease as _wl_fin

                if _step_failed:
                    # Шаг упал — сессия вызывающего для release непригодна, и
                    # молчаливая попытка стоила часа простоя на живом сервере.
                    #
                    # Два независимых механизма съедали удаление. На Postgres
                    # ошибка шага переводит транзакцию в aborted, и следующий
                    # DELETE в ней не выполняется вообще. А если бы и
                    # выполнился — исключение сейчас улетит в `session_scope`
                    # воркера, где `except: await session.rollback()`, и
                    # удаление откатится вместе с шагом. То есть на упавшем
                    # шаге lease не освобождался НИКОГДА, ни на одной СУБД.
                    #
                    # Дальше он висит весь TTL (час), и каждый следующий такт
                    # пишет «занят живым step-lease (другой процесс)» — при
                    # том, что процесс тот же самый. Снаружи это выглядит как
                    # намертво вставший проект без единой ошибки в журнале.
                    #
                    # Откатываем сами: транзакция всё равно обречена, а после
                    # отката своя короткая сессия уже не упрётся в writer-lock
                    # SQLite — ровно та причина, по которой release изначально
                    # ходил через сессию вызывающего.
                    try:
                        await session.rollback()
                    except Exception:  # noqa: BLE001
                        logger.debug("[#{}] rollback перед release не удался", pid)
                    await _wl_fin.release(pid, _step_lease[0], owner=_step_lease[1])
                else:
                    # Успешный путь: вызывающий сейчас коммитит, и удаление
                    # уедет вместе с его транзакцией. Своя короткая сессия
                    # встала бы на busy_timeout в ожидании этой же транзакции.
                    await _wl_fin.release(pid, _step_lease[0], owner=_step_lease[1], session=session)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[#{}] не удалось освободить step-lease {} — до конца TTL шаг будет "
                    "пропускаться как занятый",
                    pid,
                    _step_lease[0],
                )
        if _step_lock_cm is not None:
            try:
                await _step_lock_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        unregister_advance_task(pid)
