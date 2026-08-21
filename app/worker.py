"""LEGACY-точка входа воркера — ВЫВЕДЕНА из эксплуатации (этап 2, F.1).

Собственный poll-цикл этого модуля удалён: он держал СВОЙ урезанный
список статусов (без scene_*/sfx/music — §9#2 карты) и конкурировал с
каноническим воркером — двойное исполнение шагов.

Канонический путь один: ``python -m app.main`` (бот + воркер + web в одном
процессе; воркер-синглтон — ``pipeline_worker.ensure_pipeline_worker_started``).
"""

from __future__ import annotations

import sys

_MSG = (
    "app.worker выведен из эксплуатации (этап 2, cache-resume): "
    "второй poll-цикл со своим списком статусов давал двойное исполнение "
    "шагов. Запускай канонический процесс: python -m app.main"
)


def main() -> None:
    raise SystemExit(_MSG)


if __name__ == "__main__":
    print(_MSG, file=sys.stderr)
    sys.exit(2)
