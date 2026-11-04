"""Пауза и снятие паузы отвечают по-русски, без «статус: paused»."""

import pytest

from app.services.action_layer import builtin_wave3 as w3


class _Ctx:
    def __init__(self, status: str) -> None:
        self.status = status

    async def call(self, method: str, path: str) -> dict:
        return {"status": self.status}


@pytest.mark.asyncio
async def test_pause_text_says_how_to_resume() -> None:
    out = await w3._pause(_Ctx("paused"), {"project_id": 20})
    assert out["text_ru"] == "Проект #20 на паузе. Снять паузу можно просьбой «сними паузу с проекта 20»."


@pytest.mark.asyncio
async def test_resume_text_has_no_raw_status() -> None:
    out = await w3._resume(_Ctx("new"), {"project_id": 20})
    assert out["resumed"] and "Пауза с проекта #20 снята" in out["text_ru"]
    assert "«new»" not in out["text_ru"]
