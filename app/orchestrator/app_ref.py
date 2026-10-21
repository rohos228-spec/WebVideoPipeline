from __future__ import annotations

from typing import Any

_APP: dict[str, Any] = {"app": None}


def bind(app: Any) -> None:
    _APP["app"] = app


def current() -> Any:
    return _APP["app"]
