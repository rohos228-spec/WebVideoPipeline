"""«Музыка для проекта 4 — файл C:\\...\\bgm.wav» — путь к файлу берётся из просьбы.

Планировщик то клал путь в source_path, то оставлял шаг загрузки без источника: карточка
показывала «подтвердить нельзя: укажите ровно один источник». Если в просьбе ровно один путь
к файлу, а у шага загрузки нет ни source_path, ни data_base64, путь подставляется.
"""

from __future__ import annotations

import re

from app.orchestrator.contracts import PlanStep

_UPLOADS = frozenset(
    {
        "montage.upload_voice",
        "montage.upload_music",
        "montage.upload_image",
        "montage.upload_video",
        "montage.upload_ref",
    }
)
_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s«»\"']*?\.(?:wav|mp3|m4a|ogg|flac|aac|png|jpe?g|webp|mp4|mov|webm)\b", re.I)


def upload_source_from_goal(plan: list[PlanStep], goal: str) -> list[PlanStep]:
    paths = list(dict.fromkeys(_PATH.findall(str(goal or ""))))
    if len(paths) != 1:
        return plan
    out: list[PlanStep] = []
    for step in plan:
        params = step.params
        if step.action in _UPLOADS and not params.get("source_path") and not params.get("data_base64"):
            step = step.model_copy(update={"params": {**params, "source_path": paths[0]}})
        out.append(step)
    return out
