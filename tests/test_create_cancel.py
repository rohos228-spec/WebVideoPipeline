import asyncio
import uuid
from pathlib import Path
import pytest
from app.db import session_scope
from app.services import credit_ledger as cl
from app.services.create_jobs import enqueue_generation, cancel_job


@pytest.mark.asyncio
async def test_cancel_job_with_hold_sets_friendly_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.generation_storage.generations_root", lambda: tmp_path)

    tenant = str(uuid.uuid4())
    async with session_scope() as s:
        await cl.topup(s, tenant, 10_000_000)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="test", amount_micro=50_000)
        hold_id = hold.id

    async def mock_run(out_path: Path):
        try:
            await asyncio.sleep(10.0)
        except BaseException:
            # Runner wrapper tries to release hold on abort:
            async with session_scope() as s:
                try:
                    await cl.release_hold(s, hold_id, memo="Сбой/отмена")
                except Exception:
                    pass
            raise

    job = await enqueue_generation(
        media="image",
        model="nano-banana-2",
        provider="outsee",
        prompt="A photo of a cat",
        ext=".png",
        params={},
        quote=None,
        run=mock_run,
        hold_id=hold_id,
    )

    # Allow task to start running
    await asyncio.sleep(0.05)

    ok = await cancel_job(job.id)
    assert ok is True

    # Allow cancellation to process
    await asyncio.sleep(0.05)

    assert job.status == "failed"
    assert job.error == "Генерация отменена пользователем"
    assert "уже закрыт" not in job.error
