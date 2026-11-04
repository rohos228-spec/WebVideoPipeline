"""Участник студии не получает денег из чата: настоящий результат spend_guard, чекпоинт,
текст потолка расходов, ошибки слоя действий. Падает на прежней маске (список ключей + `$\\d`)."""

from __future__ import annotations

import json
import re

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.orchestrator.contracts import CheckpointData, OrchEvent, SpendSnapshot
from app.web.member_money import MemberMoneyMiddleware, is_money_key, mask_amounts

_CURRENCY_NEAR_NUMBER = re.compile(
    r"(\$|US\s?\$|USD|usd|долл)\s?[.,]?\d|\d\s?(\$|USD|usd|долл)", re.IGNORECASE
)


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _assert_no_money(data: str) -> None:
    assert not _CURRENCY_NEAR_NUMBER.search(data), data
    parsed = json.loads(data)
    leaked = {k for k in _keys(parsed) if is_money_key(k)}
    assert not leaked, leaked
    for word in ("daily_spent", "budget_usd", "remaining_usd", "cap_usd", "pipeline_scope"):
        assert word not in data, word


@pytest.fixture
def member(monkeypatch):
    import app.services.action_layer.plan as plan

    monkeypatch.setattr(plan, "member_view", lambda: True)


def _spend_guard_result() -> dict:
    from app.orchestrator.validators import spend_guard as sg
    from app.orchestrator.validators.result import make_result

    view = {
        "spent": {
            "daily_spent_usd": 312.4,
            "project_spent_usd": 48.2,
            "run_spent_usd": 9.1,
            "internal_spent_usd": 9.9,
            "actions_spent_usd": 38.3,
        },
        "known": {
            "daily_spent_usd",
            "project_spent_usd",
            "run_spent_usd",
            "internal_spent_usd",
            "actions_spent_usd",
        },
        "caps": {"internal_cap_usd": 10.0},
        "run_id": "plan:1",
        "internal_source": "ledger",
        "internal_remaining_usd": 0.1,
        "sources": ["spend_ledger"],
        "pipeline_scope_spent_usd": 38.3,
    }
    public = sg._public(view)
    details = {
        "reason": "budget_exhausted",
        "reasons": ["budget_exhausted"],
        "violations": [
            sg._violation(
                "budget_exhausted",
                spent=9.9,
                cap=10.0,
                forecast=0.5,
                scope="internal",
                source="pipeline_scope",
            )
        ],
        "totals": public["totals"],
        "internal": public["internal"],
        "pipeline_scope_spent_usd": view["pipeline_scope_spent_usd"],
        "forecast_usd": {"internal": 0.5, "actions": 0.0},
        "sources": public["sources"],
    }
    result = make_result(name="spend_guard", passed=False, blocking=True, details=details)
    return result.model_dump(by_alias=True)


def test_real_spend_guard_result_reaches_member_without_money(member) -> None:
    from app.web.routers.orchestrator_chat import _sse

    payload = _spend_guard_result()
    assert "312.4" in json.dumps(payload)  # исходный результат несёт траты студии за день
    event = OrchEvent(type="validator_result", seq=1, task_id=1, session_id="main", payload=payload)
    data = json.loads(_sse(event)["data"])
    _assert_no_money(json.dumps(data["payload"], ensure_ascii=False))
    assert data["payload"]["name"] == "spend_guard" and data["payload"]["blocking"] is True
    assert "312" not in json.dumps(data, ensure_ascii=False)


def test_checkpoint_spend_snapshot_is_dropped_for_member(member) -> None:
    from app.web.routers.orchestrator_chat import _sse

    cp = CheckpointData(position=2, change_ids=["orch-1-1"], spend=SpendSnapshot(internal=7.25, actions=31.5))
    event = OrchEvent(type="checkpoint", seq=2, task_id=1, session_id="main", payload=cp.model_dump())
    data = json.loads(_sse(event)["data"])
    assert "spend" not in data["payload"] and data["payload"]["position"] == 2
    assert "7.25" not in json.dumps(data) and "31.5" not in json.dumps(data)


def test_admin_still_sees_spend_details(monkeypatch) -> None:
    import app.services.action_layer.plan as plan
    from app.web.routers.orchestrator_chat import _sse

    monkeypatch.setattr(plan, "member_view", lambda: False)
    event = OrchEvent(
        type="validator_result", seq=1, task_id=1, session_id="main", payload=_spend_guard_result()
    )
    assert "312.4" in _sse(event)["data"]


@pytest.mark.parametrize(
    "text",
    [
        "Локальный потолок расходов исчерпан (cap): потрачено 48.20 USD, оценка 1.50 USD, потолок 50.00 USD.",
        "стоит $.42 за кадр",
        "стоит 0.42$ за кадр",
        "лимит 10 USD",
        "около US$ 5",
        "около $1 200,50",
        "итого 3 доллара",
    ],
)
def test_amounts_masked_on_either_side(text) -> None:
    out = mask_amounts(text)
    assert not _CURRENCY_NEAR_NUMBER.search(out), out


def test_money_keys_are_usd_named_plus_chat_set() -> None:
    for key in (
        "daily_spent_usd",
        "budget_usd",
        "remaining_usd",
        "cap_usd",
        "forecast_usd",
        "usd",
        "pipeline_scope_spent_usd",
        "internal_cap_usd",
        "high_usd",
        "usd_total",
    ):
        assert is_money_key(key), key
    assert is_money_key("cost_estimate", chat=True) and is_money_key("estimated_cost", chat=True)
    # Не деньги в $: кредиты участника, лимиты в штуках, флаги.
    for key in (
        "name",
        "pass",
        "blocking",
        "title",
        "future_spend",
        "paid",
        "risk",
        "cost",
        "cost_estimate",
        "cap",
        "caps",
        "max_cap",
        "remaining",
        "remaining_steps",
        "free_credits",
        "balance_credits",
        "unknown_cost",
        "cost_known",
        "step_cost_declared",
        "amount_frames",
        "budget_exhausted",
        "has_cost",
        "credits_high",
        "ceiling_credits",
        "sound_credits",
        "cost_recorded",
    ):
        assert not is_money_key(key), key


async def test_action_layer_error_detail_is_masked_for_member(member) -> None:
    async def plan(_request):
        message = "Локальный потолок расходов исчерпан: потрачено 48.20 USD, потолок 50.00 USD."
        return JSONResponse(
            {"detail": {"code": "spend_cap", "message": message, "cap_usd": 50.0}}, status_code=503
        )

    async def other(_request):
        return JSONResponse({"detail": "потрачено 48.20 USD"})

    app = MemberMoneyMiddleware(
        Starlette(
            routes=[Route("/api/action-layer/plan", plan, methods=["POST"]), Route("/api/projects/1", other)]
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/action-layer/plan")
        assert res.status_code == 503
        _assert_no_money(res.text)
        assert res.json()["detail"]["code"] == "spend_cap"
        untouched = await client.get("/api/projects/1")
        assert "48.20 USD" in untouched.text  # чужие пути middleware не трогает
