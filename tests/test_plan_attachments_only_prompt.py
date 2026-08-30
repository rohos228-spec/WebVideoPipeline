"""К запросу плана прикладывается только промт — книга больше не идёт контекстом.

Для нового ролика `project.xlsx` — пустой шаблон, а сопроводительный текст про
«лист „Общий план“» разводил модель между таблицей и apply-ops: она то
возвращала .xlsx, то JSON. Источник правды — база, Excel остаётся на экспорт.

Тест держит состав вложений и то, что в сопроводительном тексте не осталось
требования вернуть книгу.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Project
from app.services import xlsx_step_runners as xsr


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(slug="plan", topic="Как работает ГЭС", hero_mode="no_hero")
    p.id = 5
    return p


@pytest.mark.asyncio
async def test_plan_sends_prompt_file_without_the_workbook(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def _ask(chat_msg: str, attachments: list[Path], **kw: object) -> str:
        seen["chat"] = chat_msg
        seen["attachments"] = list(attachments)
        return '{"ops":[{"target":"project","fields":{"общий_план":"' + "План на сто знаков. " * 30 + '"}}]}'

    async def _lock(_pid: int, _name: str, fn: object) -> str:
        return await fn()  # type: ignore[operator]

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)

    result = await xsr.run_plan_xlsx(project)

    attachments = seen["attachments"]
    assert isinstance(attachments, list) and len(attachments) == 1
    only = attachments[0]
    assert only.suffix == ".txt", "приложен промт, а не книга"
    assert not any(p.suffix == ".xlsx" for p in attachments)
    assert result.plan_text.startswith("План на сто знаков")


@pytest.mark.asyncio
async def test_accompanying_text_does_not_ask_for_a_file_back(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    async def _ask(chat_msg: str, attachments: list[Path], **kw: object) -> str:
        seen["chat"] = chat_msg
        return '{"ops":[{"target":"project","fields":{"общий_план":"' + "Текст плана. " * 50 + '"}}]}'

    async def _lock(_pid: int, _name: str, fn: object) -> str:
        return await fn()  # type: ignore[operator]

    monkeypatch.setattr(xsr.xgf, "telegram_style_ask_with_files", _ask)
    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", _lock)
    await xsr.run_plan_xlsx(project)

    chat = seen["chat"]
    assert ".xlsx" not in chat.lower()
    assert "apply-ops" in chat or "ops" in chat
