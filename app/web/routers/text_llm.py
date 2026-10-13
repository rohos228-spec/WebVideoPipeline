"""Выбор текстовой LLM: vibecode (Claude/GPT/Gemini/Grok) + GPT (kie).

`models[]` в ответе несёт `group` — семейство модели; пикер Студии
группирует список по нему.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.text_llm_catalog import catalog_status, write_choice
from app.services.vibecode_catalog import grouped_catalog, markup_for_channel

router = APIRouter(prefix="/text-llm", tags=["text-llm"])


class TextLlmSelectBody(BaseModel):
    provider: str | None = Field(default=None, description="kie | vibecode (если опущен, сохраняется активный)")
    model_id: str | None = None
    reasoning_effort: str | None = Field(default=None, description="off | low | medium | high | xhigh")


@router.get("")
async def get_text_llm() -> dict[str, Any]:
    status = catalog_status()
    status["catalog"] = grouped_catalog()
    return status


@router.get("/catalog")
async def get_text_llm_catalog() -> dict[str, Any]:
    payload = grouped_catalog()
    payload["markup"] = markup_for_channel()
    return payload


@router.put("")
async def select_text_llm(body: TextLlmSelectBody) -> dict[str, Any]:
    try:
        write_choice(
            provider=body.provider,
            model_id=body.model_id,
            reasoning_effort=body.reasoning_effort,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return catalog_status() | {"catalog": grouped_catalog()}
