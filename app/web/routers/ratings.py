"""Оценки нод и промтов для профиля: `/api/ratings`."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.settings import settings

router = APIRouter(prefix="/ratings", tags=["ratings"])


class RatingsOut(BaseModel):
    default: int = 5
    ratings: dict[str, int] = Field(default_factory=dict)


def _ratings_path(kind: str) -> Path:
    name = "ratings-prompt.json" if kind == "prompt" else "ratings-node.json"
    return settings.data_dir / name


def _load(kind: str) -> RatingsOut:
    path = _ratings_path(kind)
    if not path.is_file():
        return RatingsOut()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RatingsOut()
    if not isinstance(data, dict):
        return RatingsOut()
    raw_ratings = data.get("ratings")
    ratings: dict = raw_ratings if isinstance(raw_ratings, dict) else {}
    cleaned = {str(k): int(v) for k, v in ratings.items() if isinstance(v, (int, float))}
    default = int(data.get("default", 5))
    return RatingsOut(default=default, ratings=cleaned)


@router.get("", response_model=RatingsOut)
async def get_ratings(kind: str = Query("node", pattern="^(node|prompt)$")) -> RatingsOut:
    """Фронт профиля читает оценки нод (`kind=node`) и промтов (`kind=prompt`)."""
    return _load(kind)
