"""Логи импорта xlsx при превью: импорт откатывается, и «создан Frame» в логе - неправда.

Проверка промтов картинок (``img_pr_guard.xlsx_frames_preview``) прогоняет импорт project.xlsx
в откатываемой сессии, чтобы увидеть кадры, которые появятся в базе. Импорт пишет по строке
INFO на каждый кадр («xlsx→DB: создан Frame 3»), и в логе это выглядит как настоящая запись.
Внутри ``preview_logs()`` эти строки опускаются до DEBUG, помечаются превью и говорят
«прочитано» вместо «создан/обновлён». Вне превью ``log_info`` - обычный ``logger.info``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from loguru import logger

_PREVIEW: ContextVar[bool] = ContextVar("xlsx_import_preview", default=False)
PREVIEW_TAG = "[превью xlsx, в базу не пишется] "
_DONE_WORDS = re.compile(r"(?<!\w)(?:создан\w*|обновлен\w*|обновлён\w*|заполнен\w*|подтянут\w*)(?!\w)")


def in_preview() -> bool:
    return _PREVIEW.get()


@contextmanager
def preview_logs() -> Iterator[None]:
    token = _PREVIEW.set(True)
    try:
        yield
    finally:
        _PREVIEW.reset(token)


def preview_message(message: str) -> str:
    return PREVIEW_TAG + _DONE_WORDS.sub("прочитано", message)


def log_info(message: str, *args: Any, **kwargs: Any) -> None:
    if _PREVIEW.get():
        logger.opt(depth=1).debug(preview_message(message), *args, **kwargs)
    else:
        logger.opt(depth=1).info(message, *args, **kwargs)
