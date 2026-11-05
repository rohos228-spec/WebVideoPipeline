"""Проверка содержания после шагов: ответ модели прошёл схему, но по смыслу неверен.

Схема контракта ловит только форму (поля, типы, «≥2 кадра»). Что модель потеряла
кусок закадра, продублировала фразу, сделала не то число кадров или описала в промте
картинки чужого героя — схема не видит. Здесь это проверяется кодом, без LLM:

- ``vo_concat`` — закадр кадров подряд = закадровый текст (voiceover.txt), без потерь
  и дублей; сравнение без пробелов и ударений, как у «Границ сцен» (``scene_boundaries``);
- ``frame_count`` — кадров столько, сколько явно попросили в сценарии;
- ``split_degraded`` — разбивка сделана локально, без модели;
- ``prompt_characters`` — герои cNN в промте картинки есть в реестре и совпадают с
  персонажами кадра;
- ``prompt_style`` — в промте картинки есть стиль проекта (пластилин, вязаный, нуар):
  обёртку стиля код больше не дописывает, и вариант промта без стиля молча даёт
  картинки «без пластилина».

Итог шага — отчёт ``{"step", "ok", "findings", "at"}``: пишется в ``project.meta
["content_checks"][шаг]``, уходит уведомлением в чат и читается чатом через
``diag.content_checks``. К находке — исправления существующими действиями
(``fix_options``): бесплатная правка ячейки, если правильный текст однозначно известен,
иначе повтор шага с текстом находки для модели или пересборка промта кадра.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger

META_KEY = "content_checks"
STEP_RU = {"split": "разбивка на кадры", "enrich": "работа с GPT", "img_pr": "промты картинок"}
STEPS = tuple(STEP_RU)
# Больше этого (слов) SequenceMatcher считает долго, а закадр ролика на порядок короче.
_DIFF_LIMIT = 6000
_SNIPPET = 120
_MAX_FINDINGS = 20
# Правок ячеек без человека за раз не больше этого: длинный список — признак сбоя
# разбивки целиком, его чинит повтор шага, а не десяток точечных правок.
MAX_AUTO_EDITS = 5
_CHAR_IN_PROMPT = re.compile(r"(?<![a-z0-9_])c(\d{2,3})(?![0-9])", re.I)
_APPEARANCE_LINE = re.compile(r"^\s*Внешность\s+(c\d{1,3})\s*[:—-]", re.I)


def step_for_running(status_value: str | None) -> str | None:
    """Running-статус шага → какую проверку запускать после него."""
    value = str(status_value or "")
    if value == "splitting":
        return "split"
    if value.startswith("enriching_"):
        return "enrich"
    if value == "generating_image_prompts":
        return "img_pr"
    return None


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clip(text: Any, limit: int = _SNIPPET) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _finding(check: str, severity: str, title_ru: str, detail_ru: str = "", **extra: Any) -> dict:
    return {"check": check, "severity": severity, "title_ru": title_ru, "detail_ru": detail_ru, **extra}


# ---------- закадр: склейка кадров = закадровый текст ----------


# Типографика, которую модель меняет без смысла: ё/е, кавычки, тире, многоточие.
# Ошибкой это не считаем — иначе repair и платные повторы шли бы из-за «ёлочек».
_TYPO = str.maketrans(
    {"ё": "е", "Ё": "Е", "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "—": "-", "–": "-", "…": "..."}
)


def _canon(text: str) -> str:
    return (text or "").replace("\u0301", "").translate(_TYPO)


def _nz(text: str) -> str:
    """Без пробелов, ударений и типографики — как ``scene_boundaries.nz``, но мягче."""
    return re.sub(r"\s+", "", _canon(text))


def _words(text: str) -> list[tuple[str, int, int]]:
    """Слова текста: (слово без ударения и типографики, начало, конец в исходнике)."""
    return [(_canon(m.group(0)), m.start(), m.end()) for m in re.finditer(r"\S+", text or "")]


def _span(raw: str, words: list[tuple[str, int, int]], start: int, end: int) -> str:
    """Исходный текст слов [start, end) — с пунктуацией и пробелами, как написано."""
    if start >= end or not words:
        return ""
    return raw[words[start][1] : words[end - 1][2]].strip()


def _a_pos(opcodes: list[tuple], pos: int) -> int | None:
    """Слово закадра для границы кадра в склейке; None — граница неоднозначна.

    Однозначна, только если лежит в совпавшем куске. На стыке двух совпавших кусков с
    потерянным текстом между ними кандидатов два — куда относится потеря, код не знает.
    """
    found = {i1 + (pos - j1) for tag, i1, _i2, j1, j2 in opcodes if tag == "equal" and j1 <= pos <= j2}
    return found.pop() if len(found) == 1 else None


def compare_voiceover(vo: str, cells: list[dict]) -> dict[str, Any]:
    """Сверка закадра кадров с закадровым текстом.

    ``cells`` — ``[{"number", "uuid", "text"}]`` по порядку. Совпадение решается по знакам
    без пробелов и ударений (склеенные слова «водой.Они» — не ошибка); расхождения
    показываются по словам. Возвращает ``ok``, потерянные куски (``lost``), лишние
    (``extra``), заменённые (``changed``), дубли кадров и ``fixes`` — кадры, у которых
    правильный текст однозначно виден из закадра.
    """
    vo = str(vo or "")
    texts = [str(c.get("text") or "") for c in cells]
    a_n, b_n = _nz(vo), _nz("".join(texts))
    out: dict[str, Any] = {
        "ok": a_n == b_n,
        "vo_chars": len(a_n),
        "frames_chars": len(b_n),
        "lost": [],
        "extra": [],
        "changed": [],
        "duplicates": [],
        "fixes": [],
        "diff_words": 0,
    }
    if out["ok"] or not a_n:
        return out
    a_w = _words(vo)
    b_w: list[tuple[str, int, int]] = []
    owner: list[int] = []
    starts: list[int] = []
    for k, t in enumerate(texts):
        starts.append(len(b_w))
        for w in _words(t):
            b_w.append(w)
            owner.append(k)
    if len(a_w) > _DIFF_LIMIT or len(b_w) > _DIFF_LIMIT:
        return out

    def num(k: int) -> Any:
        return cells[k].get("number", k + 1)

    def b_text(j1: int, j2: int) -> str:
        return _clip(" ".join(texts[owner[j]][b_w[j][1] : b_w[j][2]] for j in range(j1, j2)))

    def frames_of(j1: int, j2: int) -> list[Any]:
        return [num(k) for k in dict.fromkeys(owner[j] for j in range(j1, j2))]

    ops = difflib.SequenceMatcher(
        None, [w[0] for w in a_w], [w[0] for w in b_w], autojunk=False
    ).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        out["diff_words"] += max(i2 - i1, j2 - j1)
        if tag == "delete":
            lost = {
                "text": _clip(_span(vo, a_w, i1, i2)),
                "after_frame": None,
                "before_frame": None,
                "in_frame": None,
            }
            if 0 < j1 < len(b_w) and owner[j1 - 1] == owner[j1]:
                lost["in_frame"] = num(owner[j1])
            else:
                lost["after_frame"] = num(owner[j1 - 1]) if j1 > 0 else None
                lost["before_frame"] = num(owner[j1]) if j1 < len(b_w) else None
            out["lost"].append(lost)
        elif tag == "insert":
            out["extra"].append({"text": b_text(j1, j2), "frames": frames_of(j1, j2)})
        else:
            out["changed"].append(
                {"was": _clip(_span(vo, a_w, i1, i2)), "now": b_text(j1, j2), "frames": frames_of(j1, j2)}
            )
    # Дубль: одинаковый кадр встречается чаще, чем такая фраза есть в закадре.
    norm_cells = [_nz(t) for t in texts]
    seen: dict[str, list[Any]] = {}
    for k, nt in enumerate(norm_cells):
        if len(nt) >= 6:
            seen.setdefault(nt, []).append(num(k))
    for nt, nums in seen.items():
        if len(nums) > max(1, a_n.count(nt)):
            out["duplicates"].append({"text": _clip(texts[norm_cells.index(nt)]), "frames": nums})
    # Однозначная правка кадра: обе его границы в совпавших кусках, а слова между ними
    # в закадре другие и непустые. Пустые — кадр лишний: удаление — не правка ячейки.
    for k in range(len(texts)):
        end = starts[k + 1] if k + 1 < len(starts) else len(b_w)
        s, e = _a_pos(ops, starts[k]), _a_pos(ops, end)
        if s is None or e is None or s >= e:
            continue
        expected = _span(vo, a_w, s, e)
        if _nz(expected) == norm_cells[k]:
            continue
        out["fixes"].append(
            {"number": num(k), "uuid": cells[k].get("uuid"), "was": _clip(texts[k]), "text": expected}
        )
    return out


def vo_findings(vo: str, cells: list[dict], *, step: str) -> list[dict]:
    """Находка «закадр не сходится» по сверке; пусто — сходится."""
    if not str(vo or "").strip() or not cells:
        return []
    cmp = compare_voiceover(vo, cells)
    if cmp["ok"]:
        return []
    parts: list[str] = []
    for item in cmp["lost"][:4]:
        if item.get("in_frame"):
            where = f"в кадре {item['in_frame']}"
        elif item["after_frame"] and item["before_frame"]:
            where = f"между кадрами {item['after_frame']} и {item['before_frame']}"
        else:
            where = f"после кадра {item['after_frame']}" if item["after_frame"] else "перед первым кадром"
        parts.append(f"пропал текст {where}: «{item['text']}»")
    for item in cmp["extra"][:4]:
        parts.append(f"лишний текст {_in_frames(item['frames'])}: «{item['text']}»")
    for item in cmp["changed"][:4]:
        parts.append(f"{_in_frames(item['frames'])} «{item['now']}» вместо «{item['was']}»")
    for item in cmp["duplicates"][:4]:
        parts.append(f"кадры {_nums(item['frames'])} повторяют одну фразу «{item['text']}»")
    frames = sorted(
        {
            n
            for key in ("extra", "changed", "duplicates")
            for item in cmp[key]
            for n in item.get("frames") or []
            if n is not None
        }
        | {
            item[k]
            for item in cmp["lost"]
            for k in ("after_frame", "before_frame", "in_frame")
            if item.get(k)
        }
    )
    detail = "; ".join(parts) or "текст кадров отличается от закадра"
    return [
        _finding(
            "vo_concat",
            "error",
            "Закадр кадров не сходится с закадровым текстом",
            f"Знаков без пробелов: в кадрах {cmp['frames_chars']}, в закадре {cmp['vo_chars']}. {detail}.",
            step=step,
            frames=frames,
            fixes=cmp["fixes"][:_MAX_FINDINGS],
            diff_words=cmp["diff_words"],
        )
    ]


def _in_frames(nums: list[Any]) -> str:
    return f"в кадре {_nums(nums)}" if len(nums) == 1 else f"в кадрах {_nums(nums)}"


def _nums(nums: list[Any]) -> str:
    """[9, 10, 11, 12, 22] → «9–12, 22»: длинный список кадров в чате не читается."""
    vals = sorted({int(n) for n in nums if isinstance(n, int)})
    parts: list[str] = []
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[j] + 1:
            j += 1
        parts.append(str(vals[i]) if i == j else f"{vals[i]}–{vals[j]}")
        i = j + 1
    rest = [str(n) for n in nums if not isinstance(n, int) and n is not None]
    return ", ".join(parts + rest)


# ---------- число кадров ----------

_COUNT_PATTERNS = (
    re.compile(
        r"(?:ровно|строго|всего|итого|разбей\w*\s+на|разбить\s+на)\s+(\d{1,2})\s+кадр(?:ов|а)?(?![а-я])", re.I
    ),
    re.compile(r"(?:количество|число)\s+кадров\s*[:—=-]?\s*(\d{1,2})(?!\d)", re.I),
    re.compile(r"(?<![а-я])кадров\s*[:—=]\s*(\d{1,2})(?!\d)", re.I),
)


def requested_frame_count(project: Any) -> int | None:
    """Точное число кадров, если его ЯВНО попросили; иначе None.

    Параметр шага ``node_step_params.split.frames_count`` или фраза в сценарии
    («ровно 18 кадров», «количество кадров: 18»). Перечисление «Кадр 1… Кадр N» — не
    просьба: в сценарии это часто сцены, а не кадры разбивки.
    """
    meta = getattr(project, "meta", None) or {}
    split = ((meta.get("node_step_params") or {}).get("split") or {}) if isinstance(meta, dict) else {}
    for key in ("frames_count", "frame_count"):
        try:
            n = int(float(cast(Any, split.get(key))))
        except (TypeError, ValueError):
            continue
        if 2 <= n <= 60:
            return n
    plan = str(getattr(project, "general_plan", "") or "")
    for pattern in _COUNT_PATTERNS:
        m = pattern.search(plan)
        if m:
            n = int(m.group(1))
            if 2 <= n <= 60:
                return n
    return None


def count_findings(n_frames: int, requested: int | None, *, step: str = "split") -> list[dict]:
    if requested is None or n_frames == requested:
        return []
    return [
        _finding(
            "frame_count",
            "warning",
            "Число кадров не то, что просили",
            f"Кадров {n_frames}, а в сценарии просили ровно {requested}.",
            step=step,
            frames=[],
            requested=requested,
            got=n_frames,
        )
    ]


def split_contract_problems(vo: str, frames_spec: list[dict]) -> list[str]:
    """Проблемы ответа разбивки для repair-повтора контракта: модель видит, что потеряла.

    Только склейка: число кадров проверяется отдельно и мягче (см. ``run_split_xlsx``).
    """
    cells = [{"number": i + 1, "text": _spec_vo(item)} for i, item in enumerate(frames_spec or [])]
    found = vo_findings(vo, cells, step="split")
    if not found:
        return []
    return [
        "склейка поля «закадр» всех кадров подряд должна дословно совпадать с закадровым "
        "текстом (voiceover.txt), без пропусков, дублей и пересказа: " + found[0]["detail_ru"]
    ]


def _spec_vo(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    for key in ("закадр", "voiceover_text", "voiceover", "реплика"):
        if item.get(key):
            return str(item[key])
    return ""


# ---------- герои в промтах картинок ----------


def prompt_character_ids(prompt: str | None) -> list[str]:
    """cNN, упомянутые в промте, по порядку и без повторов (латинская «c»)."""
    out: list[str] = []
    for m in _CHAR_IN_PROMPT.finditer(str(prompt or "")):
        cid = f"c{m.group(1)}".lower()
        if cid not in out:
            out.append(cid)
    return out


def drop_appearance_lines(prompt: str, cids: set[str]) -> str:
    """Убрать строки канона «Внешность cNN:» для героев, которых в кадре нет."""
    lines = str(prompt or "").splitlines()
    kept = [ln for ln in lines if not ((m := _APPEARANCE_LINE.match(ln)) and m.group(1).lower() in cids)]
    return "\n".join(kept)


def character_findings(frames: list[dict], registry: dict[str, str]) -> list[dict]:
    """Герои в промтах: есть в реестре и совпадают с персонажами кадра.

    ``frames`` — ``[{"number", "uuid", "prompt", "characters"}]``; ``registry`` — id → канон
    внешности (пустая строка, если паспорт пуст). Однозначные расхождения чинятся кодом
    (``fixes`` — новый текст промта): лишняя строка канона чужого героя убирается,
    недостающий канон второго героя вставляется. Остальное — ``regen`` кадра.
    """
    from app.services.frame_cast import cast_ids_in_order
    from app.services.img_pr_canon import ensure_appearance_lines

    findings: list[dict] = []
    for fr in frames:
        prompt = str(fr.get("prompt") or "")
        if not prompt.strip():
            continue
        cast = cast_ids_in_order(fr.get("characters"))
        mentioned = prompt_character_ids(prompt)
        unknown = [c for c in mentioned if registry and c not in registry]
        foreign = [c for c in mentioned if c not in cast and c not in unknown]
        missing = [c for c in cast if c not in mentioned] if len(cast) > 1 else []
        if not (unknown or foreign or missing):
            continue
        problems: list[str] = []
        if unknown:
            problems.append(f"в промте герой {', '.join(unknown)}, которого нет в реестре персонажей")
        if foreign:
            who = ", ".join(cast) if cast else "никого"
            problems.append(f"в промте {', '.join(foreign)}, а в кадре по разметке {who}")
        if missing:
            problems.append(f"в промте не описан {', '.join(missing)} — выйдет с лицом соседа")
        # Однозначно: чужой герой только в строке канона, а недостающему есть канон.
        fixed = prompt
        if foreign:
            fixed = drop_appearance_lines(fixed, set(foreign))
        if missing:
            fixed = ensure_appearance_lines(
                fixed, {c: registry.get(c, "") for c in missing if registry.get(c)}
            )
        left = prompt_character_ids(fixed)
        clean = (
            not unknown
            and not [c for c in left if c not in cast]
            and not [c for c in cast if c not in left and len(cast) > 1]
        )
        findings.append(
            _finding(
                "prompt_characters",
                "error" if (unknown or missing) else "warning",
                f"Кадр {fr.get('number')}: герои в промте картинки не совпадают с кадром",
                "; ".join(problems) + ".",
                step="img_pr",
                frames=[fr.get("number")],
                uuid=fr.get("uuid"),
                fixed_prompt=fixed if clean and fixed != prompt else None,
                regen=not (clean and fixed != prompt),
            )
        )
    return findings[:_MAX_FINDINGS]


# ---------- стиль в промтах картинок ----------

# Семейства стиля: слова, по которым видно стиль в тексте промта (англ. и рус.).
STYLE_FAMILIES: dict[str, tuple[str, ...]] = {
    "пластилин": ("plasticine", "claymation", "clay ", "clay-", "plastilin", "пластилин"),
    # Не «felt»: по-английски это ещё и «почувствовал».
    "вязаный": ("knitted", "textile", "felted", "вязан", "войлок"),
    "нуар": ("noir", "archival", "watercolor grunge", "trash polka", "нуар"),
}


def style_families(text: str) -> set[str]:
    low = f" {(text or '').casefold()} "
    return {name for name, marks in STYLE_FAMILIES.items() if any(m in low for m in marks)}


def expected_style(project: Any) -> str | None:
    """Стиль проекта по настройкам: meta-ключ стиля, вариант промта картинок, мастер-промт."""
    try:
        from app.services.img_pr_style import _local_variant_name, resolve_project_img_style

        meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
        blob = " ".join(
            x for x in (resolve_project_img_style(project) or "", _local_variant_name(project, meta)) if x
        )
    except Exception:  # noqa: BLE001 — проверка стиля не должна ронять проверку шага
        return None
    found = style_families(blob)
    return next(iter(found)) if len(found) == 1 else None


def style_state(rows: list[dict], expected: str | None) -> dict[str, Any] | None:
    """Какой стиль ждём и в скольких промтах он есть: для ответа «стиль на месте»."""
    prompts = [r for r in rows if str(r.get("prompt") or "").strip()]
    if not prompts:
        return None
    fams = [style_families(str(r["prompt"])) for r in prompts]
    want = expected
    if want is None:
        counts: dict[str, int] = {}
        for f in fams:
            for name in f:
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return {"style": None, "with": 0, "total": len(prompts), "source": None}
        want = max(counts.items(), key=lambda kv: kv[1])[0]
    has = sum(1 for f in fams if want in f)
    return {
        "style": want,
        "with": has,
        "total": len(prompts),
        "source": "в настройках проекта" if expected else "в большинстве промтов",
    }


def style_findings(rows: list[dict], expected: str | None) -> list[dict]:
    """Кадры, в промте которых нет стиля проекта.

    ``rows`` — ``[{"number", "uuid", "prompt"}]`` только кадров со своим промтом. Стиль —
    из настроек проекта, иначе стиль большинства промтов. Если стиля не видно нигде —
    молчим: по одним данным нельзя сказать, какой он должен быть.
    """
    prompts = [r for r in rows if str(r.get("prompt") or "").strip()]
    if not prompts:
        return []
    fams = [style_families(str(r["prompt"])) for r in prompts]
    want = expected
    if want is None:
        counts: dict[str, int] = {}
        for f in fams:
            for name in f:
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return []
        name, n = max(counts.items(), key=lambda kv: kv[1])
        if n * 2 < len(prompts):
            return []
        want = name
    missing = [r for r, f in zip(prompts, fams, strict=True) if want not in f]
    if not missing:
        return []
    nums = [r.get("number") for r in missing]
    shown = ", ".join(str(n) for n in nums[:15]) + (" и др." if len(nums) > 15 else "")
    source = "в настройках проекта" if expected else "в большинстве промтов"
    other = sorted({x for r, f in zip(prompts, fams, strict=True) if r in missing for x in f if x != want})
    detail = (
        f"Стиль «{want}» ({source}) не написан в промтах {len(missing)} из {len(prompts)} кадров: {shown}."
        + (f" Вместо него в промтах стиль «{', '.join(other)}»." if other else "")
        + " Стиль в промт картинки сам не дописывается — его пишет вариант промта картинок;"
        " без слов стиля модель картинок рисует как придётся."
    )
    return [
        _finding(
            "prompt_style",
            "error" if len(missing) * 2 >= len(prompts) else "warning",
            f"Промты картинок без стиля «{want}»",
            detail,
            step="img_pr",
            frames=nums,
            style=want,
        )
    ]


# ---------- данные проекта ----------


def voiceover_text(project: Any) -> str:
    """Закадровый текст без побочных эффектов: voiceover.txt, иначе script_text.

    ``ensure_current_voiceover`` при пустом файле пишет его — чтению это не положено.
    """
    try:
        path = Path(project.data_dir) / "voiceover.txt"
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                from app.services.chatgpt_xlsx import _voiceover_text_usable

                if _voiceover_text_usable(text):
                    return text
    except Exception:  # noqa: BLE001
        pass
    return str(getattr(project, "script_text", "") or "")


def _frame_dict(fr: Any) -> dict:
    return {
        "id": getattr(fr, "id", None),
        "number": getattr(fr, "number", None),
        "uuid": getattr(fr, "uuid", None),
        "sort_key": getattr(fr, "sort_key", None),
        "voiceover_text": getattr(fr, "voiceover_text", None) or "",
        "attrs": fr.attrs if isinstance(getattr(fr, "attrs", None), dict) else {},
    }


def vo_cells(frames: list[Any]) -> list[dict]:
    """Ячейки закадра (как в меню съёмки): шоты одной сцены — одна ячейка, текст родителя."""
    from app.services.shot_menu import group_vo_cells

    cells = group_vo_cells([_frame_dict(fr) for fr in frames])
    by_uuid = {getattr(fr, "uuid", None): fr for fr in frames}
    out: list[dict[str, Any]] = []
    for c in cells:
        parent = by_uuid.get(c.get("parent_uuid"))
        out.append(
            {
                "number": getattr(parent, "number", None) or c.get("index"),
                "uuid": c.get("parent_uuid"),
                "text": str(c.get("voiceover") or ""),
            }
        )
    return [c for c in out if c["text"].strip() or c["uuid"]]


async def run_checks(session: Any, project: Any, step: str, *, with_vo: bool = True) -> dict[str, Any]:
    """Все проверки шага по текущим данным. Только чтение.

    ``with_vo=False`` — без сверки закадра: чат проверяет разбивку и промты разом, и одна
    и та же находка не должна прийти дважды.
    """
    from sqlalchemy import select

    from app.models import Entity, Frame

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)))
        .scalars()
        .all()
    )
    findings: list[dict] = []
    vo = voiceover_text(project)
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    if with_vo and step in ("split", "enrich", "img_pr") and frames:
        findings += vo_findings(vo, vo_cells(frames), step=step)
    if step == "split" and frames:
        parents = [fr for fr in frames if (fr.voiceover_text or "").strip()]
        findings += count_findings(len(parents), requested_frame_count(project))
        if meta.get("split_degraded_no_llm"):
            findings.append(
                _finding(
                    "split_degraded",
                    "warning",
                    "Разбивка сделана без модели",
                    "Ответ модели не прошёл проверку, кадры нарезаны локально по предложениям: "
                    "границы кадров не смысловые, число кадров не по сценарию.",
                    step="split",
                    frames=[],
                )
            )
    if step == "img_pr" and frames:
        from app.services.excel_characters import characters_from_entities
        from app.services.img_pr_canon import appearance_line

        entities = (
            (await session.execute(select(Entity).where(Entity.project_id == project.id))).scalars().all()
        )
        cards = {c.id.lower(): appearance_line(c) for c in characters_from_entities(list(entities))}
        rows = [
            {
                "number": fr.number,
                "uuid": fr.uuid,
                "prompt": fr.image_prompt or "",
                "characters": (fr.attrs or {}).get("characters") or (fr.attrs or {}).get("персонажи"),
            }
            for fr in frames
        ]
        findings += character_findings(rows, cards)
        from app.services.vo_shot_expand import uses_parent_still

        own = [r for r, fr in zip(rows, frames, strict=True) if not uses_parent_still(fr)]
        want = expected_style(project)
        findings += style_findings(own, want)
        report = make_report(step, findings, frames=len(frames))
        report["style"] = style_state(own, want)
        return report
    return make_report(step, findings, frames=len(frames))


def make_report(step: str, findings: list[dict], *, frames: int) -> dict[str, Any]:
    findings = findings[:_MAX_FINDINGS]
    body = json.dumps(
        [{k: f.get(k) for k in ("check", "detail_ru", "frames")} for f in findings],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return {
        "step": step,
        "ok": not findings,
        "frames": frames,
        "findings": findings,
        "at": _now(),
        # Отпечаток находок: одна и та же картина даёт одно уведомление, а не по каждому шагу.
        "digest": hashlib.sha1(body.encode("utf-8")).hexdigest()[:16],
    }


def save_report(project: Any, report: dict[str, Any]) -> None:
    meta = dict(project.meta or {})
    stored = dict(meta.get(META_KEY) or {}) if isinstance(meta.get(META_KEY), dict) else {}
    stored[report["step"]] = report
    meta[META_KEY] = stored
    project.meta = meta


def stored_reports(meta: dict | None) -> dict[str, dict]:
    raw = (meta or {}).get(META_KEY) if isinstance(meta, dict) else None
    return {k: v for k, v in (raw or {}).items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


async def check_after_step(session: Any, project: Any, prev_status: str) -> dict[str, Any] | None:
    """Из воркера после успешного шага: проверить и записать в meta. Без исключений наружу."""
    step = step_for_running(prev_status)
    if step is None:
        return None
    try:
        report = await run_checks(session, project, step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] content_checks {}: проверка не выполнена: {}", project.id, step, exc)
        return None
    save_report(project, report)
    if not report["ok"]:
        logger.warning(
            "[#{}] content_checks {}: находок {} — {}",
            project.id,
            step,
            len(report["findings"]),
            "; ".join(f["title_ru"] for f in report["findings"][:3]),
        )
    return report


# ---------- исправления ----------


def fix_options(report: dict[str, Any], project_id: int) -> list[dict[str, Any]]:
    """Исправления находок существующими действиями (формат ``diag.node_failure``).

    ``deterministic`` — правильное значение известно из данных проекта, правка бесплатна
    (ячейка закадра из voiceover.txt, канон героя из паспорта). Повтор шага — платный,
    его пускает только правило ``decide_fix``.
    """
    pid = int(project_id)
    step = str(report.get("step") or "")
    out: list[dict[str, Any]] = []
    rerun = False
    for f in report.get("findings") or []:
        if f.get("check") in ("vo_concat", "frame_count", "split_degraded") and step != "split":
            # После «Работы с GPT» и промтов переделка разбивки сотрёт их работу:
            # только точечная правка ячейки, остальное — человеку. Закадр этот шаг не писал:
            # расхождение — скорее правка человека после разбивки («поменяй текст кадра 4»),
            # поэтому сами не возвращаем, только предлагаем кнопкой.
            out += [dict(e, deterministic=False) for e in _cell_edits(f, pid)]
            continue
        check = f.get("check")
        if check == "vo_concat":
            edits = _cell_edits(f, pid)
            out += edits
            if not edits:
                rerun = True
        elif check in ("frame_count", "split_degraded"):
            rerun = True
        elif check == "prompt_characters":
            if f.get("fixed_prompt") and f.get("uuid"):
                out.append(
                    {
                        "action": "shot.edit_field",
                        "params": {
                            "project_id": pid,
                            "frame_uuid": f["uuid"],
                            "field": "img_prompt",
                            "value": str(f["fixed_prompt"])[:8000],
                        },
                        "title_ru": f"Кадр {f['frames'][0]}: привести героев в промте к канону",
                        "phrase_ru": "",
                        "deterministic": True,
                    }
                )
            elif f.get("uuid"):
                n = f["frames"][0]
                out.append(
                    {
                        "action": "shot.edit_field",
                        "params": {
                            "project_id": pid,
                            "frame_uuid": f["uuid"],
                            "field": "img_prompt",
                            "value": "",
                        },
                        "title_ru": f"Очистить промт кадра {n}, чтобы пересобрать его",
                        "phrase_ru": f"очисти промт картинки кадра {n} в проекте {pid}",
                    }
                )
                rerun = True
        elif check == "prompt_style":
            # Повтор с тем же вариантом даст тот же промт без стиля: сначала вариант, потом пересборка.
            out.append(
                {
                    "action": None,
                    "params": {},
                    "title_ru": f"Вписать стиль «{f.get('style')}» в вариант промта картинок, "
                    "затем очистить промты этих кадров и пересобрать их",
                    "phrase_ru": f"какой вариант промта картинок стоит в проекте {pid}",
                }
            )
    if rerun:
        code = step
        if code in ("split", "img_pr"):
            out.append(
                {
                    "action": "step.run_step",
                    "params": {"project_id": pid, "step_code": code},
                    "title_ru": (
                        "Переделать разбивку: модель получит список расхождений"
                        if code == "split"
                        else "Пересобрать пустые промты картинок"
                    ),
                    "phrase_ru": (
                        f"переделай разбивку проекта {pid}"
                        if code == "split"
                        else f"запусти промты картинок проекта {pid}"
                    ),
                }
            )
    out.append(
        {
            "action": "diag.content_checks",
            "params": {"project_id": pid, "step": step} if step else {"project_id": pid},
            "title_ru": "Проверить ещё раз",
            "phrase_ru": f"проверь результат шага {STEP_RU.get(step, step)} в проекте {pid}",
        }
    )
    return out


def _cell_edits(finding: dict, pid: int) -> list[dict[str, Any]]:
    """Правки ячеек закадра, если они объясняют всё расхождение; иначе пусто."""
    if finding.get("check") != "vo_concat":
        return []
    fixes = [x for x in finding.get("fixes") or [] if x.get("uuid") and x.get("text")]
    if not fixes or len(fixes) > MAX_AUTO_EDITS or not _covers(finding, fixes):
        return []
    return [
        {
            "action": "shot.edit_cell_voiceover",
            "params": {"project_id": pid, "parent_uuid": x["uuid"], "voiceover": x["text"]},
            "title_ru": f"Вернуть кадру {x['number']} закадр из текста: «{_clip(x['text'], 60)}»",
            "phrase_ru": f"в проекте {pid} поставь закадр кадра {x['number']}: {x['text']}",
            "deterministic": True,
        }
        for x in fixes
    ]


def _covers(finding: dict, fixes: list[dict]) -> bool:
    """Правки кадров объясняют всё расхождение: потерь между кадрами и лишних кадров нет."""
    nums = {x.get("number") for x in fixes}
    others = set(finding.get("frames") or []) - nums
    return not others


def feedback_text(report: dict[str, Any]) -> str:
    """Что сказать модели при повторе шага: находки проверки содержания."""
    lines = [
        f"Проверка результата шага «{STEP_RU.get(cast(str, report.get('step')), report.get('step'))}» нашла ошибки:"
    ]
    for f in report.get("findings") or []:
        lines.append(f"- {f.get('title_ru')}: {f.get('detail_ru')}")
    return "\n".join(lines)


def summary_text(report: dict[str, Any]) -> str:
    step_ru = STEP_RU.get(cast(str, report.get("step")), report.get("step"))
    # Вопрос про промты: итог по героям — первым, даже если нашлось расхождение закадра.
    heroes = ""
    style = next((f for f in report.get("findings") or [] if f.get("check") == "prompt_style"), None)
    if report.get("step") == "img_pr":
        bad = [f for f in report.get("findings") or [] if f.get("check") == "prompt_characters"]
        heroes = (
            f"Герои в промтах картинок не совпали с кадрами (находок: {len(bad)})."
            if bad
            else "Герои в промтах картинок совпадают с персонажами кадров."
        )
    state = report.get("style") if isinstance(report.get("style"), dict) else None
    if style is None and state:
        if state.get("style"):
            heroes = (
                f"Стиль «{state['style']}» ({state['source']}) написан во всех {state['total']} промтах "
                "картинок: если картинки выходят без него, стиль теряется уже в генерации картинок "
                "(модель картинок или рефы), а не в промтах. " + heroes
            )
        else:
            heroes = (
                f"Ни в одном из {state['total']} промтов картинок нет слов стиля (пластилин, вязаный, "
                "нуар): стиль в промт сам не дописывается, его должен писать вариант промта картинок. "
                + heroes
            )
    if report.get("ok"):
        return " ".join(
            p for p in (heroes, f"Шаг «{step_ru}»: проверка содержания прошла, расхождений нет.") if p
        )
    parts = [p for p in ((style or {}).get("detail_ru"), heroes) if p]
    parts.append(f"Шаг «{step_ru}»: проверка содержания нашла ошибки (находок: {len(report['findings'])}).")
    ordered = sorted(
        (f for f in report["findings"] if f.get("check") != "prompt_style"),
        key=lambda f: f.get("check") != "prompt_characters",
    )
    for f in ordered[:6]:
        parts.append(f"{f['title_ru']}. {f.get('detail_ru') or ''}".strip())
    return " ".join(parts)
