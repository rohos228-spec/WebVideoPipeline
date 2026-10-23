"""spend_ledger ↔ журнал наблюдения: строка траты ссылается на id модельного вызова.

Живые данные (_chat_test2, 2026-10-22): 634 строки native_call, ни одна не
находится по request_id в observation_events (model_start/model_end), а у
проекта #12 215 вызовов делят 14 tick-id. Writer ledger брал request_id из
request_context (id такта), а журнал — свой uuid на каждый вызов.
"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import observation, request_context, spend_guard
from app.services.llm_override import NodeLlmOverride, use_override


def _ctx():
    return SimpleNamespace(scope=SimpleNamespace(mode="observe", project_id=1, execution_id="e1", gaps=[]),
        last_request_id=None, purpose="node",
        observer=SimpleNamespace(failures={}, commit_uncertainty={}, master=None))


class ObservedCallPassesItsRequestId(unittest.IsolatedAsyncioTestCase):
    async def test_cost_tracking_gets_the_journal_request_id(self):
        result = SimpleNamespace(text="ok", model="gpt-5.6-sol", response_id="r1", finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 2}, raw={"id": "r1", "finish_reason": "stop"})

        async def transport(**_kwargs):
            return result

        ctx = _ctx()
        token = observation._attempt.set(ctx)
        try:
            with patch.object(observation, "record_fact", new_callable=AsyncMock), \
                    patch.object(observation, "_track_api_cost") as cost:
                with use_override(NodeLlmOverride("gpt-5.6-sol", "stable", "text", "vibecode", "gpt-5.6-sol")):
                    await observation.observe_model(transport)(
                        url="https://vibecode.moe/v1/chat/completions",
                        body={"model": "m", "messages": []}, use_model="gpt-5.6-sol")
        finally:
            observation._attempt.reset(token)
        self.assertTrue(ctx.last_request_id)
        self.assertEqual(cost.call_args.kwargs.get("request_id"), ctx.last_request_id)


class LedgerRowCarriesModelRequestRef(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_actual_writes_ref_and_keeps_tick_correlation(self):
        record = AsyncMock()

        @asynccontextmanager
        async def _session():
            yield SimpleNamespace(commit=AsyncMock())

        with patch.object(spend_guard, "record_actual", record), \
                patch("app.db.SessionLocal", _session):
            with request_context.request_scope("tick-abc", run_id="native:1:x"):
                spend_guard.schedule_actual(project_id=1, usd=0.01, provider="vibecode",
                                            ref="0f0e0d0c-0000-4000-8000-000000000001")
            await asyncio.gather(*list(spend_guard._tasks))
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["ref"], "0f0e0d0c-0000-4000-8000-000000000001")
        self.assertEqual(kwargs["request_id"], "tick-abc")
        self.assertEqual(kwargs["run_id"], "native:1:x")


if __name__ == "__main__":
    unittest.main()
