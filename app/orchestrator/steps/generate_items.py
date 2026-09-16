"""Шаг 4b «Предметы» — генерация реф-картинок предметов.

Параллельная ветвь шага 4 «Объекты»: если шаг 4a «Персонажи» делает
hero_reference, то 4b делает item_reference. Логика проще, чем у Hero:
без HITL, без вариаций (1 картинка на предмет).

Источник списка предметов: `project.item_descriptions: list[str]`, а если он
пуст — сущности `Entity(type="prop"|"item")` того же проекта (их описания
переезжают в `item_descriptions`, чтобы дальше был один источник).
По одному непустому описанию = один сгенерированный предмет.
Файлы кладутся в `data/videos/<slug>/items/predmet<N>_<uuid>.png`,
где N — 1-based индекс предмета.

Если шаг падает на каком-то предмете — статус откатывается на
hero_ready (предметы опциональны), юзер правит описание и жмёт
«Предметы» снова.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.bots.browser import browser_session
from app.services.image_transport import http_image_primary


@asynccontextmanager
async def _optional_browser(need_cdp: bool):
    if not need_cdp:
        yield None
        return
    async with browser_session() as bs:
        yield bs


from app.bots.outsee import (
    OutseeBot,
    OutseeContentRejectedError,
    OutseeImageError,
)
from app.generation_options import (
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
)
from app.models import Artifact, ArtifactKind, Entity, Project, ProjectStatus
from app.services.gpt_client import get_gpt_client
from app.services.img_streams import acquire_image_slot
from app.services.outsee_retry import generate_image_with_retries
from app.services.prompt_library import get_project_prompt
from app.services.step_cancel import raise_if_cancelled

# Aspect ratio и Relax для предметов — как у hero (16:9 + Relax), потому
# что предметы тоже идут как реф-листы.
ITEM_ASPECT_RATIO = "16:9"
ITEM_RELAX = True


async def _existing_item_indices(session: AsyncSession, project: Project) -> set[int]:
    """Какие индексы предметов уже имеют артефакт kind=item_reference."""
    rows = (
        (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.item_reference,
                )
                .order_by(desc(Artifact.id))
            )
        )
        .scalars()
        .all()
    )
    out: set[int] = set()
    for a in rows:
        m = a.meta or {}
        idx = m.get("item_index")
        if isinstance(idx, int):
            out.add(idx)

    # Файл на диске без Artifact (откат БД / сбой сессии) — тоже «готов»,
    # иначе шаг перерисует уже нарисованный предмет.
    items_dir = project.data_dir / "items"
    if items_dir.is_dir():
        for p in items_dir.glob("predmet*.png"):
            try:
                if not p.is_file() or p.stat().st_size <= 1000:
                    continue
            except OSError:
                continue
            num_part = p.stem.split("_")[0][len("predmet") :]
            if num_part.isdigit():
                out.add(int(num_part))
    return out


async def _resolve_item_descriptions(session: AsyncSession, project: Project) -> list[str]:
    """Описания предметов: `project.item_descriptions`, иначе Entity(prop|item).

    Состав предметов заводят сущностями (тот же источник, что и каст), а поле
    проекта заполняют руками. Если руками не заполняли — берём сущности и
    переносим их в поле, чтобы дальше по шагу был один источник.
    """
    raw = list(project.item_descriptions or [])
    descriptions = [d.strip() for d in raw if isinstance(d, str) and d.strip()]
    if descriptions:
        return descriptions

    try:
        ents = (
            (
                await session.execute(
                    select(Entity)
                    .where(
                        Entity.project_id == project.id,
                        Entity.type.in_(["prop", "item"]),
                    )
                    .order_by(Entity.sort_key, Entity.id)
                )
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[#{}] items: Entity fallback failed: {}", project.id, exc)
        return descriptions

    for e in ents:
        attrs = e.attrs or {}
        desc_val = str(attrs.get("description") or attrs.get("описание") or "").strip()
        name_val = (e.name or "").strip()
        text = f"{name_val}: {desc_val}" if name_val and desc_val else (desc_val or name_val)
        if text and text not in descriptions:
            descriptions.append(text)

    if descriptions:
        project.item_descriptions = descriptions
        flag_modified(project, "item_descriptions")
        await session.flush()
        logger.info(
            "[#{}] items: описания взяты из сущностей ({} шт.)",
            project.id,
            len(descriptions),
        )
    return descriptions


def _items_style_prompt(project: Project) -> str:
    """Мастер-промт для генерации предметов (prompts/04b_items/<name>.md).
    Если ничего не выбрано/нет файла — пустая строка (юзер должен
    положить хотя бы default.md)."""
    try:
        return get_project_prompt(project, "items").strip()
    except FileNotFoundError:
        logger.warning(
            "[#{}] items: стиля предметов нет ни в базе, ни на диске — генерирую из описаний без стиля",
            project.id,
        )
        return ""


async def run(session: AsyncSession, project: Project, bot: Any = None) -> None:
    if project.status is not ProjectStatus.generating_items:
        return

    descriptions = await _resolve_item_descriptions(session, project)
    if not descriptions:
        # Метка обязательна, иначе получается вечный цикл — ровно тот, что уже
        # чинили для героя (см. `hero_skipped_empty` в generate_hero).
        #
        # Без неё стороны читают одно и то же поле и расходятся во мнении:
        # шаг видит пустой `item_descriptions` и считает работу сделанной, а
        # `_items_step_required` из того же пустого списка заключает, что шага
        # нет вовсе — и `compute_actual_status` НИКОГДА не возвращает
        # `items_ready`, только `hero_ready`. Дальше страж откатывает статус,
        # авто-продвижение снова одобряет `hero_ready` → `generating_items`, и
        # так каждые пять секунд без конца.
        #
        # Отказом это не считается: формально ничего не падает, счётчик
        # `step_failure_policy` не растёт, паузы не наступает. Поймано живым
        # прогоном 2026-08-25 — проект крутился семь минут, пока не посмотрели
        # в журнал.
        meta = dict(project.meta or {})
        meta["items_skipped_empty"] = True
        project.meta = meta
        logger.info(
            "[#{}] items: item_descriptions пуст — items_ready без работы (items_skipped_empty)",
            project.id,
        )
        project.status = ProjectStatus.items_ready
        await session.flush()
        return

    style = _items_style_prompt(project)
    from app.services.vibecode_catalog import resolve_node_media_settings

    media = resolve_node_media_settings(project, node_type="items")
    img_gid = media["image_generator_id"]
    img_gen = IMAGE_GENERATORS_BY_ID.get(img_gid)
    ir = IMAGE_RESOLUTIONS_BY_ID.get(media["resolution_id"])
    quality_slug = media["quality_slug"]
    # items historically force square; нода может задать своё соотношение.
    item_aspect = media["aspect_slug"] or ITEM_ASPECT_RATIO

    already_done = await _existing_item_indices(session, project)
    out_dir = project.data_dir / "items"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Идём по предметам последовательно, пропускаем уже сгенерированные.
    for idx, desc_text in enumerate(descriptions, start=1):
        raise_if_cancelled(project.id)
        if idx in already_done:
            logger.info(
                "[#{}] items: predmet{} уже есть, пропускаю",
                project.id,
                idx,
            )
            continue
        logger.info(
            "[#{}] items: предмет {}/{} — '{}'",
            project.id,
            idx,
            len(descriptions),
            desc_text[:60],
        )

        full_prompt = (
            style + "\n\n---\n\n" if style else ""
        ) + f"Описание предмета (predmet{idx}): {desc_text}"

        short_uuid = uuid.uuid4().hex[:8]
        file_name = f"predmet{idx}_{short_uuid}.png"
        out_path = out_dir / file_name
        prompt_id_prefix = f"[ID: P{project.id}-ITEM{idx}-{short_uuid}]"

        try:
            # Chrome — только если нет HTTP-провайдера (см. image_transport).
            async with _optional_browser(need_cdp=not http_image_primary()) as bs:
                outsee = OutseeBot(bs) if bs is not None else None
                gpt = get_gpt_client()
                # Слот общего пула провайдера: предметы идут наравне с
                # кадрами и героем, иначе шаг обходит лимит параллели.
                async with acquire_image_slot():
                    result = await generate_image_with_retries(
                        outsee,
                        gpt,
                        prompt=full_prompt,
                        out_path=out_path,
                        max_attempts_per_prompt=3,
                        gpt_rewrite=True,
                        aspect_ratio=item_aspect,
                        model_slug=img_gen.outsee_slug if img_gen else None,
                        resolution=ir.outsee_slug if ir else None,
                        quality=quality_slug,
                        relax=ITEM_RELAX,
                        prompt_id_prefix=prompt_id_prefix,
                        reference_image=None,
                        timeout=600,
                        project_id=project.id,
                    )
        except OutseeImageError as e:
            is_moderation = isinstance(e, OutseeContentRejectedError)
            logger.error(
                "[#{}] items: predmet{} 6 попыток провалились (moderation={}): {}",
                project.id,
                idx,
                is_moderation,
                getattr(e, "reason", None) or str(e),
            )
            # Откат на hero_ready: предметы опциональны, юзер может
            # пропустить и идти дальше.
            project.status = ProjectStatus.hero_ready
            await session.flush()
            raise RuntimeError(
                f"items: predmet{idx} не удалось сгенерить "
                f"(см. логи). Статус откатил на hero_ready — поправь "
                f"описание предмета и жми «Предметы» снова."
            ) from e

        # Сохраняем артефакт.
        a = Artifact(
            project_id=project.id,
            frame_id=None,
            kind=ArtifactKind.item_reference,
            uuid=uuid.uuid4().hex,
            path=str(result.file_path),
            meta={
                "item_index": idx,
                "item_id": f"predmet{idx}",
                "description": desc_text,
                "prompt": full_prompt,
            },
        )
        session.add(a)
        await session.flush()
        logger.info(
            "[#{}] items: predmet{} → {}",
            project.id,
            idx,
            result.file_path,
        )

    # Все предметы готовы.
    project.status = ProjectStatus.items_ready
    await session.flush()
    logger.info(
        "[#{}] items: все {} предметов готовы → items_ready",
        project.id,
        len(descriptions),
    )
