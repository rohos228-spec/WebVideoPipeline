"""S3-совместимое хранилище: Cloudflare R2 и всё, что говорит на том же языке.

**Почему boto3, а не свой подписыватель.** SigV4 — это криптография, и её
ручная реализация ошибается тихо: подпись сходится на простых случаях и не
сходится на путях с символами, на пустых телах, на chunked. Библиотека
скучнее и правее.

**Почему синхронный клиент в потоке, а не aioboto3.** boto3 синхронный, и
вызов из петли событий её блокирует. Обёртка `asyncio.to_thread` — три строки
и ноль новых зависимостей; `aioboto3` — ещё одна библиотека в сопровождении
ради того же результата. Подпись ссылки в поток не уносится намеренно: она
считается локально, сети не касается, и уходить ради неё в тред дороже, чем
посчитать.

**Две настройки, которые кажутся мелочью и ломают всё.** Обе взяты из
`llm-gateway`, где за них уже заплачено:

* `force_path_style` — провайдеры без wildcard-DNS на поддомены бакета
  (Timeweb) требуют path-style; R2 работает virtual-hosted. Ошибка здесь
  выглядит как «бакет не найден» на совершенно рабочем бакете.
* Контрольные суммы «когда потребуется». Свежие SDK по умолчанию шлют
  заголовки, которых S3-совместимые (не-AWS) хранилища не ждут, и отвечают
  на них ошибкой подписи.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.storage.base import DEFAULT_URL_TTL_SEC, StorageError


class S3Storage:
    """Объекты в бакете. Ключ приходит уже с префиксом арендатора."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        force_path_style: bool = False,
        client: Any = None,
    ) -> None:
        self.bucket = bucket
        self.enabled = bool(endpoint and access_key and secret_key and bucket) or client is not None
        self._client = client
        self._config = {
            "endpoint": endpoint,
            "region": region,
            "access_key": access_key,
            "secret_key": secret_key,
            "force_path_style": force_path_style,
        }

    def client(self) -> Any:
        """Клиент создаётся при первом обращении и переиспользуется.

        Создавать его на импорте значит требовать ключи от каждого, кто
        импортировал модуль, — включая тесты и режим владельца, которым S3
        не нужен вовсе.
        """
        if self._client is not None:
            return self._client
        if not self.enabled:
            raise StorageError("объектное хранилище не настроено")
        import boto3
        from botocore.config import Config

        cfg = self._config
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            region_name=cfg["region"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            config=Config(
                s3={"addressing_style": "path" if cfg["force_path_style"] else "virtual"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        return self._client

    async def put_file(self, key: str, source: Path, *, content_type: str = "") -> str:
        extra = {"ContentType": content_type} if content_type else {}

        def _upload() -> None:
            self.client().upload_file(str(source), self.bucket, key, ExtraArgs=extra or None)

        try:
            await asyncio.to_thread(_upload)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"не удалось записать {key}: {exc}") from exc
        logger.debug("хранилище: записан {}", key)
        return key

    async def get_bytes(self, key: str) -> bytes:
        def _read() -> bytes:
            obj = self.client().get_object(Bucket=self.bucket, Key=key)
            return bytes(obj["Body"].read())

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"не удалось прочитать {key}: {exc}") from exc

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self.client().head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001 — «нет объекта» это тоже ответ
                return False

        return await asyncio.to_thread(_head)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self.client().delete_object(Bucket=self.bucket, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"не удалось удалить {key}: {exc}") from exc

    def url(self, key: str, *, ttl_sec: int = DEFAULT_URL_TTL_SEC, download_name: str = "") -> str:
        """Подписанная ссылка. Считается локально, сети не касается."""
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        try:
            return str(
                self.client().generate_presigned_url("get_object", Params=params, ExpiresIn=int(ttl_sec))
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"не удалось подписать ссылку на {key}: {exc}") from exc
