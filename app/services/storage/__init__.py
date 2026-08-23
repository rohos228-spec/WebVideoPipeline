"""Хранилище объектов: одно на процесс, выбирается настройками.

Локальное или S3-совместимое — решает наличие ключей доступа, а не флаг.
Флаг разошёлся бы с реальностью: включён, а ключей нет, и приложение падает
на первом же кадре.

Разбор контракта и решений — `app/services/storage/base.py`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.services.storage.base import (
    DEFAULT_URL_TTL_SEC,
    STUDIO_PREFIX,
    Storage,
    StorageError,
    safe_path,
    tenant_key,
)
from app.services.storage.local import LocalStorage
from app.services.storage.s3 import S3Storage

__all__ = [
    "DEFAULT_URL_TTL_SEC",
    "STUDIO_PREFIX",
    "LocalStorage",
    "S3Storage",
    "Storage",
    "StorageError",
    "get_storage",
    "reset_storage",
    "safe_path",
    "tenant_key",
]


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """Хранилище этого процесса."""
    from app.settings import settings

    if settings.s3_configured:
        return S3Storage(
            endpoint=settings.s3_endpoint.strip(),
            region=settings.s3_region.strip() or "auto",
            access_key=settings.s3_access_key.strip(),
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket.strip(),
            force_path_style=settings.s3_force_path_style,
        )
    return LocalStorage(Path(settings.data_dir) / "objects")


def reset_storage() -> None:
    """Забыть выбранное хранилище. Нужно тестам, меняющим настройки."""
    get_storage.cache_clear()
