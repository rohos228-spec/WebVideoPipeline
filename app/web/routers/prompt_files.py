"""REST: библиотека промтов — база первая, диск вторая.

Раньше роутер жил на диске: список — `ls prompts/<step>/`, запись — файл,
история — `.history/`. Это режим владельца. На сервере каталог смонтирован
только для чтения (`deploy/studio/docker-compose.yml`), и всё это давало 500.

Хуже: даже на записываемом диске сохранение отсюда НЕ меняло то, чем
пользуется шаг. `read_prompt` идёт «база, потом диск» (`prompt_store`), кэш
поднят при старте — а роутер писал файл и `MasterPrompt`, но не `prompt_store`.
Редактор говорил «сохранено», шаг читал старый текст.

Теперь каждая запись идёт в `prompt_store` (и в кэш, из которого читают
шаги), в `library_items` (версии — история отката, работает без диска) и,
если диск записываем, в файл — чтобы режим владельца продолжал видеть правки
в `prompts/`. Чтение — `read_prompt`, то есть та же цепочка, что у шагов:
редактор показывает ровно то, что пойдёт в модель.

Имя файла санитизируется через `prompt_library.is_valid_prompt_name` —
path traversal невозможен.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import local_library as lib
from app.services import prompt_store
from app.services.prompt_history import (
    bootstrap_saved_at_from_history,
    list_prompt_versions,
    read_prompt_version,
    rename_prompt_file,
    rename_prompt_version_label,
    write_prompt_with_history,
)
from app.services.prompt_library import (
    DEFAULT_NAME,
    PROMPT_SOURCE_LABELS,
    STEP_FOLDERS,
    delete_prompt,
    get_prompt_saved_at,
    is_excel_gpt_prompt_step,
    is_valid_prompt_name,
    list_prompts,
    prompt_path,
    prompts_writable,
    read_prompt,
    resolve_excel_gpt_prompt_path,
    resolve_project_prompt_with_source,
)
from app.web.deps import get_session

router = APIRouter(prefix="/prompt-files", tags=["prompt-files"])


@router.get("/global-active")
async def get_global_active_variants() -> dict[str, str]:
    """Последние активные .md по шагам (общие для всех проектов)."""
    from app.services.prompt_active_global import load_global_active

    return load_global_active()


class PromptFileInfo(BaseModel):
    name: str
    filename: str
    size: int
    modified: float | None
    is_default: bool


class PromptFileContent(BaseModel):
    name: str
    filename: str
    content: str
    size: int
    modified: float | None


class PromptResolveInfo(BaseModel):
    name: str
    source: str
    source_label: str
    modified: float | None


class PromptFileSavePayload(BaseModel):
    content: str


class PromptVersionInfo(BaseModel):
    id: str
    label: str
    saved_at: float
    size: int


class PromptVersionContent(BaseModel):
    id: str
    label: str
    content: str
    saved_at: float
    size: int


class PromptRenamePayload(BaseModel):
    new_name: str


class PromptVersionLabelPayload(BaseModel):
    label: str


def _ensure_step(step_code: str) -> None:
    if step_code not in STEP_FOLDERS:
        raise HTTPException(
            status_code=404,
            detail=f"step '{step_code}' has no prompt folder",
        )


def _ensure_name(name: str) -> None:
    if not is_valid_prompt_name(name):
        raise HTTPException(status_code=400, detail=f"invalid prompt name: {name!r}")


def _disk_prompt_path(step_code: str, name: str) -> Path:
    """Путь к .md на диске (для excel_gpt — unified + legacy enrich_*)."""
    if is_excel_gpt_prompt_step(step_code):
        return resolve_excel_gpt_prompt_path(name)
    return prompt_path(step_code, name)


def _library_prompt_path(step_code: str, name: str) -> str:
    return (Path("prompts") / STEP_FOLDERS[step_code] / f"{name}.md").as_posix()


def _prompt_modified(step_code: str, name: str, p: Path) -> float | None:
    return get_prompt_saved_at(step_code, name)


_DB_VERSION_PREFIX = "db-"


def _read_only_detail() -> str:
    return (
        "библиотека на диске только для чтения — на сервере промты правятся в базе; "
        "переименование файлов доступно только в режиме владельца"
    )


def _content_of(step_code: str, name: str) -> str:
    try:
        return read_prompt(step_code, name)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail="prompt not found") from e


def _info(step_code: str, name: str, body: str) -> PromptFileContent:
    p = prompt_path(step_code, name)
    return PromptFileContent(
        name=name,
        filename=f"{name}.md",
        content=body,
        size=len(body.encode("utf-8")),
        modified=_prompt_modified(step_code, name, p),
    )


async def _apply_save(
    session: AsyncSession,
    step_code: str,
    name: str,
    content: str,
    *,
    message: str,
    source: str,
) -> PromptFileContent:
    """Одна запись — во все места, где промт живёт.

    Порядок важен: сначала `prompt_store` — это то, что читают шаги, и если
    упадёт диск, шаг всё равно получит новый текст. Диск — только если он
    записываем; иначе молча пропускаем, а не 500.
    """
    await prompt_store.save(session, step_code, name, content)
    if prompts_writable():
        try:
            write_prompt_with_history(step_code, name, content)
        except OSError:
            # `prompts_writable` смотрит на корень; отдельная папка может
            # оказаться чужой по правам. Диск здесь вторичен.
            pass
    file_path = _library_prompt_path(step_code, name)
    await lib.create_or_update_item(
        session,
        kind="prompt",
        key=file_path,
        title=name,
        file_path=file_path,
        content=content,
        message=message,
        author="studio",
        source=source,
        meta={"step_code": step_code, "name": name},
        force_version=True,
    )
    from app.services.prompts import sync_step_prompt_to_db

    await sync_step_prompt_to_db(session, step_code, content)
    await session.commit()
    return _info(step_code, name, content)


async def _library_item(session: AsyncSession, step_code: str, name: str):
    return await lib.get_item_by_key(session, "prompt", _library_prompt_path(step_code, name))


async def _db_version(session: AsyncSession, step_code: str, name: str, version_id: str):
    """(item, версия) по id вида `db-N`; 404, если нет."""
    item = await _library_item(session, step_code, name)
    if item is None:
        raise HTTPException(status_code=404, detail="version not found")
    wanted = version_id[len(_DB_VERSION_PREFIX) :]
    row = next((v for v in await lib.list_versions(session, item.id) if str(v.version) == wanted), None)
    if row is None:
        raise HTTPException(status_code=404, detail="version not found")
    return item, row


_SOURCE_LABELS = {
    "prompt_files": "правка",
    "prompt_files_restore": "откат",
    "prompt_files_upload": "загрузка",
    "restore": "откат",
}


def _db_version_row(v) -> dict:
    # Подпись версии — по-человечески. Служебное «save prompt file plan/default»
    # в списке истории читается как мусор; если человек дал свою метку
    # (PATCH history), она побеждает.
    label = (v.message or "").strip()
    if not label or label.startswith(("save prompt file", "upload prompt file", "restore ")):
        label = _SOURCE_LABELS.get(v.source or "", "") or f"версия {v.version}"
    return {
        "id": f"{_DB_VERSION_PREFIX}{v.version}",
        "label": label,
        "saved_at": v.created_at.timestamp() if v.created_at else 0.0,
        "size": len((v.content or "").encode("utf-8")),
    }


async def _versions(session: AsyncSession, step_code: str, name: str) -> list[dict]:
    """История: версии из базы (всегда) плюс снимки с диска (если есть).

    В базе `create_or_update_item(force_version=True)` оставляет версию на
    каждое сохранение, так что откат работает и на сервере без диска. Текущая
    версия в список не идёт: «вернуть» к ней — пустое действие.
    """
    out: list[dict] = []
    item = await _library_item(session, step_code, name)
    if item is not None:
        for v in await lib.list_versions(session, item.id):
            if v.version != item.active_version:
                out.append(_db_version_row(v))
    try:
        out.extend(list_prompt_versions(step_code, name))
    except OSError:
        pass
    out.sort(key=lambda r: float(r.get("saved_at") or 0), reverse=True)
    return out


@router.get("/{step_code}/resolve", response_model=PromptResolveInfo)
async def resolve_prompt_for_project(
    step_code: str,
    project_id: int | None = Query(None),
    node_key: str | None = Query(None),
    slot_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> PromptResolveInfo:
    from app.models import Project

    _ensure_step(step_code)

    # Без проекта — общий уровень: «какой вариант возьмёт шаг, если проектных
    # переопределений нет». Спрашивает конструктор конвейера: там правится
    # промт узла, а не ролика, и подставлять туда чужой проект было бы враньём.
    #
    # Раньше параметр был обязательным, и такой вызов возвращал 422 — то есть
    # ветка просто не работала, а в интерфейсе стояло «промт не читается».
    overrides: dict = {}
    meta: dict = {}
    if project_id is not None:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        overrides = project.prompt_overrides if isinstance(project.prompt_overrides, dict) else {}
        meta = project.meta if isinstance(project.meta, dict) else {}
    name, source = resolve_project_prompt_with_source(
        overrides,
        step_code,
        meta=meta,
        node_key=node_key,
        slot_id=slot_id,
    )
    p = prompt_path(step_code, name)
    return PromptResolveInfo(
        name=name,
        source=source,
        source_label=PROMPT_SOURCE_LABELS.get(source, source),
        modified=_prompt_modified(step_code, name, p) if p.exists() else None,
    )


@router.get("/{step_code}", response_model=list[PromptFileInfo])
async def list_prompt_files(step_code: str) -> list[PromptFileInfo]:
    """Варианты промта шага: из базы и с диска, одним списком."""
    _ensure_step(step_code)
    try:
        bootstrap_saved_at_from_history(step_code)
    except OSError:
        pass  # диск только для чтения — мету не проставить, и не надо
    names: list[str] = list(prompt_store.list_names(step_code))
    try:
        for n in list_prompts(step_code):
            if n not in names:
                names.append(n)
    except (OSError, ValueError):
        pass
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    out: list[PromptFileInfo] = []
    for name in names:
        try:
            body = read_prompt(step_code, name)
        except (FileNotFoundError, ValueError):
            continue
        p = (
            resolve_excel_gpt_prompt_path(name)
            if is_excel_gpt_prompt_step(step_code)
            else prompt_path(step_code, name)
        )
        out.append(
            PromptFileInfo(
                name=name,
                filename=f"{name}.md",
                size=len(body.encode("utf-8")),
                modified=_prompt_modified(step_code, name, p),
                is_default=(name == DEFAULT_NAME),
            )
        )
    return out


@router.get("/{step_code}/{name}/content", response_model=PromptFileContent)
async def get_prompt_file(step_code: str, name: str) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    return _info(step_code, name, _content_of(step_code, name))


@router.get("/{step_code}/{name}/download")
async def download_prompt_file(step_code: str, name: str) -> FileResponse:
    _ensure_step(step_code)
    _ensure_name(name)
    p = _disk_prompt_path(step_code, name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="prompt file not found")
    return FileResponse(
        path=str(p),
        filename=f"{name}.md",
        media_type="text/markdown; charset=utf-8",
    )


@router.put("/{step_code}/{name}", response_model=PromptFileContent)
async def save_prompt_file(
    step_code: str,
    name: str,
    payload: PromptFileSavePayload,
    session: AsyncSession = Depends(get_session),
) -> PromptFileContent:
    _ensure_step(step_code)
    _ensure_name(name)
    return await _apply_save(
        session,
        step_code,
        name,
        payload.content,
        message=f"save prompt file {step_code}/{name}",
        source="prompt_files",
    )


@router.delete("/{step_code}/{name}")
async def delete_prompt_file(
    step_code: str,
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    _ensure_step(step_code)
    _ensure_name(name)
    if name == DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="default удалять нельзя")
    item = await _library_item(session, step_code, name)
    # Снимается свой уровень в базе и файл на диске, если он есть и диск
    # записываем. Системный промт из области арендатора не удаляется по
    # построению — `drop` видит только свою строку.
    removed = await prompt_store.drop(session, step_code, name)
    if prompts_writable():
        try:
            removed = delete_prompt(step_code, name) or removed
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError:
            pass
    if not removed:
        raise HTTPException(status_code=404, detail="на вашем уровне такого промта нет")
    await lib.log_event(
        session,
        "deleted",
        item=item,
        payload={"step_code": step_code, "name": name, "file_path": _library_prompt_path(step_code, name)},
    )
    await session.commit()
    return {"removed": True}


@router.get("/{step_code}/{name}/history", response_model=list[PromptVersionInfo])
async def list_prompt_file_history(
    step_code: str, name: str, session: AsyncSession = Depends(get_session)
) -> list[PromptVersionInfo]:
    _ensure_step(step_code)
    _ensure_name(name)
    return [PromptVersionInfo(**row) for row in await _versions(session, step_code, name)]


@router.get("/{step_code}/{name}/history/{version_id}/content", response_model=PromptVersionContent)
async def get_prompt_file_history_content(
    step_code: str, name: str, version_id: str, session: AsyncSession = Depends(get_session)
) -> PromptVersionContent:
    _ensure_step(step_code)
    _ensure_name(name)
    if version_id.startswith(_DB_VERSION_PREFIX):
        _, row = await _db_version(session, step_code, name, version_id)
        meta = _db_version_row(row)
        return PromptVersionContent(
            id=version_id,
            label=meta["label"],
            content=row.content,
            saved_at=meta["saved_at"],
            size=meta["size"],
        )
    try:
        content = read_prompt_version(step_code, name, version_id)
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    disk_meta = next((v for v in list_prompt_versions(step_code, name) if v["id"] == version_id), None)
    if disk_meta is None:
        raise HTTPException(status_code=404, detail="version not found")
    return PromptVersionContent(
        id=version_id,
        label=str(disk_meta["label"]),
        content=content,
        saved_at=float(disk_meta["saved_at"]),
        size=len(content.encode("utf-8")),
    )


@router.patch("/{step_code}/{name}/history/{version_id}", response_model=PromptVersionInfo)
async def rename_prompt_file_history_label(
    step_code: str,
    name: str,
    version_id: str,
    payload: PromptVersionLabelPayload,
    session: AsyncSession = Depends(get_session),
) -> PromptVersionInfo:
    _ensure_step(step_code)
    _ensure_name(name)
    label = payload.label.strip()
    if version_id.startswith(_DB_VERSION_PREFIX):
        _, row = await _db_version(session, step_code, name, version_id)
        row.message = label
        await session.commit()
        return PromptVersionInfo(**_db_version_row(row))
    try:
        row_d = rename_prompt_version_label(step_code, name, version_id, label)
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return PromptVersionInfo(**row_d)


@router.post("/{step_code}/{name}/history/{version_id}/restore")
async def restore_prompt_file_history(
    step_code: str, name: str, version_id: str, session: AsyncSession = Depends(get_session)
) -> PromptFileContent:
    """Откат — это обычное сохранение старого текста.

    Так он проходит тот же путь, что и правка: попадает в `prompt_store`,
    оставляет свою версию в истории и, если диск записываем, ложится в файл.
    Отдельный «особый» откат мимо базы возвращал бы файл, а шаг читал бы
    прежний текст из кэша — ровно та поломка, ради которой роутер переписан.
    """
    _ensure_step(step_code)
    _ensure_name(name)
    if version_id.startswith(_DB_VERSION_PREFIX):
        _, row = await _db_version(session, step_code, name, version_id)
        content = row.content
    else:
        try:
            content = read_prompt_version(step_code, name, version_id)
        except (FileNotFoundError, OSError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return await _apply_save(
        session,
        step_code,
        name,
        content,
        message=f"restore {version_id}",
        source="prompt_files_restore",
    )


@router.patch("/{step_code}/{name}/rename", response_model=PromptFileInfo)
async def rename_prompt_file_route(step_code: str, name: str, payload: PromptRenamePayload) -> PromptFileInfo:
    _ensure_step(step_code)
    _ensure_name(name)
    new_name = payload.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name required")
    if not prompts_writable():
        # Переименование — операция над файлом и его историей на диске. В базе
        # промт живёт под именем-ключом, и честнее сказать «недоступно», чем
        # переименовать файл, которого нет.
        raise HTTPException(status_code=409, detail=_read_only_detail())
    try:
        final = rename_prompt_file(step_code, name, new_name)
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    body = _content_of(step_code, final)
    return PromptFileInfo(
        name=final,
        filename=f"{final}.md",
        size=len(body.encode("utf-8")),
        modified=_prompt_modified(step_code, final, prompt_path(step_code, final)),
        is_default=(final == DEFAULT_NAME),
    )


@router.post("/{step_code}/upload", response_model=PromptFileInfo)
async def upload_prompt_file(
    step_code: str,
    file: UploadFile = File(...),
    name: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> PromptFileInfo:
    """Загрузить .md как новый вариант промта. Это обычное сохранение."""
    _ensure_step(step_code)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    raw_name = name or file.filename
    if raw_name.lower().endswith(".md"):
        raw_name = raw_name[:-3]
    raw_name = raw_name.strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="empty prompt name")
    if not is_valid_prompt_name(raw_name):
        raise HTTPException(
            status_code=400,
            detail=("имя промта содержит запрещённые символы или превышает 255 байт UTF-8"),
        )
    blob = await file.read()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="file must be utf-8 text") from e
    saved = await _apply_save(
        session,
        step_code,
        raw_name,
        text,
        message=f"upload prompt file {step_code}/{raw_name}",
        source="prompt_files_upload",
    )
    return PromptFileInfo(
        name=raw_name,
        filename=saved.filename,
        size=saved.size,
        modified=saved.modified,
        is_default=(raw_name == DEFAULT_NAME),
    )
