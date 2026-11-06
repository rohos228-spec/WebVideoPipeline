"""Класс «разберись / найди где / почему так»: быстрый путь по данным, без карточек запуска.

Скриншоты руководителя 06.11: «у меня картинки без стиля пластилин, найди где он теряется» →
«по этим данным не определилось»; «ну я запустил, он отработал 3 секунды, ищи что случилось» →
общий отчёт; «промты картинок я говорю ноду запускал!» → карточка запуска за $1.
"""

from __future__ import annotations

import pytest

from app.orchestrator.queue import MessageMode, classify_deterministic, is_investigation


@pytest.mark.parametrize(
    "text",
    [
        "у меня картинки без стиля пластилин, найди где он теряется",
        "ну я запустил, он отработал 3 секунды, хотя это невозможно, ищи что случилось",
        "разберись, почему промты картинок отработали за 3 секунды",
        "посмотри, что не так с нодой работа с gpt",
        "выясни, куда делся стиль",
        "скажи почему разбивка дала 30 кадров",
    ],
)
def test_investigation_is_a_question_not_a_task(text: str) -> None:
    assert is_investigation(text)
    got = classify_deterministic(text)
    assert got is not None and got.mode is MessageMode.append, text


@pytest.mark.parametrize(
    "text",
    [
        "найди причину и перезапусти ноду",
        "разберись и почини",
        "посмотри промт кадра 3 и поменяй его на ночной",
        "найди где теряется стиль и перерисуй кадры",
    ],
)
def test_investigation_with_fix_stays_a_task(text: str) -> None:
    assert not is_investigation(text)


def test_style_loss_reads_prompts_with_keyword_and_image_node() -> None:
    from app.orchestrator.agents.question_context import plan_reads

    reads = dict(plan_reads("у меня картинки без стиля пластилин, найди где он теряется", 46))
    assert reads["diag.content_checks"] == {"project_id": 46, "step": "img_pr", "keyword": "пластилин"}
    assert reads["diag.node_failure"] == {"project_id": 46, "step": "img"}


def test_three_seconds_reads_last_run_and_logs() -> None:
    from app.orchestrator.agents.question_context import plan_reads

    reads = plan_reads("ну я запустил промты картинок, он отработал 3 секунды, ищи что случилось", 46)
    assert ("diag.node_failure", {"project_id": 46, "step": "img_pr"}) in reads, reads
    assert ("logs.tail", {"project_id": 46, "kind": "errors", "limit": 20}) in reads


def test_llm_evidence_short_reply_and_window() -> None:
    from app.services.action_layer.node_failure import llm_evidence, llm_evidence_text

    rows = [
        {"created_at": "2026-11-06T10:00:00+00:00", "duration_ms": 20000, "completion_tokens": 2000, "result": "ok"},
        {"created_at": "2026-11-06T12:00:00+00:00", "duration_ms": 3000, "completion_tokens": 8, "result": "ok", "model": "gpt-6.1-sol"},
    ]
    nr = {"started_at": "2026-11-06T11:59:58+00:00", "finished_at": "2026-11-06T12:00:03+00:00"}
    ev = llm_evidence(rows, nr)
    assert ev["calls"] == 1 and ev["short_reply"] and ev["completion_tokens"] == 8
    text = llm_evidence_text(ev)
    assert "Вызовов модели в последнем запуске: 1" in text and "почти пустой" in text
    # Без окна запуска — последняя пачка вызовов (разрыв больше 10 минут — другой запуск).
    assert llm_evidence(rows, None)["calls"] == 1
    assert llm_evidence([], None) is None
    assert "в журнале нет" in llm_evidence_text(None)


def test_keyword_text_names_frames_without_word() -> None:
    from app.services.action_layer.content_checks_diag import keyword_text

    text = keyword_text({"keyword": "пластилин", "frames": 24, "with_prompt": 24, "missing": [3, 7]})
    assert "нет в 2 из 24" in text and "кадры 3, 7" in text and "Промты картинок" in text
    text = keyword_text({"keyword": "пластилин", "frames": 24, "with_prompt": 24, "missing": []})
    assert "во всех 24" in text and "генерации картинок" in text


def test_resume_cost_by_origin() -> None:
    from app.services.action_layer.builtin_wave3 import _resume_cost

    free = _resume_cost({"project_id": 1, "_resume": {"from": "frames_ready", "auto_mode": False, "llm_budget": 0.1}})
    assert free.known and free.high == 0.0
    text = _resume_cost({"project_id": 1, "_resume": {"from": "scripting", "auto_mode": True, "llm_budget": 0.1}})
    assert text.known and text.high == 0.1 and "лимита текстов" in text.basis
    media = _resume_cost({"project_id": 1, "_resume": {"from": "generating_images", "auto_mode": True, "llm_budget": 0.1}})
    assert not media.known
    assert not _resume_cost({"project_id": 1}).known


def test_pause_info_shows_human_wait_only_for_same_status() -> None:
    from app.services.action_layer.builtin_insight import _pause_info, _pause_lines

    meta = {"auto_wait": {"status": "plan_ready", "reason": "CHECK_PROMPT_NOT_CONFIGURED: plan"}}
    pause = _pause_info("plan_ready", meta)
    assert pause["await_human"] and "чек-промта" in pause["await_human"]
    assert any("Автопродвижение стоит" in line for line in _pause_lines(pause, None))
    assert _pause_info("scripting", meta)["await_human"] is None


def test_summary_carries_dropped_project_note() -> None:
    import asyncio

    from app.orchestrator.agents.summarizer import TaskFacts, TaskSummarizer

    facts = TaskFacts(goal="запусти 26, 12 и 32", status="done", note="Не поставлен в очередь: #12 — нет данных сцены.")
    text = asyncio.run(TaskSummarizer(router=None).summarize(facts, run_id="t"))
    assert "Не поставлен в очередь: #12" in text


def test_status_explains_manual_start_wait_after_restart() -> None:
    from app.services.action_layer.builtin_insight import _pause_info, _pause_lines

    pause = _pause_info("plan_ready", {"auto_await_manual_start": True})
    assert not pause["paused"] and pause["await_manual_start"]
    assert any("ждёт ручного ▶" in line for line in _pause_lines(pause, None))
