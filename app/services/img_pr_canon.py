"""Второй герой и фон держатся кодом, а не обещанием модели.

Главный герой консистентен потому, что у него есть фотография: `image-01`
берёт её как `subject_reference`, и лицо не уезжает. Второй герой такой
фотографии не получит — провайдер принимает РОВНО ОДНУ character-ссылку
(`app/services/frame_cast.py`, разбор кадров 12/13/17 живого прогона #2).
Значит его сходство может держать только текст.

Текст сегодня пишет модель: поле `describe_appearance` перечисляет, КОГО
описать, а сами 100–150 символов агент сочиняет заново в каждом батче. Отсюда
дрейф: c02 в кадрах 1–8 и c02 в кадрах 17–24 описаны разными словами, потому
что это разные вызовы модели, между которыми она ничего не помнит. Ровно та
же болезнь, что была у расстановки, и лечится она тем же способом, которым
вылечена расстановка (`scene_design/continuity.py`): **то же самое считается
кодом, а в промт уезжает готовая фраза, а не правило.**

С фоном история короче и та же. `shot01_bg` — канон сцены, он один на все её
кадры, и мастер-промт честно велит «фон подробно из shot01_bg, священно». Это
требование к модели; проверять его было некому.

**Что делает этот модуль.** После применения ответа агента:

* внешность каждого героя, которому не досталась фотография, приводится к
  канону — одной строке, собранной из паспорта персонажа один раз и
  вставленной во все кадры дословно;
* строка «Фон:» приводится к канону сцены.

**Почему это не досочинение за модель.** Досочинение — это когда код
выдумывает то, чего в данных нет. Здесь и внешность, и фон уже лежат в базе:
паспорт персонажа заполнен на шаге «Герои», фон — на разборе сцен. Модель
обязана была их скопировать; код копирует за неё то же самое.

**Про лимит.** У MiniMax промт картинки режется по 1500 символов, и хвост
уходит первым (`img_pr_budget`). Поэтому канон вставляется ВЫСОКО — сразу
после фона, до сюжетной части, — и сам ужимается до `_APPEARANCE_BUDGET`
символов на человека. Личность важнее эмоции: кадр с неверным лицом
переснимают, кадр с бледной эмоцией — нет.
"""

from __future__ import annotations

import re
from typing import Any

#: Сколько символов отводится на канон внешности одного человека. Столько же
#: просит у модели мастер-промт (100–150), то есть бюджет не меняется — просто
#: текст перестаёт быть каждый раз новым.
_APPEARANCE_BUDGET = 150

#: Метка строки внешности. Отдельная и узнаваемая, чтобы канон можно было
#: найти и заменить, а не приписать второй раз при повторном прогоне.
_APPEARANCE_LABEL = "Внешность"

