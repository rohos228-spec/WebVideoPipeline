"""Промт узла изменился — это должно быть сказано вслух.

В графе лежит только имя варианта, текст — в prompts/ вне git. Живой прогон
2026-08-31: чтобы поменять поведение узла, файл копировался по scp на прод, а
граф не изменился ни на байт. Первый шаг решения C из ORCHESTRATOR-V2 §3.8.
"""

from __future__ import annotations

from app.services.prompt_drift import (
    META_KEY,
    drifted_nodes,
    note_prompt_used,
    prompt_fingerprint,
)


class _Project:
    def __init__(self) -> None:
        self.id = 1
        self.meta: dict = {}


def test_first_run_is_not_a_drift() -> None:
    p = _Project()
    assert note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="раз") is None
    assert drifted_nodes(p) == []


def test_same_text_is_not_a_drift() -> None:
    p = _Project()
    for _ in range(3):
        note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="раз")
    assert drifted_nodes(p) == []


def test_changed_text_reports_previous_hash() -> None:
    p = _Project()
    note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="раз")
    prev = note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="два")
    assert prev == prompt_fingerprint("раз")
    assert [d["node"] for d in drifted_nodes(p)] == ["n_plan"]


def test_drift_is_per_node_not_per_project() -> None:
    """Два узла на одном шаге живут своими отпечатками."""
    p = _Project()
    note_prompt_used(p, node_key="n_check_hero", step_code="excel_gpt", variant="a", text="раз")
    note_prompt_used(p, node_key="n_check_images", step_code="excel_gpt", variant="b", text="два")
    note_prompt_used(p, node_key="n_check_hero", step_code="excel_gpt", variant="a", text="ТРИ")
    assert [d["node"] for d in drifted_nodes(p)] == ["n_check_hero"]


def test_variant_rename_alone_is_not_a_drift() -> None:
    """Смысл в содержимом: то же тело под новым именем — не расхождение."""
    p = _Project()
    note_prompt_used(p, node_key="n_plan", step_code="plan", variant="default", text="тело")
    assert note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="тело") is None


def test_record_kept_in_project_meta() -> None:
    p = _Project()
    note_prompt_used(p, node_key="n_plan", step_code="plan", variant="vlog", text="раз")
    rec = p.meta[META_KEY]["n_plan"]
    assert rec["variant"] == "vlog" and rec["step"] == "plan" and rec["hash"]


def test_empty_text_and_key_are_ignored() -> None:
    p = _Project()
    assert note_prompt_used(p, node_key="n_plan", step_code="plan", variant="v", text="") is None
    assert note_prompt_used(p, node_key="", step_code="", variant="v", text="раз") is None
    assert p.meta == {}


def test_hash_matches_ledger_definition() -> None:
    """Отпечаток — тот же, что уходит в llm_calls.prompt_version_hash."""
    from app.services.input_hash import prompt_version_hash

    assert prompt_fingerprint("текст") == prompt_version_hash("текст")
