"""Чей это файл на диске. Изоляция арендаторов там, где RLS не действует.

Row-level security закрывает базу. Файлы она не закрывает никак: медиа лежат
в `data/videos/<slug>/`, ручка `GET /api/files?path=…` отдаёт что угодно под
`data_dir`, а `data_dir` в SaaS общий на всех. Клиент с совершенно законным
токеном, узнав или угадав чужой слаг, забирает чужой ролик целиком — и это
не обходит политику, а идёт мимо неё.

Отсюда правило: **путь на диске обязан доказать, что принадлежит арендатору**.
Доказательство берётся из базы, где изоляция уже работает: путь
раскладывается до слага проекта, слаг ищется в `projects`, и если политика
эту строку не показала — файла для этого клиента не существует.

**Раскладка путей — часть контракта, а не догадка.** Она объявлена в
`Project.data_dir` и `Batch.data_dir`:

    data/videos/<slug>/…                        одиночный проект
    data/batches/<batch>/sub/<slug>/…           подпроект массового
    data/batches/<batch>/…                      сам массовый

Появится четвёртая раскладка — её нужно добавить сюда, иначе файлы окажутся
недоступны законному владельцу. Это правильный отказ: лучше не отдать своё,
чем отдать чужое.

**В режиме владельца проверка выключена целиком.** Арендаторов нет,
`data_dir` принадлежит одному человеку, и мешать ему открывать собственные
файлы незачем.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

#: Каталоги верхнего уровня, у которых владельца нет по природе: снапшоты
#: промтов, библиотека, журналы харнесса. В SaaS они не отдаются никому —
#: клиенту там нечего смотреть, а владельцу проверка не мешает.
_OWNERLESS_ROOTS: frozenset[str] = frozenset({"library", "prompts", "logs", "tmp"})


class ForeignFile(PermissionError):
    """Файл принадлежит другому арендатору либо никому."""


def project_slug_of(path: Path, data_dir: Path) -> str | None:
    """Слаг проекта, которому принадлежит путь. `None` — владельца нет.

    Разбор идёт по объявленной раскладке, а не по поиску слага где-нибудь в
    середине пути: совпадение по подстроке однажды пропустило бы
    `data/videos/чужой/…/мой/…` как свой.
    """
    try:
        rel = path.resolve().relative_to(data_dir.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    if parts[0] == "videos" and len(parts) >= 2:
        return parts[1]
    if parts[0] == "batches" and len(parts) >= 4 and parts[2] == "sub":
        return parts[3]
    return None


def batch_slug_of(path: Path, data_dir: Path) -> str | None:
    """Слаг массового проекта, если путь лежит в его папке."""
    try:
        rel = path.resolve().relative_to(data_dir.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "batches":
        return parts[1]
    return None


async def assert_readable(session: Any, path: Path) -> None:
    """Пустить к файлу или бросить `ForeignFile`. Режим владельца — всегда да.

    Проверка стоит в ручке отдачи файлов, а не в каждом вызывающем: путь
    приходит от клиента параметром, и это единственное место, где он
    попадает в систему извне.
    """
    from app.models import BatchProject, Project
    from app.services.tenant import current_tenant
    from app.settings import settings

    if current_tenant() is None:
        return

    data_dir = Path(settings.data_dir)
    slug = project_slug_of(path, data_dir)
    if slug is not None:
        # RLS уже отфильтровала: чужой проект просто не найдётся.
        found = (await session.execute(select(Project.id).where(Project.slug == slug).limit(1))).first()
        if found is None:
            raise ForeignFile(f"проект {slug!r} этому арендатору не принадлежит")
        return

    batch = batch_slug_of(path, data_dir)
    if batch is not None:
        found = (
            await session.execute(select(BatchProject.id).where(BatchProject.slug == batch).limit(1))
        ).first()
        if found is None:
            raise ForeignFile(f"массовый проект {batch!r} этому арендатору не принадлежит")
        return

    try:
        rel = path.resolve().relative_to(data_dir.resolve())
        root = rel.parts[0] if rel.parts else ""
    except ValueError:
        root = ""
    raise ForeignFile(
        f"путь не принадлежит ни одному проекту (корень {root!r})"
        if root in _OWNERLESS_ROOTS
        else "путь не принадлежит ни одному проекту"
    )
