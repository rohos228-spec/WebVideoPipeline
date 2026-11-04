"""Ключ остановки, поставленный kill чата, и ключ человека не путаются.

Kill пишет в ключ свою метку и хранит её в записи держателей (``project_{id}.stop.orch``).
Снять ключ при возобновлении можно только если ТЕКУЩИЙ ключ несёт эту метку: ключ, который
воркер снял, а человек поставил заново кнопкой ⏹, чужой — и без рестарта, и после него.
Сеть и провайдеры не нужны: только файлы ключей во временной data_dir.
"""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime
from app.services import step_cancel as sc
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

P = 7


def _sup():
    return build_runtime(
        actions=MockActions(), validators=MockValidators(), router=ScriptedRouter()
    ).supervisor


@pytest.fixture(autouse=True)
def _clean() -> None:
    sc.clear_stop(P)
    yield
    sc.clear_stop(P)


def _restart() -> None:
    sc._stop_pids.clear()


def test_worker_consumed_then_user_stop_survives_resume_without_restart(token: str) -> None:
    del token
    sup = _sup()
    sup._note_kill_stop(P, 42)
    assert sc.is_stop_requested(P)
    assert sc.consume_stop(P)  # воркер снял ключ kill
    assert not sc.holder_path(P).exists()  # и запись о нём ушла вместе с ключом
    sc.request_stop(P, reason="user_stop")  # человек нажал ⏹
    sup._clear_one_project_stop(42, P)  # задача 42 возобновилась в том же процессе
    assert sc.is_stop_requested(P)


def test_worker_consumed_then_user_stop_survives_resume_after_restart(token: str) -> None:
    del token
    _sup()._note_kill_stop(P, 42)
    sc.consume_stop(P)
    sc.request_stop(P, reason="user_stop")
    _restart()
    _sup()._clear_one_project_stop(42, P)
    assert sc.is_stop_requested(P)


def test_later_kill_does_not_take_over_the_users_key(token: str) -> None:
    del token
    sup = _sup()
    sup._note_kill_stop(P, 42)
    sc.consume_stop(P)
    sc.request_stop(P, reason="user_stop")
    sup._note_kill_stop(P, 43)  # новый kill после ⏹ человека
    sup._clear_one_project_stop(43, P)
    sup._clear_one_project_stop(42, P)
    assert sc.is_stop_requested(P)
    assert sc.stop_token(P) == "1"


def test_two_tasks_user_restop_then_restart_keeps_users_key(token: str) -> None:
    del token
    sup = _sup()
    sup._note_kill_stop(P, 42)
    sup._note_kill_stop(P, 43)
    sc.consume_stop(P)
    sc.request_stop(P, reason="user_stop")
    sup._clear_one_project_stop(42, P)
    assert sc.is_stop_requested(P)
    _restart()
    _sup()._clear_one_project_stop(43, P)
    assert sc.is_stop_requested(P)


def test_two_tasks_share_the_key_and_the_last_resume_clears_it_after_restart(token: str) -> None:
    del token
    sup = _sup()
    sup._note_kill_stop(P, 42)
    sup._note_kill_stop(P, 43)
    sup._clear_one_project_stop(42, P)
    assert sc.is_stop_requested(P)  # 43 ещё держит
    _restart()
    fresh = _sup()
    fresh._clear_one_project_stop(43, P)
    assert not sc.is_stop_requested(P)
    assert not sc.holder_path(P).exists()


def test_key_already_set_by_the_user_is_never_ours(token: str) -> None:
    del token
    sc.request_stop(P, reason="user_stop")
    sup = _sup()
    sup._note_kill_stop(P, 42)
    sup._clear_one_project_stop(42, P)
    assert sc.is_stop_requested(P)


@pytest.mark.parametrize("how", ["clear_stop", "consume_stop", "clear_all"])
def test_holder_record_is_removed_with_the_key(token: str, how: str) -> None:
    del token
    _sup()._note_kill_stop(P, 50)
    assert sc.holder_path(P).exists()
    if how == "clear_stop":
        sc.clear_stop(P)
    elif how == "consume_stop":
        assert sc.consume_stop(P)
    else:
        sc.clear_all()
    assert not sc.holder_path(P).exists()
    assert not sc.is_stop_requested(P)
