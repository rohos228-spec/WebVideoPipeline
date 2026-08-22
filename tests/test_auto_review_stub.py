"""W1-fix: STUB-fallback в auto_review.

auto_review раньше падал FileNotFoundError на чистом клоне (промпты
check_* под .gitignore). W1-fix коммитит STUB-default.md в репо с
маркером VP_CHECK_PROMPT_STUB; review_text / review_image должны
вернуть ReviewResult(status=skipped_stub) и НЕ звать GPT.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.models import HITLDecision, HITLKind
from app.services import auto_review


def test_is_stub_prompt_marks_marker_in_first_line() -> None:
    assert auto_review.is_stub_prompt("<!-- VP_CHECK_PROMPT_STUB: REPLACE BEFORE USE -->\n# body")
    assert auto_review.is_stub_prompt("\n\n  VP_CHECK_PROMPT_STUB  \n# body")
    assert not auto_review.is_stub_prompt("# real prompt\nVP_CHECK_PROMPT_STUB")
    assert not auto_review.is_stub_prompt("")
    assert not auto_review.is_stub_prompt("   \n  \n")


def test_get_check_prompt_path_returns_repo_default(tmp_path: Path) -> None:
    """Путь строится относительно корня репо, не cwd теста."""
    p = auto_review.get_check_prompt_path(HITLKind.approve_plan)
    assert p.name == "default.md"
    assert "prompts" in p.parts
    assert "check_plan" in p.parts


def test_load_check_prompt_returns_stub_for_committed_default() -> None:
    """Коммиченный default.md существует и помечен STUB."""
    text = auto_review.load_check_prompt(HITLKind.approve_plan)
    assert text  # не пусто
    assert auto_review.is_stub_prompt(text), (
        "ожидали STUB-маркер в коммиченном default.md (см. prompts/check_plan/default.md)"
    )


@pytest.mark.asyncio
async def test_review_text_returns_skipped_stub_without_calling_gpt() -> None:
    """На STUB-промте review_text не зовёт ChatGPT, отдаёт skipped_stub."""
    bot = AsyncMock()
    result = await auto_review.review_text(
        kind=HITLKind.approve_plan,
        artifact_text="# план\nсцена 1\nсцена 2",
        chatgpt_bot=bot,
    )
    bot.ask_fresh.assert_not_called()
    assert result.status == auto_review.REVIEW_STATUS_SKIPPED_STUB
    assert result.decision is HITLDecision.approved
    assert result.confidence == 0.0
    assert result.reasons and "CHECK_PROMPT_NOT_CONFIGURED" in result.reasons[0]
    assert "check_plan" in result.check_prompt_path


@pytest.mark.asyncio
async def test_review_image_returns_skipped_stub_without_calling_gpt(
    tmp_path: Path,
) -> None:
    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    bot = AsyncMock()
    result = await auto_review.review_image(
        kind=HITLKind.approve_hero,
        image_path=img,
        chatgpt_bot=bot,
    )
    bot.ask_with_file.assert_not_called()
    assert result.status == auto_review.REVIEW_STATUS_SKIPPED_STUB
    assert result.decision is HITLDecision.approved
    assert "check_hero" in result.check_prompt_path


def test_review_result_default_status_is_applied() -> None:
    """Без stub — обычный путь применяется (status='applied')."""
    r = auto_review.ReviewResult(decision=HITLDecision.approved, confidence=0.9)
    assert r.status == auto_review.REVIEW_STATUS_APPLIED
    assert r.check_prompt_path == ""
