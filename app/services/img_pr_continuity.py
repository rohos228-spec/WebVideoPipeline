"""Расстановка кадра доезжает до промта картинки — гарантированно.

``scene_design/continuity`` считает кодом, где стоит каждый герой, куда он
смотрит и у кого предмет, и кладёт готовую фразу в ``Frame.attrs["continuity"]``.
Дальше эту фразу должен переписать в промт агент ``img_pr``. На живом прогоне
#2 он переписал её один раз из двадцати четырёх: поле приехало в контекст, но
в списке полей тела промта его не было, и модель молча прошла мимо.

Контракт починен (мастер-промт, footer батча, хинт шага), но контракт —
просьба. Здесь то же требование стоит проверкой: после apply промт кадра либо
уже содержит расстановку, либо она в него вставляется — строкой сразу после
«Фон:», до «Действие:». Это не досочинение за модель: текст берётся из того же
поля, что модель обязана была скопировать, дословно.

Порядок важен: у MiniMax промт картинки режется по 1500 символов, и хвост
уходит первым. Геометрия обязана стоять выше сюжетной части.
"""

from __future__ import annotations

import re
from typing import Any

# Метки полей тела промта (грамматика сцены из prompts/05_image_prompts).
_RE_BG = re.compile(r"^\s*(фон|background)\s*[:—-]", re.IGNORECASE)
_RE_PLACE = re.compile(r"^\s*(место|локация|setting)\s*[:—-]", re.IGNORECASE)
_RE_REF = re.compile(r"^\s*(референс|reference)\s*[:—-]", re.IGNORECASE)
_RE_AFTER_BG = re.compile(
    r"^\s*(действие|план-ракурс|план\s|ракурс|свет|эмоция|акцент|смысл|детали|action|style)\s*[:—-]?",
    re.IGNORECASE,
)


def _norm(text: str | None) -> str:
    """Сравнение по смыслу строки, а не по переносам: модель их переставляет."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def has_continuity_line(prompt: str | None, continuity: str | None) -> bool:
    """Расстановка уже в промте (пусть и с другими переносами строк)."""
    line = _norm(continuity)
    if not line:
        return True
    return line in _norm(prompt)


def _insert_at(lines: list[str]) -> int:
    """Куда вставить расстановку: сразу после «Фон:», до «Действие:»."""
    for i, ln in enumerate(lines):
        if _RE_BG.match(ln):
            # Значение фона может занять несколько строк — идём до следующей метки.
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _RE_AFTER_BG.match(lines[j]):
                j += 1
            return j
    for i, ln in enumerate(lines):
        if _RE_AFTER_BG.match(ln):
            return i
    for i, ln in enumerate(lines):
        if _RE_PLACE.match(ln) or _RE_REF.match(ln):
            return i + 1
    return 0


def ensure_continuity_line(prompt: str | None, continuity: str | None) -> str:
    """Промт с расстановкой. Уже есть — возвращается как был, байт в байт."""
    body = str(prompt or "")
    line = str(continuity or "").strip()
    if not line or has_continuity_line(body, line):
        return body
    if not body.strip():
        return line
    lines = body.split("\n")
    at = _insert_at(lines)
    lines.insert(at, line)
    return "\n".join(lines)


def _missing_appearances(prompt: str, required: list[str]) -> list[str]:
    """Кого из обязательных к описанию промт не назвал вовсе."""
    head = str(prompt or "")
    return [cid for cid in required if cid not in head]


async def enforce_continuity_in_prompts(session: Any, project: Any) -> dict[str, int]:
    """Дописать расстановку тем кадрам, где агент её не перенёс.

    Возвращает счётчики для лога и тестов: сколько кадров имеют расстановку,
    у скольких она уже была от модели, скольким её вставил код.
    """
    from loguru import logger
    from sqlalchemy import select

    from app.models import Frame

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)))
        .scalars()
        .all()
    )
    from app.services.frame_cast import characters_needing_description

    stats = {"frames": len(frames), "with_continuity": 0, "from_model": 0, "repaired": 0}
    faceless: list[str] = []
    for fr in frames:
        attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
        prompt = str(fr.image_prompt or "")
        # Фотография персонажа у провайдера одна на кадр. В кадре с двумя
        # людьми внешность обоих обязана быть словами, иначе второй выходит
        # с лицом первого. Досочинить за модель нельзя — описание берётся из
        # паспорта персонажа и не влезет в лимит, — но молчать тоже нельзя.
        need = characters_needing_description(attrs.get("characters") or attrs.get("персонажи"))
        missed = _missing_appearances(prompt, need) if prompt.strip() else []
        if missed:
            faceless.append(f"кадр {fr.number}: {', '.join(missed)}")
        line = str(attrs.get("continuity") or "").strip()
        if not line or not prompt.strip():
            continue
        stats["with_continuity"] += 1
        if has_continuity_line(prompt, line):
            stats["from_model"] += 1
            continue
        fr.image_prompt = ensure_continuity_line(prompt, line)
        stats["repaired"] += 1
    if faceless:
        logger.warning(
            "[#{}] img_pr: в кадрах с двумя людьми внешность названа не для всех "
            "({}) — эти герои выйдут с лицом соседа",
            project.id,
            "; ".join(faceless[:8]),
        )
    if stats["repaired"]:
        await session.flush()
        logger.warning(
            "[#{}] img_pr: расстановку перенёс код у {} кадров из {} — агент "
            "проигнорировал поле continuity (модель перенесла {})",
            project.id,
            stats["repaired"],
            stats["with_continuity"],
            stats["from_model"],
        )
    elif stats["with_continuity"]:
        logger.info(
            "[#{}] img_pr: расстановка в промтах {}/{} — вся от модели",
            project.id,
            stats["from_model"],
            stats["with_continuity"],
        )
    return stats
