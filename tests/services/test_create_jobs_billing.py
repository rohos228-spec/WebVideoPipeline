import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.billing import charge_hook, limits
from app.billing.ledger import InsufficientCredits
from app.services import create_jobs as cj


@pytest.fixture()
def calls(monkeypatch, tmp_path):
    log = []

    @contextlib.asynccontextmanager
    async def fake_slot(user_id, kind, job_id):
        log.append(("slot", kind))
        yield

    monkeypatch.setattr(limits, "slot", fake_slot)
    monkeypatch.setattr(charge_hook, "settle", lambda j: log.append(("settle", j)))
    monkeypatch.setattr(charge_hook, "refund", lambda j, r="": log.append(("refund", j)))
    monkeypatch.setattr(cj, "update_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(cj, "write_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(cj, "build_generation_path", lambda **k: tmp_path / "out.png")
    import app.services.generation_storage as gs

    monkeypatch.setattr(gs, "generations_root", lambda: tmp_path)
    import app.services.api_tracker_hook as th

    monkeypatch.setattr(th, "check_api_allowed", lambda **k: None)
    monkeypatch.setattr(th, "record_api_call", lambda **k: None)
    return log


async def _go(user_id, run):
    job = await cj.enqueue_generation(media="image", model="m1", provider="p", prompt=f"x{id(run)}",
                                      ext="png", params={"nonce": str(id(run))}, quote=None, run=run, user_id=user_id)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if job.status in ("done", "failed"):
            break
    return job


def test_insufficient_credits_blocks_provider(calls, monkeypatch):
    called = []

    def boom(*a, **k):
        raise InsufficientCredits(10, 0)

    monkeypatch.setattr(charge_hook, "reserve", boom)

    async def run(path):
        called.append(1)

    job = asyncio.run(_go("u1", run))
    assert job.status == "failed" and "кредит" in job.error
    assert not called


def test_error_refunds(calls, monkeypatch):
    monkeypatch.setattr(charge_hook, "reserve", lambda *a, **k: 1)

    async def run(path):
        raise RuntimeError("provider down")

    job = asyncio.run(_go("u1", run))
    assert job.status == "failed"
    assert ("refund", job.id) in calls and not any(c[0] == "settle" for c in calls)


def test_success_settles(calls, monkeypatch):
    monkeypatch.setattr(charge_hook, "reserve", lambda *a, **k: 1)

    async def run(path: Path):
        path.write_bytes(b"0" * 100)
        return SimpleNamespace(file_path=path)

    job = asyncio.run(_go("u1", run))
    assert job.status == "done", job.error
    assert ("settle", job.id) in calls


def test_no_user_skips_billing(calls, monkeypatch):
    monkeypatch.setattr(charge_hook, "reserve", lambda *a, **k: pytest.fail("billing touched"))

    async def run(path: Path):
        path.write_bytes(b"0" * 100)
        return SimpleNamespace(file_path=path)

    job = asyncio.run(_go(None, run))
    assert job.status == "done"
    assert not calls