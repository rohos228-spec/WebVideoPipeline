"""Диагностика отказов LLM: пустой ops, reject-сайдкар, QC no-op, пустой вход.

Без сети: chat замокан, gpt_api_enabled → True.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.contracts import APPLY_OPS, LlmContractError
from app.contracts.apply_ops import APPLY_OPS_ALLOW_EMPTY
from app.contracts.policy import _REJECTS_KEEP, run_with_contract
from app.services import gpt_operator_client as goc
from app.services.llm_attempts import recent_rejects

UUIDS = ["aaaa1111-0000-0000-0000-000000000001", "bbbb2222-0000-0000-0000-000000000002"]


def _db_frames(tmp_path):
    p = tmp_path / "db_frames_batch_01_L0.json"
    p.write_text(
        json.dumps({"frames": [{"uuid": u, "voiceover_text": "текст"} for u in UUIDS]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def _patch_chat(monkeypatch, replies, prompts, model="gpt-test"):
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: True)
    monkeypatch.setattr("app.services.gpt_api.collect_result_urls", lambda text: [])

    async def fake_chat(**kw):
        prompts.append(kw.get("prompt") or "")
        i = min(len(prompts) - 1, len(replies) - 1)
        return SimpleNamespace(text=replies[i], model=model, served_model="")

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)


# --- 2. report модели не теряется ------------------------------------------


def test_empty_ops_error_keeps_model_report() -> None:
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse('{"ops": [], "report": "все кадры уже заполнены"}')
    msg = str(ei.value)
    assert "пустой ops" in msg
    assert "модель объяснила" in msg and "все кадры уже заполнены" in msg


def test_empty_ops_error_without_report_unchanged() -> None:
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS.parse('{"ops": []}')
    assert "пустой ops — нечего применять" in str(ei.value)
    assert "модель объяснила" not in str(ei.value)


def test_nonempty_with_report_still_ok() -> None:
    parsed = APPLY_OPS.parse('{"ops":[{"frame_uuid":"u1","fields":{"место":"двор"}}],"report":"ok"}')
    assert parsed.payload.frame_uuids() == ["u1"]


# --- 3. семантика пустого ops ----------------------------------------------


def test_allow_empty_contract_accepts_empty_and_rejects_refusal() -> None:
    assert APPLY_OPS_ALLOW_EMPTY.parse('{"ops": []}').payload.ops == []
    assert APPLY_OPS_ALLOW_EMPTY.parse('{"ops": [], "report": "всё чисто"}').payload.ops == []
    with pytest.raises(LlmContractError) as ei:
        APPLY_OPS_ALLOW_EMPTY.parse('{"ops": [], "error": "нет файла"}')
    assert "отказ" in str(ei.value)


async def test_run_operator_api_accepts_allow_empty_ops(tmp_path, monkeypatch) -> None:
    """Раньше: TypeError (run_operator_api не знал allow_empty_ops)."""
    prompts: list[str] = []
    _patch_chat(monkeypatch, ['{"ops": [], "report": "всё чисто"}'], prompts)
    res = await goc.run_operator_api(
        project_dir=tmp_path,
        node_key="n_qc",
        role="assist",
        output_mode="project_file",
        prompt="проверь",
        accompanying="",
        input_paths=[_db_frames(tmp_path)],
        auto_pack=False,
        allow_empty_ops=True,
    )
    assert res.empty_ops_accepted is True
    assert res.apply_ops is not None and res.apply_ops.get("ops") == []
    assert len(prompts) == 1  # без repair


async def test_normal_node_empty_ops_retry_lists_expected_uuids(tmp_path, monkeypatch) -> None:
    prompts: list[str] = []
    ok = json.dumps({"ops": [{"frame_uuid": u, "fields": {"место": "двор"}} for u in UUIDS]})
    _patch_chat(monkeypatch, ['{"ops": [], "report": "не понял задачу"}', ok], prompts)
    res = await goc.run_operator_api(
        project_dir=tmp_path,
        node_key="n_excel_gpt_1",
        role="assist",
        output_mode="project_file",
        prompt="заполни",
        accompanying="",
        input_paths=[_db_frames(tmp_path)],
        auto_pack=False,
    )
    assert res.empty_ops_accepted is False
    assert len(res.apply_ops["ops"]) == 2
    retry = prompts[1]
    assert "пустой список ops не допускается" in retry
    assert all(u in retry for u in UUIDS)
    assert "не понял задачу" in retry  # пояснение модели дошло до фидбека

    recs = recent_rejects(tmp_path, label_prefix="apply_ops_n_excel_gpt_1")
    assert len(recs) == 1
    r = recs[0]
    assert r["attempt"] == 1 and r["kind"] == "validate"
    assert r["model"] == "gpt-test"
    assert r["input_frames"] == 2 and r["input_chars"] > 0
    assert r["report"] == "не понял задачу"
    assert "заполни" in r["prompt_excerpt"]
    assert "пустой ops" in r["error"]
    assert r["expected_uuids"] == UUIDS
    assert isinstance(r["mtime"], float)


async def test_normal_node_empty_ops_exhausts_with_report(tmp_path, monkeypatch) -> None:
    prompts: list[str] = []
    _patch_chat(monkeypatch, ['{"ops": [], "report": "кадров нет"}'], prompts)
    with pytest.raises(LlmContractError) as ei:
        await goc.run_operator_api(
            project_dir=tmp_path,
            node_key="n_excel_gpt_1",
            role="assist",
            output_mode="project_file",
            prompt="заполни",
            accompanying="",
            input_paths=[_db_frames(tmp_path)],
            auto_pack=False,
        )
    assert "repair-лимит исчерпан" in str(ei.value)
    assert "кадров нет" in str(ei.value)
    recs = recent_rejects(tmp_path)
    assert [r["attempt"] for r in recs] == [3, 2, 1]
    # промпт повтора (с фидбеком) — в выдержке своей попытки
    assert "ПУСТОЙ ops НЕ ПРИНИМАЕТСЯ" in recs[0]["prompt_excerpt"]


def test_batch_footer_qc_hint_matches_contract() -> None:
    from app.services.apply_ops_batches import _batch_footer

    allowed = _batch_footer(1, 0, 3, footer_kind="shots_qc", allow_empty_ops=True)
    strict = _batch_footer(1, 0, 3, footer_kind="shots_qc")
    assert "Пустые ops = ок" in allowed
    assert "Пустые ops = ок" not in strict and "НЕ принимается" in strict


# --- 1. сайдкар, retention, ридер -----------------------------------------


async def test_policy_sidecar_redacts_and_prunes(tmp_path) -> None:
    reject_dir = tmp_path / "llm_rejects"
    secret = "sk-" + "A" * 30

    async def call(feedback):
        return '{"ops": []}'

    for _ in range(_REJECTS_KEEP // 3 + 2):
        with pytest.raises(LlmContractError):
            await run_with_contract(
                contract=APPLY_OPS,
                call=call,
                reject_dir=reject_dir,
                label="x" + str(_),
                attempt_info=lambda: {"prompt": f"ключ {secret} " + "я" * 5000, "model": "m1"},
            )
    txts = list(reject_dir.glob("*.txt"))
    jsons = list(reject_dir.glob("*.json"))
    assert len(txts) == _REJECTS_KEEP
    assert {p.stem for p in jsons} == {p.stem for p in txts}
    side = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert secret not in side["prompt_excerpt"] and "[скрыто]" in side["prompt_excerpt"]
    assert "вырезано" in side["prompt_excerpt"]
    assert side["model"] == "m1"


def test_recent_rejects_reads_legacy_txt_without_sidecar(tmp_path) -> None:
    d = tmp_path / "llm_rejects"
    d.mkdir()
    (d / "apply_ops_n1_a2_validate.txt").write_text(
        "# contract: vp_apply_ops\n# kind: validate\n# error: схема нарушена:\n- <root>: пустой ops\n"
        '# reply_len: 11\n\n{"ops": []}',
        encoding="utf-8",
    )
    recs = recent_rejects(d, limit=5)
    assert len(recs) == 1
    r = recs[0]
    assert r["label"] == "apply_ops_n1" and r["attempt"] == 2 and r["kind"] == "validate"
    assert "пустой ops" in r["error"]
    assert r["model"] == "" and r["prompt_excerpt"] == "" and r["input_frames"] is None
    assert recent_rejects(tmp_path / "nope") == []
    assert recent_rejects(tmp_path, label_prefix="other") == []


# --- 4. пустой вход ---------------------------------------------------------


def test_require_frames_for_db_sot() -> None:
    from app.orchestrator.steps.enrich_xlsx import require_frames_for_db_sot

    require_frames_for_db_sot(1, "n_excel_gpt_1", [SimpleNamespace(uuid="u1")])
    with pytest.raises(RuntimeError) as ei:
        require_frames_for_db_sot(1, "n_excel_gpt_1", [])
    assert "нет кадров на входе ноды n_excel_gpt_1" in str(ei.value)
    assert "разбивки на кадры" in str(ei.value)
