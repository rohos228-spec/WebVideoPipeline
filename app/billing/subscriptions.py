"""Подписки (тарифы) и активные лимиты пользователя."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.billing.db import connect, now_iso
from app.billing.plans import FREE_STREAMS, Plan, StreamLimits, get_plan


def activate(conn, user_id: str, plan: Plan, period: str, payment_id: str | None) -> None:
    start = datetime.now(UTC)
    end = start + timedelta(days=365 if period == "year" else 30)
    conn.execute(
        "INSERT INTO subscriptions(user_id, plan, period, started_at, expires_at, payment_id) VALUES (?,?,?,?,?,?)",
        (str(user_id), plan.id, period, start.isoformat(timespec="seconds"),
         end.isoformat(timespec="seconds"), payment_id),
    )


def current(user_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND expires_at > ? ORDER BY id DESC LIMIT 1",
            (str(user_id), now_iso()),
        ).fetchone()
    return dict(row) if row else None


def streams_for(user_id: str) -> tuple[Plan | None, StreamLimits]:
    sub = current(user_id)
    plan = get_plan(sub["plan"]) if sub else None
    return plan, (plan.streams if plan else FREE_STREAMS)


def describe(user_id: str) -> dict:
    sub = current(user_id)
    plan, streams = streams_for(user_id)
    return {
        "active": bool(sub and plan),
        "plan": plan.id if plan else None,
        "plan_name": plan.name if plan else None,
        "period": sub["period"] if sub else None,
        "expires_at": sub["expires_at"] if sub else None,
        "streams": {"llm": streams.llm, "agents": streams.agents, "images": streams.images, "video": streams.video},
        "thread_limit": streams.video,
    }
