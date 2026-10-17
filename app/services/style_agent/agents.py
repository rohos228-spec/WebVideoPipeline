"""Группа агентов: оркестратор (общие правила) + специалисты + синтез + blueprint.

Каждый специалист: свой промпт-файл ``prompts/style_agent/<role>.md``,
анкета из ``schema.AGENT_FIELDS``, один повтор с фидбеком при невалидном
JSON. Все вызовы — через ``llm.ask_json`` (бюджет + журнал).

Роли по ролику:
- ``shots``   — покадровый разбор планов (что в кадре, крупность, камера,
                анимация, начало → изменение → конец, переход) + подтверждение
                склеек (``false_cuts``); vision: шот-листы + листы склеек;
- ``visual``  — рисовка/палитра/свет (overview-лист);
- ``voice``   — голос/музыка/SFX (транскрипт с таймкодами + метрики);
- ``narrative`` — структура сценария (hook/setup/escalation/payoff с
                таймкодами, шаблоны предложений, риторика);
- ``patterns`` — повторяющиеся приёмы и фирменные трюки (шот-листы +
                разбор shots + транскрипт).
Глобальные: ``synth`` (блоки промптов Studio), ``blueprint`` (цепочка нод).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.style_agent import linkage, llm, schema
from app.services.style_agent.budget import IMAGES_PER_CALL, SHOTS_IMAGES_PER_CALL, BudgetExceeded, LlmBudget
from app.services.style_analyzer import batch_paths, scrub_style_topic

PROMPTS_DIR = Path("prompts/style_agent")
ROLE_FILES = {
    "orchestrator": "orchestrator.md",
    "shots": "shots.md",
    "visual": "visual.md",
    "voice": "voice.md",
    "motion": "motion.md",
    "narrative": "narrative.md",
    "patterns": "patterns.md",
    "synth": "synth.md",
    "blueprint": "blueprint.md",
}
_TOPIC_SCRUB_KEYS = {"visual_core_en", "frame_style_lock_en", "subjects", "world", "composition_en"}
_TRANSCRIPT_MAX = 7000
NOT_ON_SHEET = "(не на листе)"
_TABLE_MAX = 9000


def load_prompt(role: str, *, prompts_dir: Path | None = None) -> str:
    base = prompts_dir or PROMPTS_DIR
    fp = base / ROLE_FILES[role]
    try:
        return fp.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("style_agent: нет промпта {}", fp)
        return f"Роль: {role}. Ответ — один JSON-объект с ключами: " + ", ".join(schema.AGENT_FIELDS.get(role, {}))


def system_prompt(role: str, *, prompts_dir: Path | None = None) -> str:
    return load_prompt("orchestrator", prompts_dir=prompts_dir) + "\n\n---\n\n" + load_prompt(role, prompts_dir=prompts_dir)


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _timed_transcript(profile: dict[str, Any]) -> str:
    subs = profile.get("subtitles") or []
    link = profile.get("linkage") or {}
    if subs:
        return linkage.timed_transcript(subs, link.get("shots"))[:_TRANSCRIPT_MAX]
    return str((profile.get("speech") or {}).get("transcript") or "")[:_TRANSCRIPT_MAX]


def _shots_table(profile: dict[str, Any]) -> str:
    return linkage.shots_table(profile.get("shots") or [], profile.get("linkage"))[:_TABLE_MAX]


def _metrics_for(role: str, profile: dict[str, Any]) -> dict[str, Any]:
    fmt = profile.get("format") or {}
    base = {
        "duration_s": fmt.get("duration_s"),
        "aspect": fmt.get("aspect"),
        "resolution": fmt.get("resolution"),
        "fps": fmt.get("fps"),
        "transcript_source": (profile.get("speech") or {}).get("transcript_source"),
    }
    sp = dict(profile.get("speech") or {})
    sp.pop("transcript", None)
    if role == "visual":
        return {**base, "palette": profile.get("palette") or {}, "shots": (profile.get("motion") or {}).get("shots")}
    if role in ("shots", "motion"):
        return {
            **base,
            "motion": profile.get("motion") or {},
            "linkage_stats": (profile.get("linkage") or {}).get("stats") or {},
            "uncertain_cuts_s": [c["t"] for c in profile.get("cuts") or [] if c.get("uncertain")],
            "gradual_cuts_s": [c["t"] for c in profile.get("cuts") or [] if c.get("kind") == "gradual"],
        }
    if role == "voice":
        return {**base, "speech": sp, "music": profile.get("music") or {}, "audio": profile.get("audio") or {}}
    if role == "narrative":
        script = dict(profile.get("script") or {})
        script.pop("sentences_list", None)
        return {**base, "speech": {k: sp.get(k) for k in ("words", "pace_wpm", "pause_median_s")}, "script": script}
    if role == "patterns":
        return {
            **base,
            "motion": profile.get("motion") or {},
            "linkage_stats": (profile.get("linkage") or {}).get("stats") or {},
            "script": {k: (profile.get("script") or {}).get(k) for k in ("beats_per_sec", "sentences", "words_per_sentence_median")},
        }
    return base


def _user_text(role: str, profile: dict[str, Any], video_label: str, *, shots_reply: dict[str, Any] | None = None) -> str:
    metrics = _metrics_for(role, profile)
    head = f"Ролик: {video_label}.\nИзмеренные метрики (JSON): {_compact(metrics)}\n"
    if role in ("shots", "patterns", "motion"):
        head += "\nТаблица планов (детектор склеек; таймкоды авторитетны):\n" + _shots_table(profile) + "\n"
    if role in ("shots", "voice", "narrative", "patterns"):
        head += "\nТранскрипт с таймкодами (источник: " + str(metrics.get("transcript_source") or "—") + "):\n" + _timed_transcript(profile) + "\n"
    if role == "narrative":
        sents = (profile.get("script") or {}).get("sentences_list") or []
        if sents:
            head += "\nПредложения:\n" + "\n".join(f"{s['i']}. [{s['start']:.1f}–{s['end']:.1f}] {s['text']}" for s in sents[:60]) + "\n"
    if role == "patterns" and shots_reply:
        slim = [{k: v for k, v in sh.items() if k in ("i", "what", "framing", "camera", "subject_anim", "change", "transition_out", "vfx", "illustrates")} for sh in shots_reply.get("shots") or []]
        head += "\nРазбор планов (агент shots):\n" + _compact(slim)[:_TABLE_MAX] + "\n"
    return head + f"\nЗаполни анкету роли «{role}». Ответ — один JSON-объект."


def _merge_replies(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Несколько пачек листов одного ролика → один ответ (первое непустое; списки объединяем)."""
    out: dict[str, Any] = {}
    for part in parts:
        for k, v in part.items():
            if v is None:
                continue
            cur = out.get(k)
            if cur is None:
                out[k] = v
            elif isinstance(cur, list) and isinstance(v, list):
                if cur and isinstance(cur[0], dict):
                    seen_i = {x.get("i") or x.get("name") for x in cur}
                    out[k] = cur + [x for x in v if (x.get("i") or x.get("name")) not in seen_i]
                else:
                    seen = {str(x).lower() for x in cur}
                    out[k] = cur + [x for x in v if str(x).lower() not in seen]
            elif isinstance(cur, dict) and isinstance(v, dict):
                keys = set(cur) | set(v)
                try:
                    out[k] = {kk: round((float(cur.get(kk, 0)) + float(v.get(kk, 0))) / 2, 3) for kk in keys}
                except (TypeError, ValueError):
                    out[k] = {**v, **cur}
    return out


