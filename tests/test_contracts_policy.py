"""Этап 5, D.1: repair-retry политика run_with_contract."""

from __future__ import annotations

import pytest

from app.contracts import APPLY_OPS, LlmContractError
from app.contracts.policy import FEEDBACK_HEADER, run_with_contract

OK = '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"текст"}}]}'
BAD_JSON = "Извините, вот отчёт без JSON."
BAD_SCHEMA = '{"ops":[{"frame_uuid":"u1","fields":{"чужое_поле":"x"}}]}'


def _seq_call(replies: list[str], seen: list[str | None]):
    it = iter(replies)

    async def call(feedback: str | None) -> str:
        seen.append(feedback)
        return next(it)

    return call


@pytest.mark.asyncio
async def test_success_first_try() -> None:
    seen: list[str | None] = []
    res = await run_with_contract(
        contract=APPLY_OPS, call=_seq_call([OK], seen), label="t"
    )
    assert res.attempts == 1 and res.repairs == 0
    assert res.payload.frame_uuids() == ["u1"]
    assert seen == [None]


@pytest.mark.asyncio
async def test_parse_fail_then_repair_with_feedback(tmp_path) -> None:
    seen: list[str | None] = []
    res = await run_with_contract(
        contract=APPLY_OPS,
        call=_seq_call([BAD_JSON, OK], seen),
        reject_dir=tmp_path,
        label="batch1",
    )
    assert res.attempts == 2 and res.parse_fails == 1
    # Вторая попытка получила фидбек с заголовком и текстом ошибки
    assert seen[1] and seen[1].startswith(FEEDBACK_HEADER)
    assert "JSON" in seen[1]
    # Отклонённый ответ сохранён на диск с заголовком причины
    assert len(res.rejected_paths) == 1
    body = res.rejected_paths[0].read_text(encoding="utf-8")
    assert "# kind: parse" in body and BAD_JSON in body


@pytest.mark.asyncio
async def test_validate_fail_semantic_layer() -> None:
    seen: list[str | None] = []

    def check_coverage(payload) -> list[str]:
        missing = {"u1", "u2"} - set(payload.frame_uuids())
        return [f"нет ops для uuid {sorted(missing)}"] if missing else []

    res = await run_with_contract(
        contract=APPLY_OPS,
        call=_seq_call(
            [OK, '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"а"}},'
                 '{"frame_uuid":"u2","fields":{"закадр":"б"}}]}'],
            seen,
        ),
        validate=check_coverage,
    )
    assert res.attempts == 2 and res.validate_fails == 1
    assert "u2" in (seen[1] or "")


@pytest.mark.asyncio
async def test_exhaustion_fail_closed(tmp_path) -> None:
    seen: list[str | None] = []
    with pytest.raises(LlmContractError) as ei:
        await run_with_contract(
            contract=APPLY_OPS,
            call=_seq_call([BAD_JSON, BAD_JSON, BAD_JSON, BAD_JSON], seen),
            reject_dir=tmp_path,
            parse_limit=2,
            label="x",
        )
    err = ei.value
    assert "repair-лимит исчерпан" in str(err)
    assert err.detail["attempts"] == 3  # 1 + parse_limit
    assert len(err.detail["rejected_paths"]) == 3


@pytest.mark.asyncio
async def test_limits_are_separate() -> None:
    # parse_limit=0: первый же parse-fail = исчерпание, validate не тратится
    with pytest.raises(LlmContractError):
        await run_with_contract(
            contract=APPLY_OPS,
            call=_seq_call([BAD_JSON], []),
            parse_limit=0,
            validate_limit=5,
        )


@pytest.mark.asyncio
async def test_transport_errors_pass_through() -> None:
    async def call(feedback):
        raise TimeoutError("сеть упала")

    with pytest.raises(TimeoutError):
        await run_with_contract(contract=APPLY_OPS, call=call)


@pytest.mark.asyncio
async def test_metrics_shape() -> None:
    res = await run_with_contract(
        contract=APPLY_OPS, call=_seq_call([BAD_SCHEMA, OK], [])
    )
    assert res.metrics() == {
        "attempts": 2,
        "repairs": 1,
        "parse_fails": 0,
        "validate_fails": 1,
    }


@pytest.mark.asyncio
async def test_rejects_retention(tmp_path, monkeypatch) -> None:
    from app.contracts import policy as pol

    monkeypatch.setattr(pol, "_REJECTS_KEEP", 3)
    for i in range(6):
        with pytest.raises(LlmContractError):
            await run_with_contract(
                contract=APPLY_OPS,
                call=_seq_call([BAD_JSON], []),
                reject_dir=tmp_path,
                parse_limit=0,
                label=f"r{i}",
            )
    assert len(list(tmp_path.glob("*.txt"))) <= 3 + 1  # keep + свежезаписанный
