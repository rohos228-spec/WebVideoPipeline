"""Сверка модели на ноде и модели, ушедшей провайдеру (app/services/model_coverage, scripts/model_coverage)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import model_coverage as mc
from app.settings import settings
from scripts import model_coverage as cli

SRC = "| INFO     | app.services"


def _line(clock: str, where: str, msg: str) -> str:
    return f"2026-10-21 {clock}.100 {SRC}.{where} | {msg}"


def _bind(clock, pid, node, label, model):
    return _line(clock, "llm_override:bind_project_llm", f"node_llm: #{pid} {node} → {label} ({model})")


def _text(clock, provider, model):
    return _line(clock, "gpt_api:_chat_single",
                 f"text_llm.chat → {provider} model={model} url=https://x/v1 via=direct")


def _canvas(*nodes):
    return {"canvas_graph": {"nodes": [{"id": k, "type": t, "data": dict(d or {})} for k, t, d in nodes],
                             "edges": []}}


def _project(pid, meta, **extra):
    return SimpleNamespace(id=pid, meta=meta, image_generator=extra.get("image"),
                           video_generator=extra.get("video"))


@pytest.fixture(autouse=True)
def _outsee_defaults(monkeypatch):
    monkeypatch.setattr(settings, "outsee_default_image_model", "gpt-image-2", raising=False)
    monkeypatch.setattr(settings, "outsee_default_video_model", "veo-3-1-lite", raising=False)


@pytest.mark.xfail(strict=True, reason='merge line: one database, no project.db; model catalog differs')
def test_text_call_goes_to_the_open_bind_and_normalizes_provider_spelling() -> None:
    scan = mc.scan_log([
        _text("09:59:00", "52pay (gpt-5.6-sol)", "gpt-5.6-sol"),
        _bind("10:00:00", 4, "plan", "GPT 5.6 Sol", "gpt-5.6-sol"),
        _text("10:00:01", "kie (gpt-5-6-sol)", "gpt-5-6-sol"),
        _text("10:00:02", "kie (gpt-5-6-sol)", "gpt-5-6-sol"),
        "  Traceback continuation without stamp",
    ])
    assert scan.text_calls == 3 and scan.unmatched_text_calls == 2
    rows = mc.project_rows(_project(4, _canvas(("n_plan", "plan", None))), scan)
    assert len(rows) == 1
    row = rows[0]
    assert row["node_key"] == "n_plan" and row["configured"] == "gpt-5.6-sol"
    assert row["bound"] == "gpt-5.6-sol" and row["sent"] == "gpt-5-6-sol"
    assert row["verdict"] == mc.MATCH and row["verdict_ru"] == "совпадает" and row["note"] == ""


def test_wrong_model_at_provider_is_a_mismatch_and_parallel_projects_make_it_unsure() -> None:
    meta = _canvas(("n_excel_gpt_1", "excel_gpt", {"slotIndex": 1, "modelId": "claude-sonnet-5"}))
    alone = mc.scan_log([
        _bind("10:00:00", 6, "n_excel_gpt_1", "Claude Sonnet", "claude-sonnet-5"),
        _text("10:00:01", "52pay (gpt-5.6-sol)", "gpt-5.6-sol"),
    ])
    row = mc.project_rows(_project(6, meta), alone)[0]
    assert row["verdict"] == mc.DRIFT and row["sent"] == "gpt-5.6-sol" and row["bound"] == "claude-sonnet-5"
    crowded = mc.scan_log([
        _bind("10:00:00", 7, "plan", "GPT 5.6 Sol", "gpt-5.6-sol"),
        _bind("10:00:00", 6, "n_excel_gpt_1", "Claude Sonnet", "claude-sonnet-5"),
        _text("10:00:01", "52pay (gpt-5.6-sol)", "gpt-5.6-sol"),
        _text("10:00:02", "52pay (gpt-5.6-sol)", "gpt-5.6-sol"),
    ])
    row = mc.project_rows(_project(6, meta), crowded)[0]
    assert row["verdict"] == mc.UNSURE and "другие проекты" in row["note"]


def test_bind_without_call_node_not_run_and_model_changed_after_run() -> None:
    meta = _canvas(("n_plan", "plan", {"modelId": "claude-sonnet-5"}), ("n_script", "script", None),
                   ("n_split", "split", None), ("n_topic", "topic", None), ("n_gate", "hitl_images", None))
    scan = mc.scan_log([
        _bind("10:00:00", 3, "plan", "GPT 5.6 Sol", "gpt-5.6-sol"),
        _text("10:00:05", "vibecode (gpt-5.6-sol)", "gpt-5.6-sol"),
        _bind("10:10:00", 3, "script", "GPT 5.6 Sol", "gpt-5.6-sol"),
        _text("10:50:00", "vibecode (gpt-5.6-sol)", "gpt-5.6-sol"),
    ])
    rows = {r["node_key"]: r for r in mc.project_rows(_project(3, meta), scan)}
    assert set(rows) == {"n_plan", "n_script", "n_split"}
    assert rows["n_plan"]["verdict"] == mc.MATCH
    assert rows["n_plan"]["configured"] != "gpt-5.6-sol"
    assert "на ноде сейчас другая модель" in rows["n_plan"]["note"]
    assert rows["n_script"]["verdict"] == mc.NO_CALL
    assert rows["n_split"]["verdict"] == mc.NOT_RUN
    assert mc.summary(list(rows.values()))[mc.MATCH] == 1


def test_media_calls_compare_by_canonical_id_and_flag_kling_fallback() -> None:
    meta = _canvas(("n_images", "images", None), ("n_videos", "videos", None))
    project = _project(12, meta, image="gpt_image_2_vip", video="veo_3_1_lite")
    scan = mc.scan_log([
        _line("13:38:07", "bots.outsee_http:generate_image",
              "outsee_api.image model=gpt-image-2 aspect=9:16 res=2K refs=0 project=12 try=1/3"),
        _line("13:38:08", "bots.outsee_http:generate_image",
              "outsee_api.image model=nano-banana-2 aspect=9:16 res=2K refs=0 project=99 try=1/3"),
        _line("14:00:00", "bots.kie_kling:_create",
              "kie_kling: createTask model=kling-2.6/image-to-video dur=5 sound=False i2v=True "
              "prompt_chars=80 project=12"),
    ])
    rows = {r["node_key"]: r for r in mc.project_rows(project, scan)}
    assert rows["n_images"]["verdict"] == mc.MATCH and rows["n_images"]["sent"] == "gpt-image-2"
    assert rows["n_images"]["sent_provider"] == "outsee"
    assert rows["n_videos"]["configured"] == "veo-3-1-lite" and rows["n_videos"]["sent"] == "kling-2-6"
    assert rows["n_videos"]["verdict"] == mc.DRIFT and "запасная модель" in rows["n_videos"]["note"]
    kie = mc.scan_log([_line("15:00:00", "bots.kie_http:generate_kie_image",
                             "kie_http: generate image flux-2-pro (model=flux-2/pro) aspect=9:16 refs=0 project=5")])
    flux = mc.project_rows(_project(5, _canvas(("n_images", "images", {"modelId": "flux-2-pro"}))), kie)[0]
    assert flux["configured"] == "flux-2-pro" and flux["verdict"] == mc.MATCH


def _db(path, ddl, rows):
    with sqlite3.connect(path) as db:
        db.execute(ddl)
        db.executemany("insert into projects values (" + ",".join("?" * len(rows[0])) + ")", rows)


@pytest.mark.xfail(strict=True, reason='merge line: one database, no project.db; model catalog differs')
def test_cli_reads_project_db_first_and_prints_table_and_json(tmp_path, capsys) -> None:
    master_meta = json.dumps(_canvas(("n_plan", "plan", {"modelId": "claude-sonnet-5"})))
    local_meta = json.dumps(_canvas(("n_plan", "plan", None)))
    _db(tmp_path / "state.db", "create table projects (id, slug, title, batch_slug, meta, image_generator, "
        "video_generator)", [(4, "sovy", "Совы", None, master_meta, None, None),
                             (5, "kity", "Киты", None, "{}", None, None)])
    (tmp_path / "videos" / "sovy").mkdir(parents=True)
    _db(tmp_path / "videos" / "sovy" / "project.db",
        "create table projects (id, meta, image_generator, video_generator)", [(4, local_meta, None, None)])
    (tmp_path / "studio-live.log").write_text("\n".join([
        _bind("10:00:00", 4, "plan", "GPT 5.6 Sol", "gpt-5.6-sol"),
        _text("10:00:01", "vibecode (gpt-5.6-sol)", "gpt-5.6-sol")]), encoding="utf-8")

    projects = cli.load_projects(tmp_path)
    assert [(p.id, p.source) for p in projects] == [(4, "project.db"), (5, "state.db")]
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "## #4 Совы (данные из project.db)" in text and "| n_plan | plan | gpt-5.6-sol |" in text
    assert "совпадает" in text and "## #5 Киты (данные из state.db)" in text
    assert "Итого: совпадает 1" in text
    assert cli.main(["--data-dir", str(tmp_path), "--project", "4", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["verdict"] for r in data["rows"]] == ["match"] and data["summary"]["match"] == 1
