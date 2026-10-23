"""Маска участника не трогает его кредиты, параметры и не-денежные ключи (ревью: over-masking).

Падает на прежнем правиле по префиксам (^cost, ^credit, ^cap_, ^remaining…): из превью
слоя действий пропадали cost.{low,high}, из каталога — ceiling_credits в properties.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.web.member_money import MemberMoneyMiddleware, mask_amounts, member_safe

PREVIEW = {
    "ok": True,
    "cost": {"low": 12.0, "high": 40.0, "known": True, "unknown_steps": 0, "actual": 18.5,
             "high_usd": 0.4},
    "cost_estimate": {"low": 12.0, "high": 40.0},
    "cost_recorded": True,
    "steps": [{"action_id": "hitl.regenerate", "params": {"ceiling_credits": 40, "note": "за $5 не надо"}}],
    "free_credits": 3, "balance_credits": "10", "cap": 2, "caps": [1], "max_cap": 4, "remaining": 1,
    "remaining_steps": 2, "unknown_cost": False, "cost_known": True, "step_cost_declared": True,
    "amount_frames": 6, "budget_exhausted": False, "has_cost": True,
}
CATALOG = {"actions": [{"id": "hitl.regenerate", "params_schema": {
    "type": "object", "required": ["project_id", "ceiling_credits"],
    "properties": {"project_id": {"type": "integer"}, "ceiling_credits": {"type": "number"}}}}]}


def test_action_layer_preview_keeps_credits_and_flags() -> None:
    safe = member_safe(PREVIEW)
    assert safe["cost"] == {"low": 12.0, "high": 40.0, "known": True, "unknown_steps": 0, "actual": 18.5}
    assert safe["cost_estimate"] == {"low": 12.0, "high": 40.0} and safe["cost_recorded"] is True
    assert safe["steps"][0]["params"] == {"ceiling_credits": 40, "note": "за $5 не надо"}  # слова человека
    for key in ("free_credits", "balance_credits", "cap", "caps", "max_cap", "remaining", "remaining_steps",
                "unknown_cost", "cost_known", "step_cost_declared", "amount_frames", "budget_exhausted",
                "has_cost"):
        assert key in safe, key


def test_catalog_param_schema_is_never_masked() -> None:
    assert member_safe(CATALOG) == CATALOG


@pytest.mark.parametrize(
    "text",
    ["итого 1,000.50 USD", "бюджет $100k", "около 5 дол.", "10 баксов", "7 у.е.", "€12", "12 €",
     "$1 200,50", "48.20 USD"],
)
def test_amounts_masked_whole(text) -> None:
    out = mask_amounts(text)
    assert not any(ch.isdigit() for ch in out), out


async def test_middleware_keeps_member_credits_and_catalog(monkeypatch) -> None:
    import app.services.action_layer.plan as plan

    monkeypatch.setattr(plan, "member_view", lambda: True)

    async def preview(_request):
        return JSONResponse({"preview": PREVIEW})

    async def catalog(_request):
        return JSONResponse(CATALOG)

    app = MemberMoneyMiddleware(Starlette(routes=[Route("/api/action-layer/plans/preview", preview, methods=["POST"]),
                                                   Route("/api/action-layer/catalog", catalog)]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.post("/api/action-layer/plans/preview")).json()["preview"]
        assert body["cost"]["high"] == 40.0 and "high_usd" not in body["cost"]
        assert (await client.get("/api/action-layer/catalog")).json() == CATALOG


def test_member_fixture_of_the_ui_e2e_matches_the_real_mask() -> None:
    """Фикстура e2e участника (web/e2e/orchestrator/member-action-layer.json) — это ровно то,
    что пропускает member_safe: e2e рисует настоящие формы ответа участника."""
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parents[1] / "web/e2e/orchestrator/member-action-layer.json")
                      .read_text(encoding="utf-8"))
    for name, full in data["full"].items():
        assert member_safe(full) == data["member"][name], name


def test_chat_plan_step_params_lose_usd_estimates_only() -> None:
    """Чекпоинт и события чата с планом: estimated_cost / *_usd внутри params шага — нет;
    прочие параметры и params слоя действий — как есть."""
    from app.web.member_money import member_event_payload

    step = {"action": "step.run_step", "estimated_cost": 0.42,
            "params": {"project_id": 13, "step_code": "images", "estimated_cost": 0.42, "ceiling_usd": 1.0,
                       "ceiling_credits": 40, "_cost_unknown": False}}
    out = member_event_payload("checkpoint", {"position": 0, "plan": [step], "spend": {"internal": 1.0}})
    params = out["plan"][0]["params"]
    assert params == {"project_id": 13, "step_code": "images", "ceiling_credits": 40, "_cost_unknown": False}
    assert "estimated_cost" not in out["plan"][0] and "spend" not in out
    plan_event = member_event_payload("plan", {"steps": [step]})
    assert "estimated_cost" not in plan_event["steps"][0]["params"]
    # Слой действий (chat=False): params не трогаются.
    assert member_safe({"params": dict(step["params"])})["params"] == step["params"]

