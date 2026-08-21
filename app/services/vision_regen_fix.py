"""Авто-правки промта перед vision-regen.

Если агент не дал ## db_patch, система сама вставляет жёсткий VISION_FIX
в НАЧАЛО промпта регенерации. Этап 4 (B): вердикт КАЖДОЙ оси брака
доезжает до промпта — для всех kind (scenes/videos) и обоих shot;
character/clones сохраняют исторические, более специфичные билдеры
(MUST show / NO CLONES), остальные оси — AXIS_FIX_LINES.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.check_analysis import (
    extract_critical_frame_regen_targets,
    extract_vision_issues,
    normalize_frame_regen_token,
)

_CID_RE = re.compile(r"\bc(\d{1,3})\b", re.IGNORECASE)
_MISSING_RE = re.compile(
    r"(?i)(?:нет|отсутств\w*|missing|без|не\s+вид\w*|"
    r"должен\s+(?:быть|присутств\w*)|нужен|обязателен)\s+"
    r"(?:заявленн\w+\s+|обязательн\w+\s+)?(c\d{1,3})"
)
_CLONE_RE = re.compile(
    r"(?i)двойник|клон|копи[яий]|идентичн\w+\s+копи|"
    r"лишн\w+\s+(?:фигур|персонаж|мужск|женск|копи)|"
    r"не\s+предусмотр\w+|не\s+указан\w+\s+в\s+баз|"
    r"похож\w+\s+(?:мужск|фигур)|два\s+(?:одинаков|похож)|"
    r"несколько\s+(?:практически\s+)?идентичн|"
    r"множеств\w+\s+двойник"
)
_VISION_FIX_RE = re.compile(
    r"\n?\[VISION_FIX\][\s\S]*?\[/VISION_FIX\]\s*",
    re.IGNORECASE,
)

# Этап 4 (B.3): ось брака → корректирующая инструкция в [VISION_FIX].
# character/clones НЕ здесь — у них специфичные билдеры (_fix_block).
AXIS_FIX_LINES: dict[str, str] = {
    "style": (
        "STYLE LOCK: match the project's established visual style exactly "
        "as described in the base prompt; no style drift, no switching "
        "between photo-realism / anime / 3D."
    ),
    "logic": (
        "SCENE LOGIC: depict exactly what the base prompt describes — no "
        "invented scene, extra props, actions or settings beyond it."
    ),
    "format": (
        "FORMAT: keep the required aspect ratio and composition of the "
        "target format; no letterboxing, no collage, no split panels."
    ),
    "pose": (
        "POSE/LAYOUT: follow the pose and layout required by the base "
        "prompt; poses must be natural and anatomically plausible."
    ),
    "text": (
        "NO STRAY TEXT: no captions, watermarks, UI labels, panel "
        "signatures or floating text; text only on in-world objects "
        "explicitly named in the base prompt."
    ),
    "angles": (
        "ANGLES: show the required distinct sides/angles; do not repeat "
        "the same angle where different views are required."
    ),
    "quality": (
        "QUALITY: clean render — no artifacts, smears, broken faces or "
        "eyes, duplicated or missing limbs, no visible seams."
    ),
    "hands": (
        "HANDS: exactly five fingers per hand, anatomically correct hands "
        "and limbs; no extra, missing or fused fingers."
    ),
}

# Поле apply-ops для (kind, shot) — алиасы уже в db_apply.FIELD_ALIASES.
_FIELD_BY_KIND_SHOT: dict[tuple[str, int], str] = {
    ("scenes", 1): "промт_картинки",
    ("scenes", 2): "промт_картинки_2",
    ("videos", 1): "промт_анимации",
    ("videos", 2): "промт_видео_2",
}


def base_prompt_for(fr: Frame, kind: str, shot: int) -> str:
    """Текущий промпт единицы регенерации (scenes/videos × shot 1/2)."""
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    if kind == "videos":
        if int(shot) == 2:
            return str(attrs.get("animation_prompt_shot2") or "")
        return str(fr.animation_prompt or "")
    if int(shot) == 2:
        return str(attrs.get("image_prompt_shot2") or "")
    return str(fr.image_prompt or "")


def _norm_cid(raw: str) -> str:
    m = re.match(r"^c?(\d{1,3})$", (raw or "").strip().lower())
    if not m:
        return ""
    return f"c{int(m.group(1)):02d}"


def _frame_nums_from_issue(body: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(
        r"\bframe[_-]?(\d{1,4})(?:[_-]?(?:s2|shot2))?\b",
        body or "",
        re.IGNORECASE,
    ):
        out.add(int(m.group(1)))
    tok = normalize_frame_regen_token(body or "")
    if tok:
        out.add(int(tok["number"]))
    return out


def _persons_list(fr: Frame) -> list[str]:
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    raw = attrs.get("персонажи") or attrs.get("characters") or attrs.get("persons")
    if isinstance(raw, list):
        return [_norm_cid(str(x)) for x in raw if _norm_cid(str(x))]
    s = str(raw or "").strip()
    if not s or s in ("—", "-", "–"):
        return []
    return [_norm_cid(m.group(0)) for m in _CID_RE.finditer(s) if _norm_cid(m.group(0))]


def _char_blurb(char_rows: list[dict[str, str]], cid: str) -> str:
    for c in char_rows:
        if (c.get("id") or "").lower() == cid:
            bits = [cid]
            if c.get("name"):
                bits.append(str(c["name"]))
            if c.get("look"):
                bits.append(f"look:{c['look'][:120]}")
            if c.get("clothes"):
                bits.append(f"clothes:{c['clothes'][:120]}")
            return " — ".join(bits)
    return cid


def _reply_marks_clones(reply: str, frame_num: int) -> bool:
    """True если в отчёте рядом с frame_N говорят про двойников/клоны."""
    raw = reply or ""
    n = int(frame_num)
    windows: list[str] = []
    for m in re.finditer(
        rf"(?is)frame[_-]?0*{n}\b.{{0,160}}|.{{0,120}}frame[_-]?0*{n}\b",
        raw,
    ):
        windows.append(m.group(0))
    for m in re.finditer(
        rf"(?is)кадр(?:а|ов|у)?\s+{n}\b.{{0,120}}",
        raw,
    ):
        windows.append(m.group(0))
    blob = "\n".join(windows) if windows else raw
    return bool(_CLONE_RE.search(blob))


def _fix_block(
    *,
    must: list[str],
    forbid_clones: bool,
    char_rows: list[dict[str, str]],
    reason: str,
    clone_hard: bool = False,
    axes: list[str] | None = None,
) -> str:
    lines = ["[VISION_FIX]", f"Reason: {reason[:220]}"]
    # Этап 4 (B.3): агрегация осей per-frame — все оси кадра в ОДИН блок.
    for ax in axes or []:
        axis_line = AXIS_FIX_LINES.get(ax)
        if axis_line:
            lines.append(axis_line)
    if must:
        lines.append(
            "MUST show exactly these character ids, clearly recognizable: "
            + "; ".join(_char_blurb(char_rows, c) for c in must)
        )
        if len(must) == 1:
            cid = must[0]
            lines.append(
                f"SINGLE HERO RULE: depict EXACTLY ONE human — {cid} — "
                f"once only. People count = 1. Zero twins of {cid}."
            )
            lines.append(
                f"RU: ровно ОДИН персонаж {cid}, без второго похожего лица, "
                f"без копий, без клонов, без пятерых одинаковых."
            )
        else:
            lines.append(
                "Do NOT omit any listed character. Each listed id appears "
                "exactly once unless the base prompt names a group role."
            )
    if forbid_clones or clone_hard:
        lines.append(
            "HARD CONSTRAINT — NO CLONES / NO DOUBLES / NO LOOKALIKES:\n"
            "- Forbidden: 2+ people with the same face, mustache, age, hair, "
            "or costume (identical c02 copies).\n"
            "- Forbidden: mirrored twins, crowd of the same hero, "
            "extra copies 'for composition'.\n"
            "- Other people only if the base prompt names a DISTINCT role "
            "(doctor, wife, stranger) with clearly different face/clothes.\n"
            "- If prior gens showed multiple identical heroes — this regen "
            "must show a SINGLE hero figure only."
        )
        if clone_hard:
            lines.append(
                "CLONE RETRY: previous image FAILED for duplicate heroes. "
                "Composition = one person only; empty space / props / archive "
                "paper OK; do not fill the frame with copies of the same man."
            )
    lines.append("[/VISION_FIX]")
    return "\n".join(lines)


def merge_prompt_with_fix(base: str, fix: str) -> str:
    """Вставить VISION_FIX в НАЧАЛО промта (модель чаще слушает префикс)."""
    text = _VISION_FIX_RE.sub("", base or "").rstrip()
    fix = (fix or "").strip()
    if not fix:
        return text
    if not text:
        return fix
    return f"{fix}\n\n{text}"


def plan_vision_prompt_fixes(
    reply: str,
    *,
    frames_by_num: dict[int, Frame],
    char_rows: list[dict[str, str]],
    frame_targets: list[dict[str, Any]] | None = None,
    clone_hard: bool = False,
    kind: str = "scenes",
) -> list[dict[str, Any]]:
    """ops для apply_ops: VISION_FIX в промт critical/regen-кадров.

    Этап 4 (B): все оси, оба shot (поле по (kind, shot)), kind
    scenes|videos. Для videos фикс идёт в промт анимации (character/clones
    MUST-блоки не подмешиваются — это семантика генерации картинки).
    """
    targets = frame_targets or extract_critical_frame_regen_targets(reply)
    if not targets:
        return []

    critical = [
        i for i in extract_vision_issues(reply) if i.get("severity") == "critical"
    ]
    per: dict[int, dict[str, Any]] = {}
    for issue in critical:
        body = str(issue.get("text") or "")
        nums = _frame_nums_from_issue(body)
        if not nums:
            continue
        missing = [_norm_cid(m.group(1)) for m in _MISSING_RE.finditer(body)]
        missing = [c for c in missing if c]
        clone = bool(_CLONE_RE.search(body))
        axis = str(issue.get("axis") or "").strip()
        for n in nums:
            slot = per.setdefault(
                n, {"must": set(), "clone": False, "reasons": [], "axes": set()}
            )
            slot["must"].update(missing)
            if clone:
                slot["clone"] = True
            if axis:
                slot["axes"].add(axis)
            slot["reasons"].append(body[:160])

    # prose / summary без [critical] — всё равно ловим «двойников» у кадра
    for t in targets:
        num = int(t["number"])
        if _reply_marks_clones(reply, num):
            slot = per.setdefault(
                num, {"must": set(), "clone": False, "reasons": [], "axes": set()}
            )
            slot["clone"] = True
            if not slot["reasons"]:
                slot["reasons"].append(f"clone/double mentioned for frame {num}")

    ops: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for t in targets:
        num = int(t["number"])
        shot = int(t.get("shot") or 1)
        if shot not in (1, 2):
            shot = 1
        field = _FIELD_BY_KIND_SHOT.get((kind, shot))
        if field is None:
            continue
        fr = frames_by_num.get(num)
        if fr is None or not (fr.uuid or "").strip():
            continue
        uuid = str(fr.uuid).strip()
        if (uuid, field) in seen:
            continue
        info = per.get(num) or {}
        axes = set(info.get("axes") or [])
        must = set(info.get("must") or [])
        if kind == "scenes":
            must.update(_persons_list(fr))
            # c02 рядом с упоминанием кадра / двойников → в MUST
            for m in re.finditer(
                rf"(?is)frame[_-]?0*{num}\b.{{0,140}}(c\d{{1,3}})|"
                rf"(c\d{{1,3}}).{{0,100}}frame[_-]?0*{num}\b",
                reply or "",
            ):
                cid = _norm_cid(m.group(1) or m.group(2) or "")
                if cid:
                    must.add(cid)
        reason = "; ".join(info.get("reasons") or []) or (
            f"vision regen frame {num}" + ("s2" if shot == 2 else "")
        )
        hard = clone_hard or bool(info.get("clone"))
        fix = _fix_block(
            must=sorted(must) if kind == "scenes" else [],
            forbid_clones=kind == "scenes",
            char_rows=char_rows,
            reason=reason,
            clone_hard=hard and kind == "scenes",
            axes=sorted(axes),
        )
        base = base_prompt_for(fr, kind, shot)
        new_prompt = merge_prompt_with_fix(base, fix)
        if new_prompt.strip() == base.strip():
            continue
        seen.add((uuid, field))
        ops.append(
            {
                "target": "frame",
                "frame_uuid": uuid,
                "fields": {field: new_prompt},
            }
        )
    return ops


def plan_hero_vision_fixes(reply: str, hero_ids: list[str]) -> dict[str, str]:
    """Этап 4 (B.4): фикс промпта hero-референса по critical-осям.

    Возвращает {cid: VISION_FIX-текст}. Кладётся в
    ``project.meta["vision_fix_hero"]`` и подмешивается generate_hero при
    сборке промпта — карточка персонажа (данные оператора) не мутируется.
    """
    ids = {c for c in (_norm_cid(x) for x in hero_ids or []) if c}
    if not ids:
        return {}
    out: dict[str, str] = {}
    for cid in sorted(ids):
        axes: set[str] = set()
        reasons: list[str] = []
        clone = False
        for issue in extract_vision_issues(reply):
            if issue.get("severity") != "critical":
                continue
            body = str(issue.get("text") or "")
            body_cids = {
                c for c in (_norm_cid(m.group(0)) for m in _CID_RE.finditer(body)) if c
            }
            if cid not in body_cids:
                continue
            axis = str(issue.get("axis") or "").strip()
            if axis:
                axes.add(axis)
            if _CLONE_RE.search(body):
                clone = True
            reasons.append(body[:160])
        if not reasons:
            continue
        out[cid] = _fix_block(
            must=[],
            forbid_clones=clone,
            char_rows=[],
            reason="; ".join(reasons),
            clone_hard=clone,
            axes=sorted(axes),
        )
    return out


# Синонимы поля промпта: модельный db_patch может писать в англ. алиас.
_FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "промт_картинки": ("промт_картинки", "image_prompt"),
    "промт_картинки_2": ("промт_картинки_2", "image_prompt_shot2"),
    "промт_анимации": ("промт_анимации", "animation_prompt"),
    "промт_видео_2": ("промт_видео_2", "animation_prompt_shot2"),
}


def merge_db_patches(
    primary: dict[str, Any] | None,
    auto_ops: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Модельный db_patch + вшить VISION_FIX в те же uuid (клоны важнее).

    Этап 4 (B): merge по всем полям промптов (shot1/shot2, img/anim), не
    только промт_картинки.
    """
    if not auto_ops and not primary:
        return None
    out: dict[str, Any] = {}
    if primary and isinstance(primary.get("characters"), list):
        out["characters"] = list(primary["characters"])
    auto_by_u = {
        str(op.get("frame_uuid") or "").strip(): op
        for op in auto_ops
        if isinstance(op, dict) and str(op.get("frame_uuid") or "").strip()
    }
    ops: list[dict[str, Any]] = []
    have: set[str] = set()
    for op in (primary or {}).get("ops") or []:
        if not isinstance(op, dict):
            continue
        op2 = dict(op)
        u = str(op2.get("frame_uuid") or "").strip()
        auto = auto_by_u.get(u)
        if auto and isinstance(op2.get("fields"), dict):
            fields = dict(op2["fields"])
            for canon, aliases in _FIELD_SYNONYMS.items():
                auto_p = str((auto.get("fields") or {}).get(canon) or "")
                if not auto_p:
                    continue
                mfix = _VISION_FIX_RE.search(auto_p)
                if not mfix:
                    continue
                model_key = next((a for a in aliases if a in fields), None)
                if model_key is not None:
                    # вытащить VISION_FIX из auto → в начало model prompt
                    fields[model_key] = merge_prompt_with_fix(
                        str(fields.get(model_key) or ""), mfix.group(0).strip()
                    )
                else:
                    fields[canon] = auto_p
            op2["fields"] = fields
            have.add(u)
        elif u:
            have.add(u)
        ops.append(op2)
    for u, op in auto_by_u.items():
        if u in have:
            continue
        ops.append(op)
    if ops:
        out["ops"] = ops
    return out or None


async def build_auto_vision_db_patch(
    session: AsyncSession,
    project: Project,
    reply: str,
    frame_targets: list[dict[str, Any]] | None = None,
    *,
    kind: str = "scenes",
) -> list[dict[str, Any]]:
    from app.services.vision_check_db import load_character_rows

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    by_num = {fr.number: fr for fr in frames}
    chars = await load_character_rows(session, project)
    meta = project.meta if isinstance(project.meta, dict) else {}
    round_n = int(meta.get("vision_check_round") or 0)
    return plan_vision_prompt_fixes(
        reply,
        frames_by_num=by_num,
        char_rows=chars,
        frame_targets=frame_targets,
        clone_hard=round_n >= 1,
        kind=kind,
    )
