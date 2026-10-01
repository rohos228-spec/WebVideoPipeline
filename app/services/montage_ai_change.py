"""ИИзменение в монтаже: LLM пишет промт по агенту img_pr.

В модель: файл агента (master) + закадр. Старый промт кадра не отправляем.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from loguru import logger

from app.services.gpt_client import get_gpt_client
from app.services.prompt_library import read_resolved_project_prompt

AiChangeKind = Literal["image", "video"]

_SYSTEM_IMAGE = """\
Ты — агент из вложенного файла. Пиши промт картинки по его правилам.
Один кадр, не батч. Старого промта кадра нет — пиши с нуля по агенту и закадру.

Первый абзац — персонаж: Image N / cNN, где стоит, что делает.
Референс = только identity (лицо/тело/одежда). Не копируй сетку листа
и не клонируй людей с приложенной картинки, если промт описывает другой каст.

STYLE / Final style lock / Negative — максимум 6 коротких строк.
Не копируй словарь стиля / §5–§6 из агента.

Категорически запрещено писать пояснения, рассуждения, выводы, отказы или примечания.
Верни ОДИН полный промт. Не JSON apply-ops. Не копируй закадр.
Ответ: только текст промта, без пояснений.
"""

_SYSTEM_VIDEO = """\
Ты — агент анимации и видеопромптов из вложенного файла. Пиши видеопромт по его правилам.
Один кадр, не батч. Старого промта нет — пиши с нуля по правилам агента и закадру.
Без музыки, silent video only: no speech, no dialogue, no narration, no music. Mute. Visual motion only.
Категорически запрещено писать пояснения, рассуждения, выводы, отказы или примечания.
Ответ: только текст видеопромта, без пояснений.
"""


def build_ai_change_user_message(
    *,
    voiceover_text: str,
    instruction: str = "",
    action: str = "",
) -> str:
    vo = (voiceover_text or "").strip() or "(пусто)"
    note = (instruction or "").strip()
    act = (action or "").strip()
    parts = [f"VOICEOVER:\n{vo}\n"]
    if act:
        parts.append(f"\nACTION:\n{act}\n\nЭто действие ЭТОГО кадра, не цепи всей сцены.\n")
    if note:
        parts.append(
            f"\nOPERATOR_CHANGE:\n{note}\n\n"
            "Это то, что оператор написал для промта. "
            "Если там новое действие или правка действия — изобрази именно его: "
            "OPERATOR_CHANGE важнее ACTION, если они расходятся. "
            "Не копируй заметку дословно — переведи в визуальный промт по правилам агента."
        )
    parts.append(
        "\nКарточка кадра — во вложенном db_frames.json (База: место, действие, "
        "персонажи, камера, свет). Старого промта нет.\n"
        "Напиши полный промт по вложенному агенту. "
        "Блок персонажа — первым. "
        "STYLE / Final style lock / Negative — коротко, не словарь из агента. "
        "Не JSON. Только промт."
    )
    return "".join(parts)


def write_ai_change_db_card(
    project: object,
    frame: object,
    dest_dir: Path,
    *,
    shot: int = 1,
    characters: list | None = None,
) -> Path:
    """Один кадр из Базы — тот же снимок, что img_pr кладёт в db_frames.json."""
    from app.services.db_frames_context import build_img_pr_db_context

    dest_dir.mkdir(parents=True, exist_ok=True)
    ctx = build_img_pr_db_context(
        project_id=int(getattr(project, "id", 0) or 0),
        slug=str(getattr(project, "slug", "") or ""),
        frames=[frame],
        characters=list(characters or []),
        general_plan=str(getattr(project, "general_plan", "") or ""),
        include_characters=True,
        include_field_map=True,
    )
    _pin_ai_change_shot_action(ctx, frame, shot=shot)
    path = dest_dir / "db_frames.json"
    path.write_text(
        json.dumps(ctx, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(
        "montage_ai_change: db card {} bytes frame={} shot={} chars={}",
        path.stat().st_size,
        getattr(frame, "number", "?"),
        shot,
        len(ctx.get("characters") or []),
    )
    return path


def _pin_ai_change_shot_action(ctx: dict, frame: object, shot: int = 1) -> None:
    """В карточке ИИзменения — действие этого шота, без цепи сцены и чужих K."""
    from app.services.montage_board import _action_for_frame
    from app.services.vo_shot_expand import _cs, coverage_shot_id

    action = _action_for_frame(frame, shot=shot)
    sid = str(_cs(frame).get("shot_id") or "").strip() or coverage_shot_id(frame)
    action_key = "shot02_action" if shot == 2 else "shot01_action"
    for row in ctx.get("frames") or []:
        if not isinstance(row, dict):
            continue
        if action:
            row[action_key] = action
            row["действие"] = action
        row.pop("main_action", None)
        row.pop("главное_действие", None)
        kadry = row.get("кадры")
        if not isinstance(kadry, list) or not kadry:
            continue
        if shot == 2:
            match2 = [
                item
                for item in kadry
                if isinstance(item, dict)
                and any(
                    m in str(item.get("id") or item.get("shot_id") or "").lower() for m in ("02", "s2", "-2")
                )
            ]
            if match2:
                row["кадры"] = match2
                continue
            if len(kadry) > 1:
                row["кадры"] = [kadry[1]] if isinstance(kadry[1], dict) else kadry[1:2]
                continue
        match = [
            item for item in kadry if isinstance(item, dict) and str(item.get("id") or "").strip() == sid
        ]
        if match:
            row["кадры"] = match
            continue
        if action:
            same = [
                item
                for item in kadry
                if isinstance(item, dict)
                and str(item.get("действие") or item.get("action") or "").strip() == action
            ]
            if same:
                row["кадры"] = same
                continue
        if len(kadry) > 1:
            row["кадры"] = [kadry[0]] if isinstance(kadry[0], dict) else kadry[:1]


def load_img_pr_master(project: object | None) -> tuple[Path | None, str]:
    """Живой .md агента img_pr проекта — тот же файл, что у ноды."""
    if project is None:
        return None, ""
    try:
        name, path, _text, source = read_resolved_project_prompt(project, "img_pr")
        logger.info(
            "montage_ai_change: img_pr master variant={!r} source={} path={}",
            name,
            source,
            path,
        )
        if path.is_file():
            return path, name
    except Exception as exc:  # noqa: BLE001
        logger.warning("montage_ai_change: img_pr master skip: {}", exc)
    return None, ""


def load_anim_pr_master(project: object | None) -> tuple[Path | None, str]:
    """Живой .md агента anim_pr проекта (07_animation) — тот же файл, что у ноды."""
    if project is None:
        return None, ""
    try:
        name, path, _text, source = read_resolved_project_prompt(project, "anim_pr")
        logger.info(
            "montage_ai_change: anim_pr master variant={!r} source={} path={}",
            name,
            source,
            path,
        )
        if path.is_file():
            return path, name
    except Exception as exc:  # noqa: BLE001
        logger.warning("montage_ai_change: anim_pr master skip: {}", exc)
    return None, ""


def load_img_pr_rules(project: object | None) -> str:
    """Текст агента — только если нужно прочитать, не класть в system."""
    path, _name = load_img_pr_master(project)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_C_ID_RE = re.compile(r"\bc(\d{2})\b", re.I)


def character_ids_from_prompt(text: str) -> list[str]:
    seen: list[str] = []
    for match in _C_ID_RE.finditer(text or ""):
        rid = f"c{match.group(1)}"
        if rid not in seen:
            seen.append(rid)
    return seen


_STYLE_LINE_RE = re.compile(r"(?im)^(?:\*\*)?(?:STYLE|Final style lock|Negative)\b")
_MAX_AI_CHANGE_STYLE = 900


def trim_style_encyclopedia(text: str, max_style: int = _MAX_AI_CHANGE_STYLE) -> str:
    """Агент часто копирует §5–§6 целиком — тогда Outsee сжимает персонажа."""
    raw = (text or "").strip()
    match = _STYLE_LINE_RE.search(raw)
    start = match.start() if match else raw.find("STYLE:")
    if start < 0:
        return raw
    scene, style = raw[:start].rstrip(), raw[start:].strip()
    if len(style) <= max_style:
        return raw
    cut = style[:max_style]
    sp = cut.rfind(" ")
    if sp > int(max_style * 0.85):
        cut = cut[:sp]
    logger.info(
        "montage_ai_change: STYLE {} → {} симв (блок персонажа не трогаем)",
        len(style),
        len(cut),
    )
    return f"{scene}\n\n{cut}" if scene else cut


def strip_ai_change_reply(raw: str) -> str:
    """Срезать обёртки; если модель вернула apply-ops — взять промт_картинки."""
    text = (raw or "").strip()
    if not text:
        return ""
    fence = re.match(r"^```(?:\w+)?\s*\n?(.*?)\n?```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") and '"ops"' in text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            ops = data.get("ops")
            if isinstance(ops, list) and ops:
                fields = ops[0].get("fields") if isinstance(ops[0], dict) else None
                if isinstance(fields, dict):
                    for key in ("промт_картинки", "image_prompt", "промт_видео"):
                        val = str(fields.get(key) or "").strip()
                        if val:
                            return val
    text = re.sub(
        r"^(?:updated\s+)?(?:image\s+|video\s+)?prompt\s*:\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:обновлённый\s+|новый\s+)?пром[пт]\s*:\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'«»":
        text = text[1:-1].strip()
    from app.services.prompt_sanitizer import clean_prompt_from_ai_chatter

    cleaned, is_refusal = clean_prompt_from_ai_chatter(text)
    if is_refusal or not cleaned:
        raise RuntimeError(f"ИИзменение: GPT вернул отказ или рассуждения вместо промта: {text[:100]}")
    return cleaned.strip()


def system_for_kind(kind: AiChangeKind, *, img_pr_rules: str = "") -> str:
    del img_pr_rules
    return _SYSTEM_VIDEO if kind == "video" else _SYSTEM_IMAGE


async def rewrite_prompt_via_gpt(
    *,
    voiceover_text: str,
    kind: AiChangeKind,
    project_id: int | None = None,
    project: object | None = None,
    img_pr_rules: str = "",
    img_pr_path: Path | None = None,
    img_pr_variant: str = "",
    image_prompt: str = "",
    db_card_path: Path | None = None,
    instruction: str = "",
    action: str = "",
) -> str:
    """Агент + карточка Базы + закадр → vibecode LLM → промт как есть."""
    del img_pr_rules, img_pr_variant, image_prompt
    from app.services.llm_override import bind_generation_llm

    user = build_ai_change_user_message(
        voiceover_text=voiceover_text,
        instruction=instruction,
        action=action,
    )
    system = system_for_kind(kind)
    files: list[Path] = []
    if img_pr_path is not None and img_pr_path.is_file():
        files.append(img_pr_path)
    if db_card_path is not None and db_card_path.is_file():
        files.append(db_card_path)
    node_type = "animation_prompts" if kind == "video" else "image_prompts"
    with bind_generation_llm(project, node_type=node_type):
        gpt = get_gpt_client()
        raw = await gpt.ask_with_files(
            user,
            files,
            timeout=60,
            project_id=project_id,
            expect_file_download=False,
            system=system,
            auto_pack=False,
        )
    cleaned = trim_style_encyclopedia(strip_ai_change_reply(raw))
    if not cleaned:
        raise RuntimeError("ИИзменение: GPT вернул пустой промт")
    logger.info(
        "montage_ai_change: kind={} pid={} in_vo={} out={} master={} head={!r} refs={}",
        kind,
        project_id,
        len((voiceover_text or "").strip()),
        len(cleaned),
        img_pr_path.name if img_pr_path is not None else "—",
        (cleaned or "").replace("\n", " ")[:80],
        character_ids_from_prompt(cleaned),
    )
    return cleaned
