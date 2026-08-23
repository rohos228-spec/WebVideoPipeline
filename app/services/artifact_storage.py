"""Публикация артефактов: с диска узла в объектное хранилище.

Конвейер пишет файлы локально и будет писать дальше — ffmpeg монтирует с
диска, провайдеры отдают байты в файл, проверка кадра открывает его же. Задача
не в том, чтобы отобрать у конвейера локальный файл, а в том, чтобы у
результата появилась вторая, общая копия: видимая всем узлам и отдаваемая
клиенту напрямую, без участия воркера (`docs/SAAS-PIVOT.md` §9.2).

**Публикуется результат, а не всё подряд.** Промежуточные файлы шага — пробы,
кадры до одобрения, временные склейки — живут на узле и умирают вместе с ним.
В хранилище едет то, что клиент увидит: артефакт с записью в базе.

**Ключ строится из проекта и uuid артефакта, а не из имени файла.** Имена на
диске повторяются между проектами (`final.mp4` у всех), а uuid уникален и уже
есть. Плюс имя файла приходит от провайдера, то есть снаружи, — строить из
него ключ значит пускать чужую строку в адресацию хранилища.

**Ошибка публикации не роняет шаг.** Ролик уже сгенерирован и уже оплачен;
уронить шаг из-за недоступного бакета значит списать деньги и не отдать
результат. Ошибка пишется в журнал, ключ остаётся пустым, артефакт отдаётся с
диска — и следующая попытка публикации его подхватит.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.storage import StorageError, get_storage, tenant_key


def artifact_key(project_slug: str, artifact_uuid: str, filename: str) -> str:
    """Путь объекта внутри префикса арендатора.

    Расширение сохраняется: по нему клиент и браузер понимают, что это, а
    хранилище отдаёт правильный `Content-Type`.
    """
    suffix = Path(filename).suffix.lower()
    return f"projects/{project_slug}/{artifact_uuid}{suffix}"


async def publish_artifact(session: Any, artifact: Any) -> str:
    """Положить артефакт в хранилище и запомнить ключ. Возвращает ключ.

    Идемпотентна: артефакт с непустым `storage_key` считается опубликованным.
    Повторная заливка стоила бы трафика и ничего не меняла.
    """
    if getattr(artifact, "storage_key", ""):
        return str(artifact.storage_key)

    source = Path(str(artifact.path or ""))
    if not source.is_file():
        # Артефакт без файла — это запись о том, чего нет. Публиковать нечего,
        # и это не ошибка: файл мог быть удалён вручную или не доехать с узла.
        return ""

    from app.models import Project
    from app.services.tenant import current_tenant

    project = await session.get(Project, artifact.project_id)
    slug = getattr(project, "slug", None) or f"project-{artifact.project_id}"
    key = tenant_key(current_tenant(), artifact_key(slug, artifact.uuid, source.name))
    content_type = mimetypes.guess_type(source.name)[0] or ""

    try:
        await get_storage().put_file(key, source, content_type=content_type)
    except StorageError:
        logger.warning("хранилище: артефакт {} не опубликован — отдаём с диска", artifact.uuid, exc_info=True)
        return ""

    artifact.storage_key = key
    await session.flush()
    logger.info("хранилище: артефакт {} → {}", artifact.uuid, key)
    return key


async def publish_project_artifacts(session: Any, project_id: int) -> int:
    """Опубликовать всё неопубликованное по проекту. Возвращает число новых.

    Зовётся после успешного шага — в одном месте, а не из двадцати шести
    точек, где артефакты создаются. Забыть публикацию в одной из них значило
    бы потерять ровно один вид результата и заметить это по жалобе клиента.

    В режиме локального хранилища не делает ничего: копировать файл рядом с
    ним самим — это удвоенный диск без единой новой возможности.
    """
    from sqlalchemy import select

    from app.models import Artifact
    from app.settings import settings

    if not settings.s3_configured:
        return 0

    rows = (
        (
            await session.execute(
                select(Artifact).where(Artifact.project_id == project_id, Artifact.storage_key == "")
            )
        )
        .scalars()
        .all()
    )
    published = 0
    for artifact in rows:
        if await publish_artifact(session, artifact):
            published += 1
    if published:
        await session.commit()
    return published


async def artifact_url(artifact: Any, *, download: bool = False) -> str:
    """Куда отправить клиента за файлом.

    Опубликованный артефакт отдаётся подписанной ссылкой мимо приложения:
    ролик это десятки мегабайт, и каждый просмотр через FastAPI занимает
    воркер на всё время скачивания. Неопубликованный — прежней ручкой отдачи,
    и в режиме владельца это единственный путь.
    """
    key = str(getattr(artifact, "storage_key", "") or "")
    storage = get_storage()
    if key:
        name = Path(str(artifact.path or "")).name if download else ""
        return storage.url(key, download_name=name)
    from urllib.parse import urlencode

    params = {"path": str(artifact.path or "")}
    if download:
        params["download"] = "1"
    return f"/api/files?{urlencode(params)}"
