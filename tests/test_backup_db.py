"""Копии баз SQLite (`schema_migrations`) и CLI `scripts/backup_db.py`.

Перенос тестов движка (int/chat-v1, шаг A15) без части про собственный прогон
миграций: схему основной базы здесь ведёт alembic. Остаётся то, чем линия
пользуется: копия `tracker.db` перед её миграцией (`tracker_schema`),
проверка и восстановление копии, плановые копии, CLI.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from app.services import schema_migrations as sm
from app.services import tracker_schema

ROOT = Path(__file__).resolve().parent.parent


def _open(path):
    return closing(sqlite3.connect(path))


def _make_db(path: Path, rows: int = 5) -> Path:
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
    db.execute("CREATE TABLE spend_ledger (id INTEGER PRIMARY KEY, usd REAL, note TEXT)")
    db.executemany("INSERT INTO legacy(note) VALUES (?)", [(f"n{i}",) for i in range(rows)])
    db.executemany("INSERT INTO spend_ledger(usd, note) VALUES (?, ?)", [(0.1 * i, f"s{i}") for i in range(rows)])
    db.commit()
    db.close()
    return path


def _count(path: Path, table: str) -> int:
    with _open(str(path)) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_backup_is_verified_has_manifest_and_separate_journal(tmp_path):
    db = _make_db(tmp_path / "state.db")
    res = sm.backup_sqlite(db, tag="Pre Migration!")
    assert res.path.parent == tmp_path / "backups" and res.path.name.endswith(".bak.db")
    assert ".pre-migration." in res.path.name
    assert res.manifest_path.is_file() and res.tables == {"legacy": 5, "spend_ledger": 5}
    assert _count(res.path, "legacy") == 5
    assert res.journal_path and ".journal." in res.journal_path.name
    with _open(str(res.journal_path)) as j:
        assert [r[0] for r in j.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["spend_ledger"]
        assert j.execute("SELECT count(*) FROM spend_ledger").fetchone()[0] == 5
    assert sm.verify_backup(res.path)["ok"] is True
    assert not list((tmp_path / "backups").glob("*.part"))


def test_verify_detects_tampered_and_garbage_copies(tmp_path):
    res = sm.backup_sqlite(_make_db(tmp_path / "state.db"))
    data = bytearray(res.path.read_bytes())
    data[-50] ^= 0xFF
    res.path.write_bytes(bytes(data))
    bad = sm.verify_backup(res.path)
    assert bad["ok"] is False and any("sha256" in p for p in bad["problems"])
    junk = tmp_path / "backups" / "state.manual.x.bak.db"
    junk.write_bytes(b"not a database at all" * 50)
    assert sm.verify_backup(junk)["ok"] is False
    assert sm.verify_backup(tmp_path / "nope.bak.db")["ok"] is False


def test_backup_of_missing_or_unreadable_source_raises(tmp_path):
    with pytest.raises(sm.BackupError):
        sm.backup_sqlite(tmp_path / "absent.db")
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"garbage" * 300)
    with pytest.raises(sm.BackupError):
        sm.backup_sqlite(broken)
    assert not list((tmp_path / "backups").glob("*.bak.db")) and not list((tmp_path / "backups").glob("*.part"))


def test_restore_into_clean_folder_passes_integrity_and_keeps_rows(tmp_path):
    res = sm.backup_sqlite(_make_db(tmp_path / "state.db", rows=7))
    clean = tmp_path / "clean"
    out = sm.restore_backup(res.path, clean / "state.db")
    assert out["moved_aside"] == []
    with _open(str(clean / "state.db")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM legacy").fetchone()[0] == 7


def test_restore_over_existing_moves_current_file_aside_and_never_deletes(tmp_path):
    db = _make_db(tmp_path / "state.db", rows=3)
    res = sm.backup_sqlite(db)
    with _open(str(db)) as c:
        c.execute("INSERT INTO legacy(note) VALUES ('later')")
        c.commit()
    out = sm.restore_backup(res.path, db)
    assert _count(db, "legacy") == 3
    aside = Path(out["moved_aside"][0])
    assert aside.exists() and _count(aside, "legacy") == 4


def test_restore_refuses_bad_backup_and_leaves_target_untouched(tmp_path):
    db = _make_db(tmp_path / "state.db", rows=3)
    res = sm.backup_sqlite(db)
    res.path.write_bytes(res.path.read_bytes()[:-100] + b"x" * 100)
    before = db.read_bytes()
    with pytest.raises(sm.RestoreError):
        sm.restore_backup(res.path, db)
    assert db.read_bytes() == before and not list(tmp_path.glob("*.before-restore-*"))


def test_prune_keeps_only_newest_of_each_tag(tmp_path):
    db = _make_db(tmp_path / "state.db")
    made = []
    for _ in range(4):
        made.append(sm.backup_sqlite(db, tag="scheduled", keep=2).path)
        time.sleep(0.01)
    left = sm.backup_files(tmp_path / "backups", "state", "scheduled")
    assert left == made[-2:]
    assert len(list((tmp_path / "backups").glob("state.journal.scheduled.*.bak.db"))) == 2
    sm.backup_sqlite(db, tag="manual")
    assert len(sm.backup_files(tmp_path / "backups", "state", "manual")) == 1


def test_scheduled_backup_respects_interval_and_checks_restore(tmp_path):
    db = _make_db(tmp_path / "state.db")
    first = sm.run_scheduled_backups([db], interval_h=6)
    assert first[0]["status"] == "backed_up" and first[0]["verified"] is True
    assert sm.run_scheduled_backups([db], interval_h=6)[0]["status"] == "skipped"
    old = Path(first[0]["path"])
    stale = time.time() - 7 * 3600
    os.utime(old, (stale, stale))
    third = sm.run_scheduled_backups([db], interval_h=6)
    assert third[0]["status"] == "backed_up" and third[0]["path"] != str(old)
    assert sm.run_scheduled_backups([db], interval_h=6, force=True)[0]["status"] == "backed_up"
    assert sm.run_scheduled_backups([tmp_path / "gone.db"])[0]["status"] == "missing"


def test_scheduled_backup_reports_failure_instead_of_raising(tmp_path):
    broken = tmp_path / "state.db"
    broken.write_bytes(b"garbage" * 300)
    out = sm.run_scheduled_backups([broken])
    assert out[0]["status"] == "failed" and "error" in out[0]


async def test_scheduled_loop_stops_on_event_and_never_raises(tmp_path):
    db = _make_db(tmp_path / "state.db")
    stop = asyncio.Event()
    task = asyncio.create_task(sm.scheduled_backup_loop([db, tmp_path / "gone.db"], check_every_s=0.05, stop=stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert len(sm.backup_files(tmp_path / "backups", "state", "scheduled")) == 1


def test_tracker_existing_database_is_copied_before_its_migration(tmp_path):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
               "provider TEXT NOT NULL, model TEXT NOT NULL)")
    db.execute("INSERT INTO api_calls(timestamp, provider, model) VALUES ('t', 'kie', 'm')")
    db.commit()
    applied = tracker_schema.migrate_tracker_db(db)
    db.close()
    assert applied == ["0001_api_calls", "0002_legacy_columns"]
    copies = sm.backup_files(tmp_path / "backups", "tracker", "pre-migration")
    assert len(copies) == 1 and _count(copies[0], "api_calls") == 1
    with _open(str(copies[0])) as c:
        assert "user_name" not in {r[1] for r in c.execute("PRAGMA table_info(api_calls)")}


def test_tracker_fresh_and_already_migrated_databases_are_not_copied(tmp_path):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    tracker_schema.migrate_tracker_db(db)
    tracker_schema.migrate_tracker_db(db)
    db.close()
    assert not (tmp_path / "backups").exists()


def test_tracker_backup_failure_stops_the_migration(tmp_path, monkeypatch):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY, timestamp TEXT, provider TEXT, model TEXT)")
    db.commit()

    def boom(*a, **k):
        raise sm.BackupError("диск полон")
    monkeypatch.setattr(sm, "backup_sqlite", boom)
    with pytest.raises(sm.BackupError):
        tracker_schema.migrate_tracker_db(db)
    assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (tracker_schema.MIGRATIONS_TABLE,)).fetchone() is None
    db.close()


def _cli():
    spec = importlib.util.spec_from_file_location("w17_backup_db", ROOT / "scripts" / "backup_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_backup_verify_restore_roundtrip(tmp_path, capsys):
    cli = _cli()
    db = _make_db(tmp_path / "state.db", rows=6)
    assert cli.main(["backup", "--db", str(db), "--quiet"]) == 0
    assert cli.main(["verify", "--db", str(db)]) == 0
    latest = sm.latest_backup(tmp_path / "backups", "state")
    with _open(str(db)) as c:
        c.execute("DELETE FROM legacy")
        c.commit()
    assert cli.main(["restore", str(latest), "--target", str(db)]) == 0
    assert _count(db, "legacy") == 6
    capsys.readouterr()
    assert cli.main(["list", "--db", str(db)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [r["backup"] for r in listed] == [str(latest)]


def test_cli_reports_failures_with_nonzero_exit(tmp_path):
    cli = _cli()
    assert cli.main(["verify", "--db", str(tmp_path / "state.db")]) == 1
    assert cli.main(["restore", str(tmp_path / "nope.bak.db"), "--target", str(tmp_path / "state.db")]) == 1
    junk = tmp_path / "state.db"
    junk.write_bytes(b"garbage" * 300)
    assert cli.main(["backup", "--db", str(junk), "--quiet"]) == 1


def test_restore_command_names_existing_cli_and_target(tmp_path):
    cmd = sm.restore_command(tmp_path / "state.pre-migration.x.bak.db", tmp_path / "state.db")
    assert "backup_db.py restore" in cmd and str(tmp_path / "state.db") in cmd
    assert (ROOT / "scripts" / "backup_db.py").is_file()


def test_backup_while_writer_holds_open_transaction(tmp_path):
    db = _make_db(tmp_path / "state.db", rows=2)
    with closing(sqlite3.connect(str(db), timeout=5)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO legacy(note) VALUES ('uncommitted')")
        res = sm.backup_sqlite(db)
        writer.rollback()
    assert _count(res.path, "legacy") == 2 and sm.verify_backup(res.path)["ok"]


def test_cli_default_targets_use_env_and_never_read_dotenv(tmp_path):
    cli = _cli()
    targets = cli.default_targets(tmp_path, {"SQLITE_PATH": "custom/s.db"})
    assert targets == [tmp_path / "custom" / "s.db", tmp_path / "api-tracker" / "tracker.db"]
    assert cli.default_targets(tmp_path, {})[0] == tmp_path / "data" / "state.db"
    src = (ROOT / "scripts" / "backup_db.py").read_text(encoding="utf-8")
    assert "dotenv" not in src and "load_dotenv" not in src and "read_text" not in src
