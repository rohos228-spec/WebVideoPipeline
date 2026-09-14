"""Разбор ответа модели на шагах «План» и «Закадровый текст».

Футер промта (`chatgpt_xlsx`) допускает не только apply-ops, а модель за
пределами ChatGPT-браузера регулярно отвечает голым JSON или связным
текстом. Раньше такой ответ терялся целиком: план уходил в пустую строку,
закадр — в `LlmContractError`.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.contracts import LlmContractError
from app.models import Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.services.plan_validation import MIN_GENERAL_PLAN_CHARS
from app.services.xlsx_step_runners import extract_general_plan_from_gpt_reply

PLAN = "Акт 1. Герой выходит из дома. " * 12
VO = "Диктор говорит первую фразу. " * 12


def test_plan_from_bare_json_in_fence() -> None:
    reply = "Готово:\n```json\n" + json.dumps({"general_plan": PLAN}, ensure_ascii=False) + "\n```"
    assert extract_general_plan_from_gpt_reply(reply) == PLAN.strip()


def test_plan_from_bare_json_without_fence() -> None:
    reply = "Вот результат " + json.dumps({"общий_план": PLAN}, ensure_ascii=False) + " конец."
    assert extract_general_plan_from_gpt_reply(reply) == PLAN.strip()


@pytest.mark.parametrize("key", ["plan", "script", "content", "text"])
def test_plan_from_bare_json_alias_keys(key: str) -> None:
    reply = json.dumps({key: PLAN}, ensure_ascii=False)
    assert extract_general_plan_from_gpt_reply(reply) == PLAN.strip()


def test_plan_bare_json_ignores_short_value() -> None:
    short = "A" * (MIN_GENERAL_PLAN_CHARS - 1)
    assert extract_general_plan_from_gpt_reply(json.dumps({"general_plan": short})) == ""


def test_plan_from_plain_prose_with_keywords() -> None:
    assert extract_general_plan_from_gpt_reply(PLAN) == PLAN.strip()


def test_plan_from_fenced_prose_strips_fence() -> None:
    assert extract_general_plan_from_gpt_reply("```\n" + PLAN + "\n```") == PLAN.strip()


def test_plan_plain_prose_without_keywords_is_rejected() -> None:
    """Длинная простыня без слов плана — не план, а извинение модели."""
    assert extract_general_plan_from_gpt_reply("Извините, не получилось. " * 20) == ""


def test_plan_apply_ops_still_wins() -> None:
    """Порядок уровней: apply-ops важнее голого JSON в том же ответе."""
    reply = json.dumps(
        {"ops": [{"target": "project", "fields": {"общий_план": PLAN}}], "text": "мусор"},
        ensure_ascii=False,
    )
    assert extract_general_plan_from_gpt_reply(reply) == PLAN.strip()


def test_plan_empty_reply() -> None:
    assert extract_general_plan_from_gpt_reply("") == ""
    assert extract_general_plan_from_gpt_reply("   ") == ""


def _project() -> Project:
    p = Project(id=11, slug="parsers", topic="t", status=ProjectStatus.plan_ready)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    return p


async def _run_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str) -> str:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project()
    prompt_path = p.data_dir / "tmp_gpt" / "prompt_script.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("prompt", encoding="utf-8")

    async def fake_ask(*_a: object, **_k: object) -> str:
        return reply

    async def fake_lock(_pid: int, _step: str, fn):
        return await fn()

    with (
        patch.object(xsr.xgf, "telegram_style_ask_with_files", side_effect=fake_ask),
        patch.object(xsr.xgf, "run_under_xlsx_lock", side_effect=fake_lock),
        patch.object(xsr.cx, "write_script_prompt_file", return_value=prompt_path),
        patch.object(xsr.cx, "chat_message", return_value="go"),
    ):
        _res, vo = await xsr.run_script_xlsx(p)
    return vo


@pytest.mark.asyncio
async def test_script_from_bare_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "```json\n" + json.dumps({"закадровый_текст": VO}, ensure_ascii=False) + "\n```"
    assert VO.strip()[:40] in await _run_script(tmp_path, monkeypatch, reply)


@pytest.mark.asyncio
async def test_script_from_bare_json_english_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = json.dumps({"voiceover": VO}, ensure_ascii=False)
    assert VO.strip()[:40] in await _run_script(tmp_path, monkeypatch, reply)


@pytest.mark.asyncio
async def test_script_plain_prose_stays_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Хвост «весь ответ целиком» из форка не берём: отчёт модели ≠ закадр."""
    with pytest.raises(LlmContractError):
        await _run_script(tmp_path, monkeypatch, "Извините, не смог. " * 30)
