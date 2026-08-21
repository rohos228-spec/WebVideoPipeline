"""Этап 5, C.5: битый отчёт проверки = repair-retry, не verdict:fail."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.contracts import LlmContractError
from app.services import gpt_operator_client as goc
from app.services.check_analysis import parse_check_analysis

TXT_REPORT = "# ОТЧЁТ ПРОВЕРКИ\nverdict: pass\n\n## summary\nвсё ок\n"


def test_strict_garbage_raises_contract_error() -> None:
    with pytest.raises(LlmContractError) as ei:
        parse_check_analysis("просто проза", strict_contract=True)
    assert ei.value.kind == "parse"


def test_strict_apply_ops_echo_raises() -> None:
    # Эхо apply-ops — не отчёт; раньше молчаливый fail → платный regen
    with pytest.raises(LlmContractError):
        parse_check_analysis(
            '{"ops":[{"frame_uuid":"u1","fields":{"x":1}}]}',
            strict_contract=True,
        )


def test_strict_real_fail_verdict_passes_through() -> None:
    a = parse_check_analysis(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\n## summary\nбрак\n",
        strict_contract=True,
    )
    assert a.verdict == "fail"


def test_default_mode_keeps_legacy_fail() -> None:
    assert parse_check_analysis("просто проза").verdict == "fail"


def test_strict_txt_without_verdict_raises() -> None:
    # Дыра TXT-пути (панель): шаблонный заголовок без verdict раньше
    # превращался в тихий fail внутри parse_check_report_txt
    broken = "# ОТЧЁТ ПРОВЕРКИ\nмусор без вердикта\n\n## summary\nчто-то\n"
    with pytest.raises(LlmContractError):
        parse_check_analysis(broken, strict_contract=True)
    # legacy-режим — прежнее поведение
    assert parse_check_analysis(broken).verdict == "fail"


@pytest.mark.asyncio
async def test_operator_check_repairs_broken_report(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: True)

    async def fake_chat(**kw):
        calls.append(kw.get("prompt") or "")
        if len(calls) == 1:
            return SimpleNamespace(text="Извините, файл не открылся.")
        return SimpleNamespace(text=TXT_REPORT)

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    monkeypatch.setattr("app.services.gpt_api.collect_result_urls", lambda text: [])

    res = await goc.run_operator_api(
        project_dir=tmp_path,
        node_key="n_check_1",
        role="review",
        output_mode="text",
        prompt="проверь",
        accompanying="",
        input_paths=[],
        check_mode=True,
        check_fix=False,
    )
    assert len(calls) == 2
    assert "ОШИБКИ ПРОШЛОЙ ПОПЫТКИ" in calls[1]
    assert "verdict: pass" in (res.reply_text or "")


@pytest.mark.asyncio
async def test_operator_check_exhaustion_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: True)

    async def fake_chat(**kw):
        return SimpleNamespace(text="мусор без отчёта")

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    monkeypatch.setattr("app.services.gpt_api.collect_result_urls", lambda text: [])

    with pytest.raises(LlmContractError):
        await goc.run_operator_api(
            project_dir=tmp_path,
            node_key="n_check_1",
            role="review",
            output_mode="text",
            prompt="проверь",
            accompanying="",
            input_paths=[],
            check_mode=True,
            check_fix=False,
        )
