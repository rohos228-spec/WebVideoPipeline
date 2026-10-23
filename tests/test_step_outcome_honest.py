"""Итог шага в чате говорит правду: откат очередью, пауза, стоп и нода GPT без вызова."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Project, ProjectStatus
from app.services.action_layer import builtin_insight as bi
from app.services.action_layer import step_outcome as so
from app.services.action_layer.builtin_launch import _poll_any_step, _run_any_step
from app.services.action_layer.spec import JobState
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401


class _Ctx:
    def __init__(self, status: str, meta: dict | None = None, *, auto_mode: bool = False,
                 node_runs: list | None = None, launch_status: str = "generating_image_prompts"):
        self.detail = {"id": 7, "title": "Кот", "status": status, "meta": meta or {},
                       "auto_mode": auto_mode}
        self.node_runs = node_runs or []
        self.launch_status = launch_status
        self.calls: list[tuple[str, str]] = []

    async def call(self, method, path, **kw):
        self.calls.append((method, path))
        if method == "POST":
            return {**self.detail, "status": self.launch_status}
        if path == "/projects":
            return [{"id": 7, "status": self.detail["status"]}]
        if path.endswith("/run"):
            return {"node_runs": self.node_runs}
        return self.detail


@pytest.fixture
def studio(monkeypatch):
    flags = {"stop_file": False, "queue": []}
    monkeypatch.setattr(so, "_stop_flag", lambda pid: flags["stop_file"])
    monkeypatch.setattr(so, "_in_gen_queue", lambda pid: pid in flags["queue"])
    return flags


def _started(running="generating_image_prompts", before="enrich_3_ready", **extra) -> dict:
    return {"started": True, "step_code": "img_pr", "running_status": running,
            "status_before": before, "launched_at": time.time() - 60, **extra}


_IMG_PR = {"project_id": 7, "step_code": "img_pr"}


async def test_queue_rollback_is_reported_as_not_done(studio) -> None:
    studio.update(stop_file=True, queue=[7])
    state = await _poll_any_step(_Ctx("enrich_3_ready"), _IMG_PR, _started())
    assert state.done and not state.ok
    assert state.error.startswith("#7 «Кот»: Шаг «") and "не выполнен" in state.error
    assert "очередь генерации откатила" in state.error
    assert "завершён" not in state.error and "enrich_3_ready" not in state.error


async def test_rollback_without_queue_still_fails_the_step(studio) -> None:
    state = await _poll_any_step(_Ctx("enrich_3_ready"), _IMG_PR, _started())
    assert not state.ok and "откатился назад" in state.error


async def test_rollback_after_a_step_error_names_the_error(studio) -> None:
    meta = {"step_failure": {"last_error": "GPT не ответил", "last_running": "generating_image_prompts"}}
    state = await _poll_any_step(_Ctx("enrich_3_ready", meta), _IMG_PR, _started())
    assert not state.ok and "Причина: GPT не ответил" in state.error


async def test_user_stop_is_reported(studio) -> None:
    meta = {"user_stop": {"by": "human"}}
    state = await _poll_any_step(_Ctx("enrich_3_ready", meta), _IMG_PR, _started())
    assert not state.ok and "кнопкой «Стоп»" in state.error


async def test_pause_with_reason_fails_the_step(studio) -> None:
    meta = {"auto_paused_reason": "frames_ready: GPT отметил как rejected"}
    state = await _poll_any_step(_Ctx("paused", meta), {"project_id": 7, "step_code": "split"},
                                 _started("splitting", "script_ready"))
    assert not state.ok
    assert "не выполнен: проект на паузе. Причина: frames_ready: GPT отметил как rejected" in state.error


async def test_step_that_reached_its_result_is_done(studio) -> None:
    studio.update(stop_file=True, queue=[7])
    state = await _poll_any_step(_Ctx("image_prompts_ready"), _IMG_PR, _started())
    assert state.done and state.ok and "завершён" in state.result["text_ru"]


async def test_rerun_of_an_earlier_step_is_not_a_rollback(studio) -> None:
    state = await _poll_any_step(_Ctx("image_prompts_ready"), _IMG_PR,
                                 _started(before="videos_ready"))
    assert state.ok


async def test_old_launch_without_marks_keeps_plain_behaviour(studio) -> None:
    state = await _poll_any_step(_Ctx("enrich_3_ready"), _IMG_PR, {"started": True})
    assert state.ok


async def test_auto_mode_waits_for_the_studio_check_then_reports_pause(studio, monkeypatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(so.time, "time", lambda: clock["t"])
    started = _started("splitting", "script_ready")
    params = {"project_id": 7, "step_code": "split"}
    first = await _poll_any_step(_Ctx("frames_ready", auto_mode=True), params, started)
    assert not first.done and "проверку" in first.note
    clock["t"] += 30
    meta = {"auto_paused_reason": "harness gate: frames_ready — брак сценария"}
    paused = await _poll_any_step(_Ctx("paused", meta, auto_mode=True), params, started)
    assert paused.done and not paused.ok and "брак сценария" in paused.error
    assert "settle_status" not in started


async def test_auto_mode_step_is_done_once_the_status_holds(studio, monkeypatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(so.time, "time", lambda: clock["t"])
    started = _started("splitting", "script_ready")
    params = {"project_id": 7, "step_code": "split"}
    assert not (await _poll_any_step(_Ctx("frames_ready", auto_mode=True), params, started)).done
    clock["t"] += so.SETTLE_S + 1
    state = await _poll_any_step(_Ctx("frames_ready", auto_mode=True), params, started)
    assert state.done and state.ok and "settle_since" not in started


async def test_auto_mode_step_is_done_when_the_studio_starts_the_next_one(studio, monkeypatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(so.time, "time", lambda: clock["t"])
    started = _started("splitting", "script_ready")
    params = {"project_id": 7, "step_code": "split"}
    assert not (await _poll_any_step(_Ctx("frames_ready", auto_mode=True), params, started)).done
    clock["t"] += 10
    state = await _poll_any_step(_Ctx("scene_designing", auto_mode=True), params, started)
    assert state.done and state.ok and "settle_status" not in started
    assert "завершён, статус проекта: " in state.result["text_ru"]


async def test_launch_remembers_status_before_and_running_status(monkeypatch) -> None:
    async def no_cost_check(ctx, params):
        return None

    monkeypatch.setattr("app.services.action_layer.builtin_launch._verify_any_cost", no_cost_check)
    ctx = _Ctx("enrich_3_ready", launch_status="enriching_2")
    out = await _run_any_step(ctx, {"project_id": 7, "step_code": "excel_gpt", "node_key": "n5"})
    assert out["status_before"] == "enrich_3_ready" and out["running_status"] == "enriching_2"
    assert out["node_key"] == "n5" and out["launched_at"] <= time.time()
    marks = so.launch_marks("img_pr", {"status": "images_ready"}, {"status": "images_ready"},
                             time.time())
    assert marks["running_status"] == "generating_image_prompts"


def _gpt_node(monkeypatch, tmp_path, *, fresh: bool):
    async def no_row(pid):
        return None

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(bi, "_project_row", no_row)
    folder = tmp_path / "uploads"
    folder.mkdir(exist_ok=True)
    monkeypatch.setattr("app.services.excel_gpt_node.upload_dir", lambda project, key: folder)
    reply = folder / "gpt_reply.txt"
    reply.write_text("ответ", encoding="utf-8")
    if not fresh:
        old = time.time() - 3600
        os.utime(reply, (old, old))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _node_meta(key: str, reply_at: float) -> dict:
    """Данные проекта, как их оставляет save_operator_result после ответа GPT."""
    return {"gpt_operator_results": {key: {"at": _iso(reply_at), "gateStatus": None}},
            "excel_gpt_nodes": {key: {"lastReplyAt": _iso(reply_at)}}}


async def _poll_gpt(key: str, meta: dict | None):
    ctx = _Ctx("enrich_2_ready", meta, node_runs=[{"node_key": key, "status": "done"}])
    params = {"project_id": 7, "step_code": "excel_gpt", "node_key": key}
    return await _poll_any_step(ctx, params, _started("enriching_2", "enrich_1_ready"))


async def test_gpt_node_with_a_real_call_after_launch_is_done(studio, monkeypatch, tmp_path) -> None:
    # Файлы в папке ноды старые, но ответ модели сохранён после запуска: GPT вызывался.
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    state = await _poll_gpt("n_excel_gpt_2", _node_meta("n_excel_gpt_2", time.time() - 5))
    assert state.done and state.ok and "завершена" in state.result["text_ru"]


async def test_gpt_node_healed_without_a_call_is_not_done(studio, monkeypatch, tmp_path) -> None:
    # Реальный случай: тик воркера пропустил GPT (нода в completed_keys), NodeRun вылечен в done.
    # Ответ ноды остался от прошлого прогона, свежий файл в папке (копия хранилища) не вызов GPT.
    _gpt_node(monkeypatch, tmp_path, fresh=True)
    meta = _node_meta("n_excel_gpt_2", time.time() - 7200)
    meta["excel_gpt_completed_keys"] = ["n_excel_gpt_1", "n_excel_gpt_2"]
    state = await _poll_gpt("n_excel_gpt_2", meta)
    assert state.done and not state.ok
    assert state.error.endswith("не выполнена: GPT не вызывался, Студия сочла ноду уже выполненной "
                                "и оставила прежний ответ")
    assert "похоже" not in state.error


async def test_gpt_node_never_answered_and_healed_is_not_done(studio, monkeypatch, tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=True)
    meta = {"excel_gpt_nodes": {"n_excel_gpt_2": {"model": "gpt-5"}}}
    state = await _poll_gpt("n_excel_gpt_2", meta)
    assert not state.ok and "GPT не вызывался" in state.error


async def test_answer_of_another_node_does_not_count(studio, monkeypatch, tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    state = await _poll_gpt("n_excel_gpt_2", _node_meta("n_excel_gpt_1", time.time() - 5))
    assert not state.ok and "GPT не вызывался" in state.error


@pytest.mark.parametrize("key", ["n_fw_report", "n_excel_gpt_fw_report_2", "n_fw_style"])
async def test_code_nodes_are_never_reported_as_skipped(studio, monkeypatch, tmp_path, key) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    state = await _poll_gpt(key, _node_meta(key, time.time() - 7200))
    assert state.done and state.ok
    assert await so.gpt_skipped(_Ctx("enrich_2_ready", {"x": 1}), {"project_id": 7, "node_key": key},
                                _started()) is None


def _trace_run(tmp_path, key: str, *, started: datetime, calls: int) -> None:
    run = (tmp_path / "data" / "videos" / "project-7" / "node_trace" / key
           / started.strftime("%Y%m%d-%H%M%S-000000-1"))
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"node_key": key, "started": started.isoformat(timespec="seconds"),
                                              "status": "ok", "calls": calls}), encoding="utf-8")


async def test_node_diary_with_calls_after_launch_counts_as_a_call(studio, monkeypatch, tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    _trace_run(tmp_path, "n_excel_gpt_2", started=datetime.now() - timedelta(seconds=20), calls=3)
    state = await _poll_gpt("n_excel_gpt_2", _node_meta("n_excel_gpt_2", time.time() - 7200))
    assert state.ok


async def test_node_diary_without_calls_or_from_before_launch_is_not_a_call(studio, monkeypatch,
                                                                             tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    _trace_run(tmp_path, "n_excel_gpt_2", started=datetime.now() - timedelta(seconds=20), calls=0)
    _trace_run(tmp_path, "n_excel_gpt_2", started=datetime.now() - timedelta(hours=2), calls=4)
    state = await _poll_gpt("n_excel_gpt_2", _node_meta("n_excel_gpt_2", time.time() - 7200))
    assert not state.ok and "GPT не вызывался" in state.error


async def _master_project(env, meta: dict, slug: str) -> int:
    async with env.factory() as s:
        p = Project(topic="совы", title="Совы", slug=slug, status=ProjectStatus("enrich_2_ready"), meta=meta)
        s.add(p)
        await s.commit()
        return p.id


def _obs(pid: int, key: str, kind: str, at: datetime, n: int):
    from app.services.observation import ObservationEvent

    return ObservationEvent(event_id=f"e-{n}", execution_id="x-1", project_id=pid, workflow_run_id=1,
                            node_key=key, request_id=f"0000000{n}-uuid", db_scope="master", kind=kind,
                            payload={}, created_at=at)


async def test_model_call_of_the_node_in_the_observation_journal_counts(env, studio) -> None:
    from app.services import spend_guard

    meta = _node_meta("n_excel_gpt_2", time.time() - 7200)
    pid = await _master_project(env, meta, "sovy-skip")
    now = datetime.utcnow()
    async with env.factory() as s:
        # После запуска: вызов модели другой нодой и прочие события этой ноды без вызова модели;
        # вызов модели этой нодой был задолго до запуска.
        s.add_all([_obs(pid, "n_excel_gpt_1", "model_start", now - timedelta(seconds=10), 1),
                   _obs(pid, "n_excel_gpt_2", "node_start", now - timedelta(seconds=10), 2),
                   _obs(pid, "n_excel_gpt_2", "node_end", now - timedelta(seconds=9), 3),
                   _obs(pid, "n_excel_gpt_2", "model_end", now - timedelta(hours=2), 4)])
        # Запись трат такта воркера после запуска: id такта, к ноде она не привязана.
        s.add(spend_guard.SpendLedger(created_at=now - timedelta(seconds=5), day=now.date().isoformat(),
                                      kind="estimated", provider="kie", project_id=pid, run_id=f"native:{pid}:a1",
                                      source="native_call", usd=0.01, credits=0.0, note="",
                                      request_id="tick-0123456789abcdef", ref=""))
        await s.commit()
    started = _started("enriching_2", "enrich_1_ready")
    ctx = _Ctx("enrich_2_ready", meta)
    ctx.detail["id"] = pid
    params = {"project_id": pid, "node_key": "n_excel_gpt_2"}
    assert "GPT не вызывался" in await so.gpt_skipped(ctx, params, started)
    async with env.factory() as s:
        s.add(_obs(pid, "n_excel_gpt_2", "model_start", datetime.utcnow() - timedelta(seconds=5), 5))
        await s.commit()
    assert await so.gpt_skipped(ctx, params, started) is None


async def test_fresh_answer_in_project_db_counts_before_the_master_copy(env, studio) -> None:
    # Воркер сначала коммитит project.db (detail и NodeRun уже готовы), мастер догоняет позже,
    # а gpt_operator_results в мастер не копируется вовсе: старая копия в мастере не делает вызов пропуском.
    stale = _node_meta("n_excel_gpt_2", time.time() - 7200)
    pid = await _master_project(env, stale, "sovy-split")
    ctx = _Ctx("enrich_2_ready", _node_meta("n_excel_gpt_2", time.time() - 5))
    ctx.detail["id"] = pid
    params = {"project_id": pid, "node_key": "n_excel_gpt_2"}
    started = _started("enriching_2", "enrich_1_ready")
    assert await so.gpt_skipped(ctx, params, started) is None
    ctx.detail["meta"] = stale
    assert "GPT не вызывался" in await so.gpt_skipped(ctx, params, started)


async def test_without_project_data_falls_back_to_files_and_says_so(studio, monkeypatch, tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=False)
    state = await _poll_gpt("n5", None)
    assert state.done and not state.ok
    assert "Нода «n5» не выполнена: похоже, GPT не вызывался" in state.error
    assert "в папке ноды нет файлов новее запуска" in state.error


async def test_without_project_data_a_fresh_file_keeps_the_node_done(studio, monkeypatch, tmp_path) -> None:
    _gpt_node(monkeypatch, tmp_path, fresh=True)
    state = await _poll_gpt("n5", None)
    assert state.ok and "завершена" in state.result["text_ru"]


async def test_running_poll_passes_through(studio) -> None:
    state = await so.honest_state(_Ctx("generating_images"), _IMG_PR, _started(),
                                  JobState(done=False, note="статус проекта generating_images"))
    assert not state.done and state.ok
