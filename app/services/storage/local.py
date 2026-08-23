"""Локальное хранилище: тот же контракт, файлы на диске узла.

Это не заглушка на время разработки и не «пока не настроили S3». Это рабочий
режим владельца: одна машина, один человек, диск рядом. Конвейер и так пишет
файлы локально — ffmpeg монтирует с диска, а не из сети, — и класс лишь даёт
им тот же интерфейс, что и объектному хранилищу.

**Ссылка ведёт обратно в приложение.** Подписать локальный файл нечем, поэтому
`url()` отдаёт путь к ручке `/api/files`, которая уже умеет проверять и
границы каталога, и принадлежность арендатору (`app/services/file_scope.py`).
То есть в режиме владельца отдача остаётся ровно такой, какой была.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlencode

from app.services.storage.base import DEFAULT_URL_TTL_SEC, StorageError, safe_path


class LocalStorage:
    """Объекты под `data_dir/objects/<ключ>`."""

    enabled = True

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, key: str) -> Path:
        """Куда ляжет объект. Ключ санитизируется тем же кодом, что и в S3.

        Единая санитизация не косметика: два хранилища с разными правилами
        разъехались бы на первом же ключе с необычным символом, и разъехались
        бы молча — файл записан, а найти его нельзя.
        """
        return self.root / safe_path(key)

    async def put_file(self, key: str, source: Path, *, content_type: str = "") -> str:
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(source).resolve() == target.resolve():
            return key
        await asyncio.to_thread(shutil.copy2, str(source), str(target))
        return key

    async def get_bytes(self, key: str) -> bytes:
        target = self.path_for(key)
        if not target.is_file():
            raise StorageError(f"объекта нет: {key}")
        return await asyncio.to_thread(target.read_bytes)

    async def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    async def delete(self, key: str) -> None:
        target = self.path_for(key)
        if target.is_file():
            await asyncio.to_thread(target.unlink)

    def url(self, key: str, *, ttl_sec: int = DEFAULT_URL_TTL_SEC, download_name: str = "") -> str:
        """Ссылка на ручку отдачи. Срок здесь не при чём — проверяет доступ она."""
        params = {"path": str(self.path_for(key))}
        if download_name:
            params["download"] = "1"
        return f"/api/files?{urlencode(params)}"