def _scrub(role: str, clean: dict[str, Any]) -> dict[str, Any]:
    for key in _TOPIC_SCRUB_KEYS:
        val = clean.get(key)
        if isinstance(val, str) and val:
            scrubbed = scrub_style_topic(val)
            if scrubbed and len(scrubbed) >= 0.4 * len(val):
                clean[key] = scrubbed
    return clean


async def _ask_role(
    role: str,
    user_text: str,
    *,
    files: list[Path] | None,
    budget: LlmBudget,
    label: str,
    project_id: int | None,
    log_path: Path | None,
    prompts_dir: Path | None,
    allow_retry: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    sys_prompt = system_prompt(role, prompts_dir=prompts_dir)
    data, raw = await llm.ask_json(
        user_text, files=files, system=sys_prompt, budget=budget, label=label, project_id=project_id, log_path=log_path
    )
    clean, problems = schema.validate_agent_reply(role, data)
    if problems and allow_retry and budget.can_reserve():
        feedback = (
            user_text
            + "\n\nПредыдущий ответ не прошёл проверку: "
            + "; ".join(problems[:6])
            + ". Верни только валидный JSON-объект по анкете."
        )
        if raw:
            feedback += "\nПредыдущий ответ:\n" + raw[:1500]
        data, raw = await llm.ask_json(
            feedback, files=files, system=sys_prompt, budget=budget, label=label + "/retry", project_id=project_id, log_path=log_path
        )
        clean, problems = schema.validate_agent_reply(role, data)
    if data is None:
        return None, problems or ["ответ не JSON"]
    return _scrub(role, clean), problems


def sheets_for_role(role: str, profile: dict[str, Any]) -> list[Path]:
    sheets = profile.get("sheets") or {}
    ov = [Path(p) for p in sheets.get("overview") or []]
    sh = [Path(p) for p in sheets.get("shots") or sheets.get("motion") or []]
    cu = [Path(p) for p in sheets.get("cuts") or []]
    if role == "shots":
        return sh + cu
    if role == "visual":
        return ov or sh
    if role == "patterns":
        return sh or ov
    if role == "motion":
        return sh or ov
    return []


async def run_specialist(
    role: str,
    profile: dict[str, Any],
    *,
    sheets: list[Path] | None,
    budget: LlmBudget,
    video_label: str,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
    max_batches: int = 1,
    shots_reply: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Один специалист по одному ролику → {reply, problems, calls}."""
    text = _user_text(role, profile, video_label, shots_reply=shots_reply)
    parts: list[dict[str, Any]] = []
    problems_all: list[str] = []
    calls_before = budget.used
    if sheets:
        per_call = SHOTS_IMAGES_PER_CALL if role == "shots" else IMAGES_PER_CALL
        batches = batch_paths(list(sheets), per_call)[: max(1, int(max_batches))]
        for i, batch in enumerate(batches, start=1):
            suffix = f"\nЛисты {i}/{len(batches)}: {', '.join(p.name for p in batch)}." if len(batches) > 1 else ""
            try:
                clean, problems = await _ask_role(
                    role,
                    text + suffix,
                    files=batch,
                    budget=budget,
                    label=f"{role}:{video_label}:{i}",
                    project_id=project_id,
                    log_path=log_path,
                    prompts_dir=prompts_dir,
                    allow_retry=(i == 1),
                )
            except BudgetExceeded:
                problems_all.append("лимит LLM исчерпан — часть листов не просмотрена")
                break
            problems_all.extend(problems)
            if clean:
                parts.append(clean)
    else:
        clean, problems = await _ask_role(
            role,
            text,
            files=None,
            budget=budget,
            label=f"{role}:{video_label}",
            project_id=project_id,
            log_path=log_path,
            prompts_dir=prompts_dir,
        )
        problems_all.extend(problems)
        if clean:
            parts.append(clean)
    return {
        "role": role,
        "reply": _merge_replies(parts) if parts else None,
        "problems": problems_all,
        "calls": budget.used - calls_before,
    }


def _slim_videos(per_video: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for v in per_video:
        ag = v.get("agents") or {}
        shots_r = ag.get("shots") or {}
        out.append(
            {
                "video": v.get("video"),
                "visual": ag.get("visual"),
                "voice": ag.get("voice"),
                "shots_summary": {k: shots_r.get(k) for k in ("camera_vocab", "transitions", "subject_motion", "vo_cut_sync", "rhythm_notes", "shot_construction", "subtitles_style")},
                "narrative": ag.get("narrative"),
                "patterns": ag.get("patterns"),
                "linkage_stats": ((v.get("metrics") or {}).get("linkage") or {}).get("stats"),
                "script": {k: ((v.get("metrics") or {}).get("script") or {}).get(k) for k in ("beats_per_sec", "sentences_per_min", "words_per_sentence_median", "hook_text", "first_sentence", "last_sentence")},
            }
        )
    return out


def _fixed_inputs(passport: dict[str, Any]) -> str:
    """Решения кода, которые synth/blueprint не имеют права менять."""
    medium = schema.passport_value(passport, "visual.medium")
    cam = schema.passport_value(passport, "motion.camera_vocab")
    lines = []
    if medium:
        lines.append(f"- visual.medium = {medium} (решено кодом). Не меняй технику в текстах; label/style_id и visual_core_en должны ей соответствовать.")
        if str(medium) in ("3d_render", "2d_flat_vector", "anime", "claymation", "pixel_art", "watercolor"):
            lines.append("- Запрещены слова photoreal/photorealistic/realistic footage/реализм/фотореалистичный в любых полях; рисовка — стилизованная.")
            lines.append(f"- {medium} и его синонимы НЕ могут быть в negative_en.")
    if cam:
        lines.append(f"- motion.camera_vocab (по описаниям всех планов) = {_compact(cam)}; camera_motion_en пиши по этим долям.")
    lvl = schema.passport_value(passport, "music.level_vs_voice_db")
    if lvl is not None:
        lines.append(f"- музыка: {lvl} dB относительно голоса (измерено) — не пиши, что уровень не задан.")
    dens = schema.passport_value(passport, "sfx.density")
    if dens:
        lines.append(f"- плотность SFX = {dens}.")
    return ("ЗАФИКСИРОВАНО КОДОМ (не менять):\n" + "\n".join(lines) + "\n\n") if lines else ""


def missing_shot_ids(reply: dict[str, Any] | None, n_shots: int) -> list[int]:
    """Планы без vision-описания: нет записи, нет крупности/камеры или пометка «(не на листе)»."""
    by_i = {int(e["i"]): e for e in (reply or {}).get("shots") or [] if isinstance(e, dict) and e.get("i") is not None}
    out = []
    for i in range(1, n_shots + 1):
        e = by_i.get(i)
        if not e or str(e.get("what") or "").startswith(NOT_ON_SHEET) or not (e.get("framing") and e.get("camera")):
            out.append(i)
    return out


def sheets_for_shot_ids(profile: dict[str, Any], ids: list[int], *, per_sheet: int = 4) -> list[Path]:
    """Шот-листы, на которых стоят планы ``ids`` (план i → лист (pos)//4 в порядке таблицы)."""
    sheets = [Path(p) for p in (profile.get("sheets") or {}).get("shots") or []]
    order = [int(s["i"]) for s in profile.get("shots") or []]
    idx = sorted({order.index(i) // per_sheet for i in ids if i in order})
    return [sheets[k] for k in idx if k < len(sheets)]


def merge_patch_shots(reply: dict[str, Any], patch: dict[str, Any] | None, ids: list[int]) -> tuple[dict[str, Any], int]:
    """Подставить описания из дозапроса только для недостающих планов."""
    if not patch:
        return reply, 0
    new = {int(e["i"]): e for e in patch.get("shots") or [] if isinstance(e, dict) and e.get("i") is not None}
    rows = [dict(e) for e in reply.get("shots") or []]
    pos = {int(e["i"]): k for k, e in enumerate(rows) if e.get("i") is not None}
    fixed = 0
    for i in ids:
        e = new.get(i)
        if not e or str(e.get("what") or "").startswith(NOT_ON_SHEET):
            continue
        if i in pos:
            rows[pos[i]] = {**rows[pos[i]], **{k: v for k, v in e.items() if v not in (None, "", [])}}
        else:
            rows.append(e)
        fixed += 1
    rows.sort(key=lambda e: int(e.get("i") or 0))
    out = dict(reply)
    out["shots"] = rows
    return out, fixed


async def run_shots_patch(
    profile: dict[str, Any],
    reply: dict[str, Any],
    ids: list[int],
    *,
    budget: LlmBudget,
    video_label: str,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
    max_calls: int = 1,
) -> dict[str, Any]:
    """Дозапрос shots только по листам с недостающими планами (≤4 листа за вызов)."""
    files = sheets_for_shot_ids(profile, ids)
    if not files:
        return {"reply": reply, "fixed": 0, "problems": ["нет листов для недостающих планов"]}
    text = _user_text("shots", profile, video_label) + (
        f"\n\nДОЗАПРОС: опиши ТОЛЬКО планы № {', '.join(map(str, ids))} (они есть на приложенных листах; метки «#N» на кадрах). "
        "Остальные планы не возвращай. false_cuts — только среди этих планов."
    )
    problems: list[str] = []
    fixed_total = 0
    for k, batch in enumerate(batch_paths(files, SHOTS_IMAGES_PER_CALL)[: max(1, max_calls)], start=1):
        clean, probs = await _ask_role(
            "shots", text, files=batch, budget=budget, label=f"shots:{video_label}:patch{k}",
            project_id=project_id, log_path=log_path, prompts_dir=prompts_dir, allow_retry=False,
        )
        problems.extend(probs)
        reply, fixed = merge_patch_shots(reply, clean, ids)
        fixed_total += fixed
    return {"reply": reply, "fixed": fixed_total, "problems": problems}


def clean_narrative_reply(reply: dict[str, Any] | None, profile: dict[str, Any]) -> dict[str, Any] | None:
    """OCR-мусор в цитатах нарратива (кэш до чистки субтитров) → по очищенным предложениям/словарю."""
    if not reply:
        return reply
    import difflib

    from app.services.style_agent import ocr

    sents = [str(x.get("text") or "") for x in (profile.get("script") or {}).get("sentences_list") or [] if isinstance(x, dict)]
    lines = [str(x.get("text") or "") for x in profile.get("subtitles") or [] if isinstance(x, dict)]
    vocab = set(ocr._COMMON_EN) | {w for t in lines for w in t.lower().split() if len(w) >= 3}

    def fix(q: str) -> str:
        q = str(q)
        best, score = None, 0.0
        for sent in sents:
            r = difflib.SequenceMatcher(None, q.lower(), sent.lower()).ratio()
            if r > score:
                best, score = sent, r
        if best and score >= 0.8:
            return best
        prefix = ""
        m = __import__("re").match(r"^(\[[^\]]*\]\s*)(.*)$", q)
        if m:
            prefix, q = m.group(1), m.group(2)
        return prefix + ocr.clean_text(q, vocab)

    def walk(x: Any, key: str = "") -> Any:
        if isinstance(x, dict):
            return {k: walk(v, k) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        if isinstance(x, str) and key in ("quote", "example", "examples", "example_hook", "example_payoff", "hook_text"):
            return fix(x)
        return x

    return walk(reply)


async def run_synth(
    passport_draft: dict[str, Any],
    per_video: list[dict[str, Any]],
    *,
    budget: LlmBudget,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
) -> dict[str, Any]:
    """Синтез текстовых блоков для промптов Studio (1 вызов + ≤1 повтор)."""
    draft_slim = {k: v for k, v in passport_draft.items() if k in ("sources", *schema.AXES)}
    text = (
        _fixed_inputs(passport_draft)
        + "Агрегированный паспорт (значения/уверенность/поддержка — не менять):\n"
        + _compact(draft_slim)
        + "\n\nОтветы специалистов по роликам:\n"
        + _compact(_slim_videos(per_video))[:16000]
        + "\n\nЗаполни анкету synth. Ответ — один JSON-объект."
    )
    calls_before = budget.used
    clean, problems = await _ask_role(
        "synth", text, files=None, budget=budget, label="synth", project_id=project_id, log_path=log_path, prompts_dir=prompts_dir
    )
    return {"role": "synth", "reply": clean, "problems": problems, "calls": budget.used - calls_before}


async def run_blueprint(
    passport: dict[str, Any],
    per_video: list[dict[str, Any]],
    *,
    budget: LlmBudget,
    project_id: int | None = None,
    log_path: Path | None = None,
    prompts_dir: Path | None = None,
) -> dict[str, Any]:
    """Blueprint: цепочка нод Studio + шаблоны промптов (1 вызов + ≤1 повтор)."""
    from app.services.style_agent import blueprint as bp

    slim = {k: passport.get(k) for k in ("style_id", "label", "mapping", "script", "linkage", "patterns", "peculiarities")}
    slim["axes"] = {ax: passport.get(ax) for ax in ("format", "visual", "voice", "music", "sfx", "motion", "narrative")}
    text = (
        _fixed_inputs(passport)
        + "Паспорт стиля v2 (агрегат по роликам):\n"
        + _compact(slim)[:18000]
        + "\n\nДоступные типы нод Studio (existing) и их назначение:\n"
        + _compact(bp.node_catalog_for_prompt())
        + "\n\nСкелет цепочки по умолчанию (можно уточнять параметры/промпты, добавлять new-ноды):\n"
        + _compact(bp.default_chain(passport))[:12000]
        + "\n\nЗаполни анкету blueprint. Ответ — один JSON-объект."
    )
    calls_before = budget.used
    clean, problems = await _ask_role(
        "blueprint", text, files=None, budget=budget, label="blueprint", project_id=project_id, log_path=log_path, prompts_dir=prompts_dir
    )
    return {"role": "blueprint", "reply": clean, "problems": problems, "calls": budget.used - calls_before}


FALSE_CUT_MAX_SCORE = 0.08  # выше — жёсткая склейка, vision её не отменяет
FALSE_CUT_MAX_MERGED_S = 6.0


def _false_cut_allowed(prev: dict[str, Any], cur: dict[str, Any]) -> bool:
    """Ложной можно признать только слабую/сомнительную склейку и не склеивать планы в «простыню»."""
    weak = (
        str(cur.get("transition_in") or "") in ("cue", "refined", "gradual")
        or bool(cur.get("uncertain"))
        or float(cur.get("cut_score") or 0.0) < FALSE_CUT_MAX_SCORE
    )
    if not weak:
        return False
    merged = float(cur["end"]) - float(prev["start"])
    return merged <= FALSE_CUT_MAX_MERGED_S or bool(cur.get("uncertain"))


def apply_false_cuts(shots: list[dict[str, Any]], false_cuts: list[float] | None, *, tolerance_s: float = 0.3) -> list[dict[str, Any]]:
    """Убрать склейки, которые vision-агент счёл ложными → слить соседние планы."""
    if not false_cuts or len(shots) < 2:
        return shots
    out: list[dict[str, Any]] = []
    for s in shots:
        start = float(s["start"])
        if out and any(abs(start - fc) <= tolerance_s for fc in false_cuts) and _false_cut_allowed(out[-1], s):
            prev = out[-1]
            prev["end"] = s["end"]
            prev["len"] = round(float(prev["end"]) - float(prev["start"]), 3)
            prev["merged_from"] = (prev.get("merged_from") or [int(prev["i"])]) + [int(s["i"])]
            continue
        out.append(dict(s))
    for k, s in enumerate(out, start=1):
        s["orig_i"] = s.get("orig_i") or int(s["i"])
        s["i"] = k
    return out


def merge_shot_descriptions(shots: list[dict[str, Any]], reply: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Описания агента shots (по ``i`` таблицы) → в строки планов."""
    if not reply:
        return shots
    by_i = {int(e["i"]): e for e in reply.get("shots") or [] if e.get("i") is not None}
    for s in shots:
        src_ids = [int(s.get("orig_i") or s["i"])] + [int(x) for x in s.get("merged_from") or []]
        for sid in src_ids:
            e = by_i.get(sid)
            if not e:
                continue
            for key in schema.SHOT_ENTRY_FIELDS:
                if e.get(key) and not s.get(key):
                    s[key] = e[key]
    return shots
