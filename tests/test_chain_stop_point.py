"""Точка остановки цепочки нод «Работа с GPT» держится в коде чата, а итог цепочки называет
ноду, на которой она встала, и причину из данных Студии."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.action_layer import builtin_launch as bl
from app.services.action_layer import chain_stop as cs
from app.services.action_layer import step_outcome as so
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import JobState

PID = 6
B, S, R, G1 = ("n_excel_gpt_fw_boundaries", "n_excel_gpt_fw_script", "n_excel_gpt_fw_report",
               "n_excel_gpt_1")
RUN = {"project_id": PID, "step_code": "excel_gpt", "node_key": B, "stop_after_node_key": R}


def _node(key: str, typ: str, label: str) -> dict:
    return {"id": key, "type": typ, "data": {"label": label}}


def _meta(**extra) -> dict:
    nodes = [_node(B, "excel_gpt", "Границы сцен"), _node(S, "excel_gpt", "Каркас"),
             _node(R, "excel_gpt", "Отчёт: кадры"), _node(G1, "excel_gpt", "Работа с GPT"),
             _node("n_hero", "hero", "Персонажи")]
    keys = [n["id"] for n in nodes]
    edges = [{"source": a, "target": b} for a, b in zip(keys, keys[1:], strict=False)]
    return {"canvas_graph": {"nodes": nodes, "edges": edges}, **extra}


def _iso(delta_s: float = 0.0) -> str:
    # Как Студия: datetime.utcnow() без зоны.
    return (datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=delta_s)).isoformat()


def _run(key: str, status: str, finished: str | None = None, error: str | None = None,
         updated: str | None = None) -> dict:
    # updated_at Студия пишет при каждой смене статуса; по умолчанию - только что.
    return {"node_key": key, "status": status, "finished_at": finished, "error": error,
            "updated_at": updated or _iso(-1)}


class _Ctx:
    def __init__(self, status: str, runs: list[dict], meta: dict | None = None,
                 stop_fails: bool = False) -> None:
        self.project = {"id": PID, "title": "дельфины", "status": status,
                        "meta": meta if meta is not None else _meta()}
        self.runs = runs
        self.stop_fails = stop_fails
        self.calls: list[tuple[str, str, dict | None]] = []

    async def call(self, method: str, path: str, **kw):
        if method == "GET" and path == f"/projects/{PID}/run":
            return {"node_runs": self.runs}
        if method == "GET":
            return self.project
        self.calls.append((method, path, kw.get("json") or kw.get("params")))
        if method == "PATCH":
            self.project["meta"] = {**self.project["meta"], **kw["json"]["meta"]}
        if path == f"/projects/{PID}/stop":
            if self.stop_fails:
                raise ActionCallError(500, "нет связи со Студией")
            self.project["status"] = "enrich_7_ready"
            return {"message": "остановлен"}
        return {}


def _started(**extra) -> dict:
    return {"started": True, "step_code": "excel_gpt", "node_key": B, "stop_after_node_key": R,
            "launched_at": time.time() - 60, **extra}


@pytest.fixture(autouse=True)
def _no_gpt_folder(monkeypatch):
    async def _none(ctx, params, started):
        return None
    monkeypatch.setattr(so, "gpt_skipped", _none)


def _stops(ctx: _Ctx) -> list:
    return [c for c in ctx.calls if c[1] == f"/projects/{PID}/stop"]


# --- точка остановки в коде ---------------------------------------------------------------

async def test_studio_running_past_the_stop_node_is_stopped_and_said() -> None:
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5)),
            _run(G1, "running")]
    ctx = _Ctx("enriching_1", runs)
    started = _started()
    state = await bl._poll_any_step(ctx, dict(RUN), started)
    assert state.done and state.ok
    assert len(_stops(ctx)) == 1
    text = state.result["text_ru"]
    assert "Цепочка нод от «Границы сцен» до «Отчёт: кадры» завершена" in text
    assert "Студия пошла дальше ноды «Отчёт: кадры» и запустила «Работа с GPT» (n_excel_gpt_1)" in text
    assert "чат остановил проект" in text
    assert state.result["overrun_node_key"] == G1 and state.result["project_stopped"] is True
    assert cs._SEEN_KEY not in started


async def test_auto_advance_to_another_step_is_caught_by_the_project_status() -> None:
    # NodeRun героев ещё не в работе, но проект уже идёт шагом «Персонажи».
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5)),
            _run("n_hero", "pending")]
    ctx = _Ctx("generating_hero", runs, meta=_meta(auto_mode=True))
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.done and state.ok and len(_stops(ctx)) == 1
    assert "запустила «Персонажи» (n_hero)" in state.result["text_ru"]


async def test_failed_stop_after_overrun_is_an_error_with_a_hint() -> None:
    runs = [_run(R, "done", _iso(-5)), _run(G1, "running")]
    ctx = _Ctx("enriching_1", runs, stop_fails=True)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.done and not state.ok
    assert "остановить проект не удалось: нажмите «Стоп» в Студии" in state.error
    assert state.error.startswith("#6 «дельфины»: Цепочка нод")


async def test_chain_in_progress_is_not_stopped(monkeypatch) -> None:
    runs = [_run(B, "done", _iso(-30)), _run(S, "running"), _run(R, "pending")]
    ctx = _Ctx("enriching_1", runs)
    started = _started()
    state = await bl._poll_any_step(ctx, dict(RUN), started)
    assert not state.done and _stops(ctx) == []
    assert started[cs._SEEN_KEY] == [S]


async def test_stop_node_rerun_by_the_studio_check_is_not_an_overrun() -> None:
    runs = [_run(B, "done", _iso(-30)), _run(R, "running", _iso(-5))]
    ctx = _Ctx("enriching_1", runs, meta=_meta())
    started = _started(chain_seen_active=[R])
    assert await bl._guard_stop_point(ctx, dict(RUN), started) is None
    assert _stops(ctx) == []


async def test_stop_node_done_before_this_launch_does_not_count() -> None:
    # Отчёт готов с прошлого прогона, цепочка сейчас на «Каркасе».
    runs = [_run(B, "done", _iso(-30)), _run(S, "running"), _run(R, "done", _iso(-3600))]
    ctx = _Ctx("enriching_1", runs)
    assert await bl._guard_stop_point(ctx, dict(RUN), _started()) is None
    assert _stops(ctx) == []


async def test_fast_stop_node_seen_running_counts_as_done_in_this_launch() -> None:
    # Код-нода отчёта быстрая и finished_at остался старым: видели её в работе - она из этого запуска.
    started = _started()
    cs.observe(started, [_run(R, "running")])
    runs = [_run(R, "done", _iso(-3600)), _run(G1, "queued")]
    assert cs.overrun_node(R, runs, started, "enriching_1", _meta()) == G1


async def test_without_stop_point_the_guard_does_nothing() -> None:
    ctx = _Ctx("enriching_1", [_run(R, "done", _iso(-5)), _run(G1, "running")])
    params = {k: v for k, v in RUN.items() if k != "stop_after_node_key"}
    assert await bl._guard_stop_point(ctx, params, _started()) is None
    assert ctx.calls == []


async def test_stale_running_row_off_the_chain_is_not_an_overrun() -> None:
    # Залипшая строка чужой ноды (её нет после ноды-остановки) - проект не трогаем.
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5)),
            _run("n_stale", "running", updated=_iso(-7200))]
    ctx = _Ctx("enriching_3", runs)
    assert await bl._guard_stop_point(ctx, dict(RUN), _started()) is None
    assert _stops(ctx) == []


async def test_running_node_before_the_stop_node_is_not_an_overrun() -> None:
    # Нода выше ноды-остановки в работе (например, её перезапустили) - это не «дальше».
    runs = [_run(B, "running"), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5))]
    ctx = _Ctx("enriching_1", runs)
    assert await bl._guard_stop_point(ctx, dict(RUN), _started()) is None
    assert _stops(ctx) == []


async def test_downstream_row_stuck_since_before_the_launch_is_not_an_overrun() -> None:
    runs = [_run(R, "done", _iso(-5)), _run(G1, "running", updated=_iso(-3600))]
    ctx = _Ctx("enriching_3", runs)
    assert await bl._guard_stop_point(ctx, dict(RUN), _started()) is None
    assert _stops(ctx) == []


async def test_non_gpt_step_not_after_the_stop_node_is_not_an_overrun() -> None:
    # Проект идёт шагом героев, но нода «Персонажи» на холсте стоит до ноды-остановки.
    nodes = [_node("n_hero", "hero", "Персонажи"), _node(B, "excel_gpt", "Границы сцен"),
             _node(S, "excel_gpt", "Каркас"), _node(R, "excel_gpt", "Отчёт: кадры")]
    keys = [n["id"] for n in nodes]
    meta = {"canvas_graph": {"nodes": nodes,
                             "edges": [{"source": a, "target": b} for a, b in zip(keys, keys[1:], strict=False)]}}
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5))]
    ctx = _Ctx("generating_hero", runs, meta=meta)
    assert await bl._guard_stop_point(ctx, dict(RUN), _started()) is None
    assert _stops(ctx) == []


async def test_check_node_fix_loop_is_its_own_work_not_an_overrun() -> None:
    # Нода-остановка - проверка: Студия сама запустила fail -> патч -> переген героев,
    # потом снова проверку. Это работа самой проверки, чат её не обрывает.
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5)),
            _run("n_hero", "running")]
    meta = _meta(vision_check_return_node=R, hero_check_return_node=R, vision_check_kind="hero")
    ctx = _Ctx("generating_hero", runs, meta=meta)
    started = _started()
    assert await bl._guard_stop_point(ctx, dict(RUN), started) is None
    assert _stops(ctx) == []
    assert cs.overrun_node(R, runs, started, "generating_hero", meta) is None
    # Цикл другой ноды-проверки - не наш: выход за точку остановки ловится.
    other = _meta(vision_check_return_node=G1)
    assert cs.overrun_node(R, runs, started, "generating_hero", other) == "n_hero"


def test_downstream_keys_follow_the_arrows_from_the_stop_node() -> None:
    assert cs.downstream_keys(_meta(), R) == {G1, "n_hero"}
    assert cs.downstream_keys(_meta(), "n_hero") == set()
    assert cs.downstream_keys(None, R) == set()


def test_finished_at_is_read_as_utc() -> None:
    now = time.time()
    naive = datetime.fromtimestamp(now, UTC).replace(tzinfo=None).isoformat()
    assert abs(cs._epoch(naive) - now) < 1
    assert abs(cs._epoch(naive + "Z") - now) < 1
    assert cs._epoch("вчера") is None and cs._epoch(None) is None


# --- честный итог цепочки ------------------------------------------------------------------

def _poll_returns(monkeypatch, state: JobState) -> None:
    async def _poll(ctx, params, started):
        return state
    monkeypatch.setattr(bl, "_node_run_poll", _poll)


async def test_mid_chain_failure_names_the_node_and_its_error(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "failed"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "failed", _iso(-10), "превышен лимит токенов"),
            _run(R, "pending")]
    ctx = _Ctx("failed", runs, meta=_meta(**{bl.STOP_META_KEY: R}))
    started = _started()
    state = await bl._poll_any_step(ctx, dict(RUN), started)
    assert state.done and not state.ok
    assert state.error == ("#6 «дельфины»: Цепочка нод от «Границы сцен» до «Отчёт: кадры» не "
                           "выполнена: последней готова нода «Границы сцен», цепочка встала на ноде "
                           "«Каркас» (n_excel_gpt_fw_script): завершилась с ошибкой. Причина: "
                           "превышен лимит токенов")
    assert ctx.project["meta"][bl.STOP_META_KEY] is None
    assert cs._SEEN_KEY not in started


async def test_pause_mid_chain_names_the_node_status_and_the_studio_reason(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "paused"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "waiting_approval"), _run(R, "pending")]
    ctx = _Ctx("paused", runs, meta=_meta(auto_paused_reason="потолок трат цепочки исчерпан"))
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert not state.ok
    assert ("цепочка встала на ноде «Каркас» (n_excel_gpt_fw_script): ждёт одобрения трат; "
            "проект на паузе. Причина: потолок трат цепочки исчерпан") in state.error


async def test_pause_after_a_node_error_keeps_the_pause_in_the_text(monkeypatch) -> None:
    # Студия ставит паузу с ожиданием повтора и помечает ноду failed с той же ошибкой.
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "paused"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "failed", _iso(-10), "429 rate limit"),
            _run(R, "pending")]
    meta = _meta(step_failure={"last_error": "429 rate limit", "sleep_until": _iso(600)})
    ctx = _Ctx("paused", runs, meta=meta)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert not state.ok
    assert state.error.endswith(
        "цепочка встала на ноде «Каркас» (n_excel_gpt_fw_script): завершилась с ошибкой; "
        "проект на паузе. Причина: 429 rate limit")


async def test_pause_with_a_different_reason_says_both(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "paused"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "failed", _iso(-10), "таймаут Vibecode"),
            _run(R, "pending")]
    ctx = _Ctx("paused", runs, meta=_meta(auto_paused_reason="потолок трат исчерпан"))
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.error.endswith(
        "завершилась с ошибкой. Ошибка ноды: таймаут Vibecode; проект на паузе. "
        "Причина: потолок трат исчерпан")


async def test_failure_on_the_first_node_says_nothing_was_done(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=False, error="ответ модели пустой"))
    runs = [_run(B, "failed", _iso(-5), "ответ модели пустой"), _run(S, "pending"), _run(R, "pending")]
    ctx = _Ctx("failed", runs)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert ("не готова ни одна нода цепочки, встала на первой «Границы сцен» "
            "(n_excel_gpt_fw_boundaries): завершилась с ошибкой. Причина: ответ модели пустой") in state.error


async def test_chain_node_failed_in_this_launch_turns_a_ready_project_into_an_error(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "enrich_7_ready"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "failed", _iso(-10), "таймаут Vibecode"),
            _run(R, "pending")]
    ctx = _Ctx("enrich_7_ready", runs)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.done and not state.ok
    assert "встала на ноде «Каркас»" in state.error and "Причина: таймаут Vibecode" in state.error


async def test_old_failure_outside_this_launch_does_not_fail_a_finished_chain(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "enrich_7_ready"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "done", _iso(-5)),
            _run(G1, "failed", _iso(-3600), "старое")]
    ctx = _Ctx("enrich_7_ready", runs)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.ok and "не дошла" not in state.result["text_ru"]


async def test_early_stop_says_the_last_done_node_and_the_studio_reason(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=True, result={"final_status": "enrich_7_ready"}))
    runs = [_run(B, "done", _iso(-30)), _run(S, "done", _iso(-20)), _run(R, "pending")]
    meta = _meta(**{bl.STOP_META_KEY: R, "step_failure": {"last_error": "проверка Студии не пропустила"}})
    ctx = _Ctx("enrich_7_ready", runs, meta=meta)
    state = await bl._poll_any_step(ctx, dict(RUN), _started())
    assert state.ok and state.result["stopped_early"] is True
    assert ("до ноды «Отчёт: кадры» цепочка не дошла: последней готова нода «Каркас», цепочка "
            "встала на ноде «Отчёт: кадры» (n_excel_gpt_fw_report): не запускалась. Причина: "
            "проверка Студии не пропустила") in state.result["text_ru"]


async def test_single_node_failure_keeps_the_plain_text(monkeypatch) -> None:
    _poll_returns(monkeypatch, JobState(done=True, ok=False, error="нода завершилась с ошибкой"))
    ctx = _Ctx("failed", [_run(G1, "failed", _iso(-5), "x")])
    params = {"project_id": PID, "step_code": "excel_gpt", "node_key": G1, "stop_after_node_key": G1}
    state = await bl._poll_any_step(ctx, params, _started())
    assert state.error == "#6 «дельфины»: Нода «Работа с GPT»: нода завершилась с ошибкой"
