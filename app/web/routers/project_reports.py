"""Отчёты по проектам: скачивание сводных таблиц и HTML."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(tags=["reports"])

SHARED_PREFIX = "projects_report_"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.(html|xlsx)$")


def shared_report_url(path: Path) -> str:
    return f"/api/projects/reports/{path.name}"


def report_url(project_id: int, path: Path) -> str:
    return f"/api/projects/{project_id}/reports/{path.name}"


@router.get("/projects/reports/{file_name}")
async def get_shared_report(file_name: str) -> Response:
    if not file_name.startswith(SHARED_PREFIX) or not _SAFE_NAME.match(file_name):
        raise HTTPException(404, "Отчёт не найден")
    reports_dir = (Path(settings.data_dir) / "reports").resolve()
    path = (reports_dir / file_name).resolve()
    if path.parent != reports_dir or not path.is_file():
        raise HTTPException(404, "Отчёт не найден")
    media_type = (
        "text/html; charset=utf-8"
        if file_name.endswith(".html")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    headers = {"content-security-policy": "sandbox"} if file_name.endswith(".html") else {}
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/projects/{project_id}/reports/{file_name}")
async def get_project_report(
    project_id: int, file_name: str, session: AsyncSession = Depends(get_session)
) -> Response:
    if not _SAFE_NAME.match(file_name):
        raise HTTPException(404, "Отчёт не найден")
    proj = await session.get(Project, project_id)
    if proj is None:
        raise HTTPException(404, "Проект не найден")
    project_dir = Path(proj.data_dir).resolve()
    reports_dir = (project_dir / "reports").resolve()
    path = (reports_dir / file_name).resolve()
    if path.parent != reports_dir or not path.is_file():
        raise HTTPException(404, "Отчёт не найден")
    media_type = (
        "text/html; charset=utf-8"
        if file_name.endswith(".html")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    headers = {"content-security-policy": "sandbox"} if file_name.endswith(".html") else {}
    return FileResponse(path, media_type=media_type, headers=headers)
