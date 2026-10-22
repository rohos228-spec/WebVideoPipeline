"""Журнал публикаций (`publications_store`), ревизия 0019 и дневной лимит.

Перенос из движка (int/chat-v1): там таблицы создавал сырой DDL SQLite, здесь
их создаёт alembic 0019, а `ensure_schema` страхует базы без ревизии. Тесты
проверяют обе дороги и сам журнал: намерение до клика, аренду, идемпотентность
по sha256, одноразовые разрешения, ручные действия только дописываются, и путь
`publish_accounts.recent_click_times`, который лениво импортирует модуль.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db_migrations import upgrade_to_head_sync
from app.services import publications_store as ps
from app.services import publish_accounts as pa

PLATFORMS = ["tiktok", "vk_clips", "youtube_shorts"]
_TABLES = {"publications", "publish_approvals", "publication_manual_actions"}


@pytest.fixture(autouse=True)
def _fresh_schema_cache():
    ps._schema_ready_for.clear()
    yield
    ps._schema_ready_for.clear()


@pytest.fixture
async def maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pub.db'}", poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(maker):
    async with maker() as s:
        yield s


async def _claim(session, owner, platform="tiktok", sha="abc", project_id=1, **kw):
    return await ps.claim(session, project_id=project_id, platform=platform, sha256=sha,
                          artifact_uuid="u", owner=owner, **kw)


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _triggers(db: Path) -> set[str]:
    with sqlite3.connect(db) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


# --- журнал ---------------------------------------------------------------


async def test_two_parallel_workers_one_publication(maker):
    async def worker():
        async with maker() as s:
            return await _claim(s, ps.new_owner())

    res = await asyncio.gather(*[worker() for _ in range(4)])
    assert [r.outcome for r in res].count(ps.ACQUIRED) == 1
    assert all(r.outcome in (ps.ACQUIRED, ps.BUSY) for r in res)
    async with maker() as s:
        rows = await ps.list_for(s, 1)
    assert len(rows) == 1 and rows[0]["status"] == ps.S_PLANNED


async def test_crash_between_click_and_record_never_retries(db):
    c = await _claim(db, "w1")
    assert c.outcome == ps.ACQUIRED
    assert await ps.mark_clicked(db, c.pub_id, "w1") is True
    later = await _claim(db, "w2", now=10**10)
    assert later.outcome == ps.NEEDS_HUMAN and later.status == ps.S_CLICKED
    assert await ps.mark_clicked(db, c.pub_id, "w2") is False


async def test_failure_before_click_is_retryable_but_bounded(db):
    for attempt in range(1, ps.MAX_PRE_CLICK_ATTEMPTS + 1):
        c = await _claim(db, f"w{attempt}")
        assert c.outcome == ps.ACQUIRED and c.attempts == attempt
        await ps.record_result(db, c.pub_id, f"w{attempt}", status=ps.S_FAILED, error_code="caption_field_missing")
    assert (await _claim(db, "wx")).outcome == ps.NEEDS_HUMAN


async def test_human_hold_code_is_not_retried(db):
    c = await _claim(db, "w1")
    await ps.record_result(db, c.pub_id, "w1", status=ps.S_FAILED, error_code="captcha")
    assert (await _claim(db, "w2")).outcome == ps.NEEDS_HUMAN


async def test_stale_planned_lease_is_taken_over_but_live_one_is_busy(db):
    c = await _claim(db, "w1", now=1000.0, lease_s=10)
    assert (await _claim(db, "w2", now=1005.0)).outcome == ps.BUSY
    c2 = await _claim(db, "w2", now=1011.0)
    assert c2.outcome == ps.ACQUIRED and c2.pub_id == c.pub_id and c2.attempts == 2


async def test_confirmed_without_url_or_id_is_not_confirmed(db):
    c = await _claim(db, "w1")
    await ps.mark_clicked(db, c.pub_id, "w1")
    assert await ps.record_result(db, c.pub_id, "w1", status=ps.S_CONFIRMED) == ps.S_UNCONFIRMED
    assert (await _claim(db, "w2")).outcome == ps.NEEDS_HUMAN


async def test_confirmed_is_done_and_idempotent_per_sha(db):
    c = await _claim(db, "w1")
    await ps.mark_clicked(db, c.pub_id, "w1")
    await ps.record_result(db, c.pub_id, "w1", status=ps.S_CONFIRMED, post_url="https://x/1")
    assert (await _claim(db, "w2")).outcome == ps.DONE
    assert (await _claim(db, "w2", sha="other-file")).outcome == ps.ACQUIRED
    assert ps.idempotency_key(1, "tiktok", "abc") == "1:tiktok:abc"
    with pytest.raises(ValueError):
        await ps.record_result(db, c.pub_id, "w1", status="published")


async def test_record_unclaimed_and_skipped_never_override_confirmed(db):
    st = await ps.record_unclaimed(db, project_id=2, platform="tiktok", sha256="s", artifact_uuid=None,
                                   status=ps.S_CONFIRMED, clicked=True, post_url="https://x/2")
    assert st == ps.S_CONFIRMED
    again = await ps.record_unclaimed(db, project_id=2, platform="tiktok", sha256="s", artifact_uuid=None,
                                      status=ps.S_FAILED, clicked=True, error_code="boom")
    assert again == ps.S_CONFIRMED
    await ps.mark_skipped(db, project_id=2, platform="vk_clips", sha256="s", artifact_uuid=None,
                          reason="publish_disabled")
    await ps.mark_skipped(db, project_id=2, platform="vk_clips", sha256="s", artifact_uuid=None,
                          reason="publish_disabled")
    rows = {r["platform"]: r for r in await ps.list_for(db, 2, "s")}
    assert rows["vk_clips"]["status"] == ps.S_SKIPPED and rows["vk_clips"]["attempts"] == 0
    assert ps.display_state(rows["vk_clips"]) == ps.D_SKIPPED
    assert ps.display_state(rows["tiktok"]) == ps.D_CONFIRMED
    assert (await _claim(db, "w1", platform="vk_clips", sha="s", project_id=2)).outcome == ps.ACQUIRED


def test_summarize_and_project_state():
    rows = [{"platform": p, "status": ps.S_CONFIRMED, "post_url": "u", "post_id": None, "clicked_at": 1,
             "attempts": 1, "lease_until": None} for p in PLATFORMS]
    assert ps.summarize(rows, PLATFORMS)["all_confirmed"] is True
    assert ps.derive_project_state(rows, PLATFORMS, set())["state"] == ps.PS_PUBLISHED
    rows[0]["post_url"] = None
    s = ps.summarize(rows, PLATFORMS)
    assert s["all_confirmed"] is False and PLATFORMS[0] in s["needs_human"]
    assert ps.derive_project_state(rows, PLATFORMS, set())["state"] == ps.PS_NEEDS_HUMAN
    fresh = ps.derive_project_state([], PLATFORMS, set())
    assert fresh["state"] == ps.PS_AWAITING_APPROVAL and fresh["awaiting_approval"] == PLATFORMS
    off = ps.derive_project_state([], PLATFORMS, set(PLATFORMS), publish_enabled=False)
    assert off["approved_publish_off"] == PLATFORMS and off["awaiting_approval"] == []


# --- разрешения и ручные действия ---------------------------------------------


async def test_approval_is_one_time_and_bound_to_caption(db):
    await ps.grant_approval(db, project_id=3, platform="tiktok", sha256="f", caption_sha256="c1",
                            approved_by="operator:test")
    got = await ps.list_approvals(db, 3, "f", caption_sha256="c2")
    assert got["tiktok"]["valid"] is False
    assert await ps.consume_approval(db, project_id=3, platform="tiktok", sha256="f", caption_sha256="c2") is False
    assert await ps.consume_approval(db, project_id=3, platform="tiktok", sha256="f", caption_sha256="c1") is True
    assert await ps.consume_approval(db, project_id=3, platform="tiktok", sha256="f", caption_sha256="c1") is False
    # Повторная выдача (upsert по ключу) снова делает разрешение действующим.
    await ps.grant_approval(db, project_id=3, platform="tiktok", sha256="f", caption_sha256="c3",
                            approved_by="operator:test")
    assert (await ps.list_approvals(db, 3, "f", caption_sha256="c3"))["tiktok"]["valid"] is True
    out = await ps.revoke_approval(db, project_id=3, platform="tiktok", sha256="f")
    assert out["platform"] == "tiktok"
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.revoke_approval(db, project_id=3, platform="tiktok", sha256="f")
    assert info.value.code == "no_active_approval"


async def test_approval_refused_for_confirmed_or_clicked(db):
    c = await _claim(db, "w1", sha="g", project_id=4)
    await ps.mark_clicked(db, c.pub_id, "w1")
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.grant_approval(db, project_id=4, platform="tiktok", sha256="g", caption_sha256="c",
                                approved_by="op")
    assert info.value.code == "needs_human_first"
    await ps.record_result(db, c.pub_id, "w1", status=ps.S_CONFIRMED, post_url="https://x/4")
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.grant_approval(db, project_id=4, platform="tiktok", sha256="g", caption_sha256="c",
                                approved_by="op")
    assert info.value.code == "already_confirmed"


async def test_manual_confirm_and_mark_failed_are_logged(db):
    c = await _claim(db, "w1", sha="h", project_id=5)
    await ps.mark_clicked(db, c.pub_id, "w1")
    await ps.record_result(db, c.pub_id, "w1", status=ps.S_UNCONFIRMED)
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.confirm_manual(db, project_id=5, platform="tiktok", sha256="h", actor="op", reason="ok")
    assert info.value.code == "evidence_required"
    with pytest.raises(ps.PublicationRefused):
        await ps.confirm_manual(db, project_id=5, platform="tiktok", sha256="h", actor="op",
                                post_url="ftp://bad")
    done = await ps.confirm_manual(db, project_id=5, platform="tiktok", sha256="h", actor="op",
                                   post_url="https://x/5")
    assert done["changed"] and done["prev_status"] == ps.S_UNCONFIRMED
    again = await ps.confirm_manual(db, project_id=5, platform="tiktok", sha256="h", actor="op",
                                    post_url="https://x/5")
    assert again["changed"] is False

    c2 = await _claim(db, "w1", sha="h", project_id=5, platform="vk_clips")
    await ps.record_result(db, c2.pub_id, "w1", status=ps.S_FAILED, error_code="captcha")
    out = await ps.mark_failed_manual(db, project_id=5, platform="vk_clips", sha256="h", actor="op",
                                      reason="пост не вышел")
    assert out["status"] == ps.S_FAILED and out["allow_republish"] is False
    row = {r["platform"]: r for r in await ps.list_for(db, 5)}["vk_clips"]
    assert row["attempts"] == ps.MAX_PRE_CLICK_ATTEMPTS and row["error_code"] == ps.MANUAL_FAILED_CODE
    assert "vk_clips" in ps.summarize([row], ["vk_clips"])["manual_failed"]
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.mark_failed_manual(db, project_id=5, platform="tiktok", sha256="h", actor="op",
                                    reason="пост не вышел")
    assert info.value.code == "already_confirmed"

    actions = await ps.list_manual_actions(db, 5)
    assert [a["action"] for a in actions] == ["confirm", "mark_failed"]
    assert actions[0]["prev_status"] == ps.S_UNCONFIRMED


async def test_mark_failed_with_republish_resets_attempts(db):
    c = await _claim(db, "w1", sha="r", project_id=6)
    await ps.mark_clicked(db, c.pub_id, "w1")
    await ps.mark_failed_manual(db, project_id=6, platform="tiktok", sha256="r", actor="op",
                                reason="удалили пост", allow_republish=True)
    again = await _claim(db, "w2", sha="r", project_id=6)
    assert again.outcome == ps.ACQUIRED and again.attempts == 1


async def test_manual_actions_are_append_only(db, tmp_path):
    c = await _claim(db, "w1", sha="m", project_id=7)
    await ps.mark_clicked(db, c.pub_id, "w1")
    await ps.confirm_manual(db, project_id=7, platform="tiktok", sha256="m", actor="op", post_url="https://x/7")
    with sqlite3.connect(tmp_path / "pub.db") as con:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE publication_manual_actions SET actor='x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM publication_manual_actions")


async def test_manual_action_without_journal_row_is_refused(db):
    with pytest.raises(ps.PublicationRefused) as info:
        await ps.confirm_manual(db, project_id=8, platform="tiktok", sha256="none", actor="op",
                                post_url="https://x/8")
    assert info.value.code == "no_publication_record"


async def test_sha256_of_file(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"SYNTHETIC")
    assert await ps.sha256_of_file_async(f) == ps.sha256_of_file(f) and len(ps.sha256_of_file(f)) == 64


# --- ленивый импорт: дневной лимит публикаций ------------------------------------------


async def test_recent_click_times_counts_journal(db):
    now = time.time()
    for i, plat in enumerate(["tiktok", "tiktok", "vk_clips"]):
        c = await ps.claim(db, project_id=i + 1, platform=plat, sha256="h", artifact_uuid=None, owner="o")
        await ps.mark_clicked(db, c.pub_id, "o", now=now - 60)
    old = await ps.claim(db, project_id=8, platform="tiktok", sha256="h", artifact_uuid=None, owner="o")
    await ps.mark_clicked(db, old.pub_id, "o", now=now - 2 * pa.DAY_S)
    await ps.claim(db, project_id=9, platform="tiktok", sha256="h", artifact_uuid=None, owner="o")
    times = await pa.recent_click_times(db, "tiktok", now=now)
    assert len(times) == 2
    assert not pa.daily_limit_decision(times, 2, now).allowed
    assert pa.daily_limit_decision(times, 3, now).allowed


async def test_recent_click_times_on_empty_database_creates_schema(db, tmp_path):
    assert await pa.recent_click_times(db, "tiktok") == []
    assert _tables(tmp_path / "pub.db") >= _TABLES


# --- alembic 0019 -----------------------------------------------------------------


def test_fresh_database_gets_publication_tables_from_alembic(tmp_path):
    db = tmp_path / "fresh.db"
    upgrade_to_head_sync(db)
    assert _tables(db) >= _TABLES
    assert {"publication_manual_no_update", "publication_manual_no_delete"} <= _triggers(db)
    with sqlite3.connect(db) as con:
        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ux_publications_idem", "ix_publications_project", "ux_publish_approvals_key",
            "ix_publication_manual_project"} <= idx


def test_database_at_0017_is_caught_up_and_upgrade_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    upgrade_to_head_sync(db)
    with sqlite3.connect(db) as con:
        for t in ("publication_manual_no_update", "publication_manual_no_delete"):
            con.execute(f"DROP TRIGGER {t}")
        for t in sorted(_TABLES):
            con.execute(f"DROP TABLE {t}")
        con.execute("UPDATE alembic_version SET version_num = '0017'")
    assert not _TABLES & _tables(db)
    upgrade_to_head_sync(db)
    assert _tables(db) >= _TABLES
    upgrade_to_head_sync(db)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0019"


async def test_store_works_on_alembic_database_and_matches_its_shape(tmp_path):
    db = tmp_path / "head.db"
    upgrade_to_head_sync(db)
    with sqlite3.connect(db) as con:
        alembic_cols = {t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")] for t in _TABLES}
    assert alembic_cols == {t.name: [c.name for c in t.columns] for t in ps.TABLES}
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            c = await _claim(s, "w1")
            assert c.outcome == ps.ACQUIRED
            assert await ps.mark_clicked(s, c.pub_id, "w1")
    finally:
        await engine.dispose()


def test_publication_tables_stay_out_of_tenant_models():
    from app.models import Base
    from app.services.tenant_tables import all_isolated

    assert not _TABLES & set(Base.metadata.tables)
    assert not _TABLES & set(all_isolated())
