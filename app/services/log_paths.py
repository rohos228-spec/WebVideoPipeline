"""Пути к текстовым журналам (`logs/status.log`, `logs/errors.log`).

Раньше каждый писатель держал у себя `Path("logs/errors.log")` — константу,
посчитанную на импорте и относительную к CWD. Следствий два:

* прогон тестов дописывал НАСТОЯЩИЕ логи репозитория (autouse-фикстура
  подменяет `settings.data_dir`, но про эти константы ничего не знала);
* запуск не из корня репозитория раскидывал `logs/` где попало.

Теперь путь считается на каждый вызов из `settings.logs_dir`, который
резолвится от корня проекта и переопределяется `LOGS_DIR`.
"""

from __future__ import annotations

from pathlib import Path

from app.settings import settings


def logs_dir() -> Path:
    return Path(settings.logs_dir)


def status_log_path() -> Path:
    """Журнал переходов статусов нод (`node_status_machine`)."""
    return logs_dir() / "status.log"


def errors_log_path() -> Path:
    """Общий журнал ошибок ботов (outsee / chatgpt / elevenlabs)."""
    return logs_dir() / "errors.log"
