"""Каст: рисуем и судим по одному тексту, а не по двум.

**Что было.** Описания персонажей жили в двух местах и расходились:

* `project.hero_descriptions` — по ним шаг «Персонажи» рисует листы;
* `entities` (type=character), которые пишет сцен-агент `characters` — по ним
  vision-проверка строит блок «## База (source of truth)»
  (`vision_check_db.load_character_rows`).

Их никто не сводил. Живой прогон 2026-08-31: все пять листов получили
`verdict: fail` не за качество, а за несовпадение с параллельной правдой —
сцен-агент выдумал свой каст (лесные существа с серо-бурым ворсом) вместо
заданной леденцовой палитры. После выравнивания реестра **те же самые файлы**
дали `pass` без единой перерисовки: шесть генераций едва не сгорели на
рассинхронизации. Хуже того, петля готовила `[VISION_FIX]` с
`STYLE LOCK ... no switching between photo-realism` — то есть автопочинка
перерисовала бы фотореалистичного героя в мультстиль и уничтожила замысел.

**Что теперь.** Реестр `entities` — источник, если он не пуст: и рисование, и
приёмка читают один текст. `hero_descriptions` остаётся входом человека и
работает, пока реестра нет (новый проект, ручной каст, `no_hero`).

**Чего это НЕ делает.** Не решает вопрос «кто имеет право писать реестр» —
решение A из `ORCHESTRATOR-V2.md` §3.8 (узел `cast` единственный писатель,
агент только предлагает через diff→apply) остаётся впереди. Здесь снят
конкретный ущерб: рисуем и судим по одному тексту.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project


def describe_character_row(row: dict[str, Any]) -> str:
    """Карточка реестра → человекочитаемое описание для генератора листа."""
    parts = [
        str(row.get("look") or "").strip(),
        str(row.get("clothes") or "").strip(),
        str(row.get("rules") or "").strip(),
    ]
    return " ".join(p for p in parts if p).strip()


async def cast_descriptions(session: AsyncSession, project: Project) -> tuple[list[str], str]:
    """Описания персонажей и источник: ``registry`` либо ``project``.

    Порядок соответствует кодам ``c01…cNN``: первый элемент — ``c01``. Это
    важно, потому что шаг «Персонажи» нумерует пары по позиции в списке, а
    ``frame_cast`` раздаёт фотографию по коду — разъехаться им нельзя.
    """
    from app.services.vision_check_db import load_character_rows

    try:
        rows = await load_character_rows(session, project)
    except Exception:  # noqa: BLE001 — реестр не повод не нарисовать героев
        logger.exception("[#{}] реестр персонажей не прочитан", project.id)
        rows = []

    described = [(str(r.get("id") or ""), describe_character_row(r)) for r in rows]
    described = [(code, text) for code, text in described if code and text]
    if not described:
        return list(project.hero_descriptions or []), "project"

    described.sort(key=lambda pair: pair[0])
    out = [text for _code, text in described]

    own = [str(d or "").strip() for d in (project.hero_descriptions or [])]
    if own and out != own:
        logger.info(
            "[#{}] каст берётся из реестра ({} записей), а не из hero_descriptions "
            "({}): рисовать и проверять нужно по одному тексту",
            project.id,
            len(out),
            len(own),
        )
    return out, "registry"
