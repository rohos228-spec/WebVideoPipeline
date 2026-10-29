"""«Кадр 8: ночное освещение» — свет кадра на доске, а не перерисовка картинки.

Планировщик ставил в очередь image_regen_correction с текстом «ночное освещение»: платная
перегенерация вместо бесплатной правки покрытия. Если человек не просил перерисовать
картинку, такой шаг меняется на montage.set_coverage (coverage_light).
"""

from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_REDRAW_OPS = frozenset({"image_regen_correction", "image_ai_change", "image_regen_prompt"})
_LIGHT_ONLY = re.compile(
    r"^\s*(ночн|дневн|закат|рассвет|контров|туман|холодн)\w*(?:\s+верхн\w*)?\s+(?:освещени\w*|свет\w*)\s*[.!]?\s*$"
    r"|^\s*(?:освещени\w*|свет)\s*[:—-]?\s*(ночн|дневн|закат|рассвет|контров|туман|холодн)\w*\s*[.!]?\s*$"
)
_REDRAW_WORDS = re.compile(r"перерис|картин|изображен|перегенер|сгенер")
_LIGHT_RU = {
    "ночн": "ночной",
    "дневн": "дневной",
    "закат": "закат",
    "рассвет": "рассвет",
    "контров": "контровой",
    "туман": "туман",
    "холодн": "холодный верхний",
}


def _light_of(op: dict) -> str | None:
    text = " ".join(str(op.get(key) or "") for key in ("correction", "instruction", "prompt")).lower()
    found = _LIGHT_ONLY.match(text.replace("ё", "е"))
    if not found:
        return None
    return _LIGHT_RU[found.group(1) or found.group(2)]


def light_as_coverage(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    if _REDRAW_WORDS.search(str(goal or "").lower()):
        return plan
    out: list[PlanStep] = []
    for step in plan:
        ops = step.params.get("ops") if step.action == "montage.queue_ops" else None
        if not isinstance(ops, list) or not ops:
            out.append(step)
            continue
        lights = [_light_of(op) if isinstance(op, dict) and op.get("type") in _REDRAW_OPS else None for op in ops]
        if not all(lights):
            out.append(step)
            continue
        for op, light in zip(ops, lights):
            out.append(
                PlanStep(
                    action="montage.set_coverage",
                    params={
                        "project_id": step.params.get("project_id"),
                        "frame_number": op.get("frame_number"),
                        "type": "coverage_light",
                        "value": light,
                    },
                    expected=f"Свет кадра {op.get('frame_number')}: {light}",
                    mutating=True,
                    risk="medium",
                )
            )
    return out
