"""Чек-промт не настроен → контуры проверок скипаются, а не auto-approve.

Чек-промты `prompts/check_*/` под .gitignore (специфичны для заказчика).
На чистом клоне файла нет: раньше это был FileNotFoundError и молча
мёртвый контур. Теперь `load_check_prompt` отдаёт встроенную заглушку с
маркером `VP_CHECK_PROMPT_STUB`, и КАЖДЫЙ вызывающий контур обязан её
распознать и вернуть `status=skipped_stub`, не трогая GPT и не двигая
pipeline.

Ключевой инвариант (тест `test_apply_review_result_*`): ReviewResult со
`skipped_*` несёт `decision=approved` — одна пропущенная проверка
статуса в `_apply_review_result` превращает «промт не настроен» в
«одобрено», т.е. в auto-approve брака.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.models import HITLDecision, HITLKind, ProjectStatus
from app.orchestrator import auto_advance
from app.services import auto_review


def test_is_stub_prompt_marks_marker_in_first_line() -> None:
    assert auto_review.is_stub_prompt("<!-- VP_CHECK_PROMPT_STUB: REPLACE BEFORE USE -->\n# body")
    assert auto_review.is_stub_prompt("\n\n  VP_CHECK_PROMPT_STUB  \n# body")
    # Маркер не в первой непустой строке — это боевой промт, он упоминает
    # маркер в тексте. Скипать нельзя.
    assert not auto_review.is_stub_prompt("# real prompt\nVP_CHECK_PROMPT_STUB")
    assert not auto_review.is_stub_prompt("")
    assert not auto_review.is_stub_prompt("   \n  \n")


def test_get_check_prompt_path_returns_repo_default() -> None:
    """Путь строится относительно корня репо, не cwd теста."""
    p = auto_review.get_check_prompt_path(HITLKind.approve_plan)
    assert p.name == "default.md"
    assert "prompts" in p.parts
    assert "check_plan" in p.parts


def test_load_check_prompt_falls_back_to_builtin_stub(monkeypatch, tmp_path: Path) -> None:
    """Файла нет → встроенная заглушка, не исключение."""
    monkeypatch.setattr(auto_review, "PROMPTS_ROOT", tmp_path / "prompts")
    text = auto_review.load_check_prompt(HITLKind.approve_plan)
    assert auto_review.is_stub_prompt(text)


def test_load_check_prompt_reads_real_file_when_present(monkeypatch, tmp_path: Path) -> None:
    """Боевой промт на месте → читается он, заглушка не подменяет."""
    root = tmp_path / "prompts" / "check_plan"
    root.mkdir(parents=True)
    (root / "default.md").write_text("# боевой промт\nпроверь план", encoding="utf-8")
    monkeypatch.setattr(auto_review, "PROMPTS_ROOT", tmp_path / "prompts")
    text = auto_review.load_check_prompt(HITLKind.approve_plan)
    assert text.startswith("# боевой промт")
    assert not auto_review.is_stub_prompt(text)


@pytest.mark.asyncio
async def test_review_text_returns_skipped_stub_without_calling_gpt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(auto_review, "PROMPTS_ROOT", tmp_path / "prompts")
    bot = AsyncMock()
    result = await auto_review.review_text(
        kind=HITLKind.approve_plan,
        artifact_text="# план\nсцена 1\nсцена 2",
        chatgpt_bot=bot,
    )
    bot.ask_fresh.assert_not_called()
    assert result.status == auto_review.REVIEW_STATUS_SKIPPED_STUB
    assert result.confidence == 0.0
    assert result.reasons and "CHECK_PROMPT_NOT_CONFIGURED" in result.reasons[0]
    assert "check_plan" in result.check_prompt_path


@pytest.mark.asyncio
async def test_review_image_returns_skipped_stub_without_calling_gpt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(auto_review, "PROMPTS_ROOT", tmp_path / "prompts")
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
    assert "check_hero" in result.check_prompt_path


def test_review_result_default_status_is_applied() -> None:
    r = auto_review.ReviewResult(decision=HITLDecision.approved, confidence=0.9)
    assert r.status == auto_review.REVIEW_STATUS_APPLIED
    assert r.check_prompt_path == ""


# ============================================================
# Главный инвариант: skipped_* НЕ применяется
# ============================================================


class _FakeProject:
    """Минимальный стенд: `_apply_review_result` логирует project.id."""

    id = 1
    meta: dict = {}


def _transition() -> auto_advance.StepTransition:
    return auto_advance.StepTransition(
        ready_status=ProjectStatus.plan_ready,
        next_running=ProjectStatus.scripting,
        kind=HITLKind.approve_plan,
    )


@pytest.mark.asyncio
async def test_apply_review_result_skips_stub_without_approving(monkeypatch) -> None:
    """skipped_stub несёт decision=approved — pipeline двигать нельзя."""
    called: list[str] = []
    for fn in ("_apply_approve", "_apply_regen", "_apply_reject"):
        monkeypatch.setattr(
            auto_advance,
            fn,
            AsyncMock(side_effect=lambda *a, _n=fn, **kw: called.append(_n)),
        )
    result = auto_review.ReviewResult(
        decision=HITLDecision.approved,
        confidence=0.0,
        reasons=["CHECK_PROMPT_NOT_CONFIGURED: check_plan"],
        status=auto_review.REVIEW_STATUS_SKIPPED_STUB,
    )
    applied = await auto_advance._apply_review_result(
        AsyncMock(), _FakeProject(), None, _transition(), result, bot=None
    )
    assert applied is False
    assert called == []


@pytest.mark.asyncio
async def test_apply_review_result_applies_normal_approve(monkeypatch) -> None:
    """Контроль: обычный applied-результат по-прежнему двигает pipeline."""
    called: list[str] = []
    monkeypatch.setattr(
        auto_advance,
        "_apply_approve",
        AsyncMock(side_effect=lambda *a, **kw: called.append("_apply_approve")),
    )
    result = auto_review.ReviewResult(decision=HITLDecision.approved, confidence=0.9)
    applied = await auto_advance._apply_review_result(
        AsyncMock(), _FakeProject(), None, _transition(), result, bot=None
    )
    assert applied is True
    assert called == ["_apply_approve"]


@pytest.mark.asyncio
async def test_verdict_review_skips_stub_without_calling_gpt(monkeypatch, tmp_path: Path) -> None:
    """Путь «Вердикт» (основной для ai_control) тоже обязан видеть заглушку.

    Без гейта заглушка уезжает в GPT, а её текст даёт реальный шанс на
    «approved» → auto-approve непроверенного шага.
    """
    monkeypatch.setattr(auto_review, "PROMPTS_ROOT", tmp_path / "prompts")

    from app.services import gpt_verdict_review

    monkeypatch.setattr(gpt_verdict_review, "PROMPTS_ROOT", tmp_path / "prompts")

    def _boom(*a, **kw):  # pragma: no cover — не должен вызваться
        raise AssertionError("GPT не должен вызываться на незаданном чек-промте")

    monkeypatch.setattr("app.services.gpt_client.get_gpt_client", _boom)

    result = await auto_advance._run_verdict_review_for_step(AsyncMock(), _FakeProject(), "plan")
    assert result.status == auto_review.REVIEW_STATUS_SKIPPED_STUB
    assert result.reasons and "CHECK_PROMPT_NOT_CONFIGURED" in result.reasons[0]
