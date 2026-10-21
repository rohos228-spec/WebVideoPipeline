from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.web.deps import get_session
from app.web.engine_access import require_engine_operator

# /api/projects/** открыт участникам (TENANT_ALLOWED_PREFIXES), поэтому отчёты
# движка закрыты явной зависимостью: пока только админ.
router = APIRouter(
    prefix="/projects", tags=["reports"], dependencies=[Depends(require_engine_operator)]
)

REPORTS_DIR = "reports"
SHARED_PREFIX = "projects_report_"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,150}\.(xlsx|md|json|csv|html)$")
_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "html": "text/html; charset=utf-8",
}


def report_url(project_id: int, path: str | Path) -> str:
    return f"/api/projects/{int(project_id)}/reports/{Path(str(path)).name}"


def shared_report_url(path: str | Path) -> str:
    """Отчёт по нескольким проектам: файл в DATA_DIR/reports."""
    return f"/api/projects/{REPORTS_DIR}/{Path(str(path)).name}"


def report_file(data_dir: str | Path, name: str) -> Path:
    if not _NAME.fullmatch(name or "") or ".." in name:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    root = (Path(data_dir) / REPORTS_DIR).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return path


@router.get("/reports/{name}")
async def download_shared_report(name: str) -> FileResponse:
    from app.settings import settings

    if not str(name or "").startswith(SHARED_PREFIX):
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    path = report_file(settings.data_dir, name)
    return FileResponse(path, media_type=_MEDIA[path.suffix.lstrip(".").lower()], filename=path.name,
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{project_id}/reports/{name}")
async def download_report(
    project_id: int, name: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    path = report_file(project.data_dir, name)
    return FileResponse(path, media_type=_MEDIA[path.suffix.lstrip(".").lower()], filename=path.name,
                        headers={"Cache-Control": "private, no-store"})
