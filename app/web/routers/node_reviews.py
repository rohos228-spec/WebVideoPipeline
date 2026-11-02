"""Обзоры нод для профиля: `/api/node-reviews` (минимальный контракт под UI)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/node-reviews", tags=["node-reviews"])


class NodeReview(BaseModel):
    id: str
    title: str = ""
    body: str = ""
    rating: int = 0


class NodeReviewsOut(BaseModel):
    items: list[NodeReview] = Field(default_factory=list)


@router.get("", response_model=NodeReviewsOut)
async def list_node_reviews() -> NodeReviewsOut:
    """Пока пустой список — страница профиля не получает 404."""
    return NodeReviewsOut(items=[])
