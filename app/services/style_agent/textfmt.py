"""Человекочитаемый рендер значений паспорта (без сырых dict/list repr)."""

from __future__ import annotations

from typing import Any


def _tc(t: Any) -> str:
    try:
        t = float(t)
    except (TypeError, ValueError):
        return ""
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:05.2f}"


def human(v: Any, *, sep: str = "; ") -> str:
    """dict/list → читаемая строка. Известные формы:
    {template, example} → «шаблон» (пример: …); {quote, start, end} → «цитата» [tc];
    {name, description} → name — description; {video: freq} → video: freq.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        t = v.strip()
        if t[:2] in ("{'", '{"', "[{", "['"):
            try:
                import ast

                parsed = ast.literal_eval(t)
            except (ValueError, SyntaxError):
                return t
            if isinstance(parsed, (dict, list)):
                return human(parsed, sep=sep)
        return t
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float)):
        return f"{v:g}" if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return sep.join(x for x in (human(i, sep=", ") for i in v) if x)
    if isinstance(v, dict):
        if "template" in v:
            ex = human(v.get("example"))
            return str(v.get("template") or "").strip() + (f" (пример: {ex})" if ex else "")
        if "quote" in v:
            tc = ""
            if v.get("start") is not None:
                tc = f" [{_tc(v.get('start'))}" + (f"–{_tc(v.get('end'))}" if v.get("end") is not None else "") + "]"
            return f"«{str(v.get('quote') or '').strip()}»{tc}"
        if "name" in v and ("description" in v or "how_to_reproduce" in v):
            return f"{v.get('name')} — {human(v.get('description') or v.get('how_to_reproduce'))}"
        if "text" in v and ("start" in v or "tc" in v):
            tc = v.get("tc") or (_tc(v.get("start")) if v.get("start") is not None else "")
            return f"«{v.get('text')}»" + (f" [{tc}]" if tc else "")
        if "video" in v and "tc" in v:
            return f"{v.get('video')} {v.get('tc')}".strip()
        return sep.join(f"{k}: {human(x, sep=', ')}" for k, x in v.items() if x not in (None, "", [], {}))
    return str(v)