_RE_BG = re.compile(r"^\s*(фон|background)\s*[:—-]\s*(.*)$", re.IGNORECASE)
_RE_APPEARANCE = re.compile(rf"^\s*{_APPEARANCE_LABEL}\s+(c\d{{1,3}})\s*[:—-]", re.IGNORECASE)
#: Куда вставлять канон, если фона в промте нет вовсе: перед сюжетом.
_RE_STORY = re.compile(
    r"^\s*(действие|план-ракурс|план\s|ракурс|свет|эмоция|акцент|смысл|детали|action|style)\s*[:—-]?",
    re.IGNORECASE,
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def appearance_line(card: Any, *, budget: int = _APPEARANCE_BUDGET) -> str:
    """Канон внешности одного персонажа. Пусто — паспорт не заполнен.

    Берутся внешность и одежда, и только они: характер и правила героя
    описывают поведение, а генератору картинки нужно, как он выглядит.
    Обрезка идёт по границе слова — обрубленное на полуслове описание модель
    достраивает сама, и достраивает каждый раз иначе.
    """
    cid = _norm(getattr(card, "id", "") or "")
    if not cid:
        return ""
    parts = [_norm(getattr(card, "look", "")), _norm(getattr(card, "clothes", ""))]
    body = ", ".join(p.rstrip(".,; ") for p in parts if p)
    if not body:
        return ""
    if len(body) > budget:
        cut = body[:budget]
        space = cut.rfind(" ")
        body = (cut[:space] if space > budget // 2 else cut).rstrip(".,; ")
    return f"{_APPEARANCE_LABEL} {cid}: {body}"


def canon_lines_present(prompt: str | None) -> set[str]:
    """Чьи каноны внешности в промте уже есть."""
    found: set[str] = set()
    for line in str(prompt or "").splitlines():
        m = _RE_APPEARANCE.match(line)
        if m:
            found.add(m.group(1).lower())
    return found


def ensure_background_line(prompt: str | None, background: str | None) -> str:
    """Привести «Фон:» к канону сцены. Пустой канон — не трогаем ничего.

    Существующая строка ЗАМЕНЯЕТСЯ, а не дополняется: две строки фона хуже
    одной неверной — модель складывает их в третий фон, которого нет нигде.
    """
    canon = _norm(background)
    text = str(prompt or "")
    if not canon or not text.strip():
        return text

    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _RE_BG.match(line)
        if m:
            if _norm(m.group(2)) == canon:
                return text
            lines[i] = f"Фон: {canon}"
            return "\n".join(lines)
    # Фона в промте нет вовсе — ставим перед сюжетной частью.
    at = _story_index(lines)
    lines.insert(at, f"Фон: {canon}")
    return "\n".join(lines)


def ensure_appearance_lines(prompt: str | None, lines_by_cid: dict[str, str]) -> str:
    """Вставить недостающие каноны внешности. Существующие не трогаем.

    Не трогаем намеренно: если канон уже стоит, повторный прогон не должен
    двигать текст — иначе кадр пересчитывается «изменившимся» и
    перерисовывается за деньги клиента.
    """
    text = str(prompt or "")
    if not lines_by_cid or not text.strip():
        return text
    have = canon_lines_present(text)
    missing = [line for cid, line in lines_by_cid.items() if cid.lower() not in have and line]
    if not missing:
        return text

    lines = text.splitlines()
    at = _after_background_index(lines)
    for offset, line in enumerate(missing):
        lines.insert(at + offset, line)
    return "\n".join(lines)


def _after_background_index(lines: list[str]) -> int:
    """Сразу после фона; нет фона — перед сюжетом; нет и его — в конец."""
    for i, line in enumerate(lines):
        if _RE_BG.match(line):
            return i + 1
    return _story_index(lines)


def _story_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if _RE_STORY.match(line):
            return i
    return len(lines)


async def enforce_canon_in_prompts(session: Any, project: Any) -> dict[str, int]:
    """Привести промты кадров к канону внешности и фона.

    Возвращает счётчики для журнала и тестов. Зовётся после применения
    ответа агента `img_pr` — рядом с переносом расстановки, потому что это
    одна и та же работа: перенести в промт то, что модель обязана была
    перенести сама.
    """
    from loguru import logger
    from sqlalchemy import select

    from app.models import Entity, Frame
    from app.services.excel_characters import characters_from_entities
    from app.services.frame_cast import characters_needing_description

    entities = (await session.execute(select(Entity).where(Entity.project_id == project.id))).scalars().all()
    cards = {c.id.lower(): c for c in characters_from_entities(list(entities))}
    canon_by_cid = {cid: appearance_line(card) for cid, card in cards.items()}

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)))
        .scalars()
        .all()
    )

    stats = {"frames": len(frames), "appearance": 0, "background": 0, "no_card": 0}
    missing_cards: set[str] = set()
    for fr in frames:
        prompt = str(fr.image_prompt or "")
        if not prompt.strip():
            continue
        attrs = fr.attrs if isinstance(fr.attrs, dict) else {}

        need = characters_needing_description(attrs.get("characters") or attrs.get("персонажи"))
        wanted: dict[str, str] = {}
        for cid in need:
            line = canon_by_cid.get(cid.lower(), "")
            if line:
                wanted[cid.lower()] = line
            else:
                missing_cards.add(cid)
        after_appearance = ensure_appearance_lines(prompt, wanted)
        if after_appearance != prompt:
            stats["appearance"] += 1

        background = attrs.get("shot01_bg") or attrs.get("фон")
        after_bg = ensure_background_line(after_appearance, background)
        if after_bg != after_appearance:
            stats["background"] += 1

        if after_bg != prompt:
            fr.image_prompt = after_bg

    if missing_cards:
        # Паспорт пуст — канона нет, и вставить нечего. Это не сбой шага, но и
        # не мелочь: у этого героя сходство не держит ничто.
        stats["no_card"] = len(missing_cards)
        logger.warning(
            "[#{}] img_pr: у героев {} пустой паспорт — канон внешности не из чего "
            "собрать, сходство держится только тем, что напишет модель",
            project.id,
            ", ".join(sorted(missing_cards)),
        )
    if stats["appearance"] or stats["background"]:
        await session.flush()
        logger.info(
            "[#{}] img_pr: канон проставлен кодом — внешность у {} кадров, фон у {}",
            project.id,
            stats["appearance"],
            stats["background"],
        )
    return stats
