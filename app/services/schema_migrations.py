"""Копии баз SQLite: снять, проверить, вернуть на место, держать по расписанию.

Имя модуля сохранено за движком (int/chat-v1), где здесь же жил и собственный
прогон миграций. В этой линии схему основной базы ведёт alembic
(`app/db_migrations.py`), поэтому сюда перенесена только часть про копии:
ей пользуются миграции учётной базы `tracker.db` (`tracker_schema`, копия
перед первой невыполненной миграцией) и `scripts/backup_db.py`.

Копия снимается через sqlite backup API (работает при открытой записи),
проверяется `integrity_check`, рядом пишется манифест с sha256 и числом строк,
отдельно выносится копия журнальных таблиц. Восстановление ничего не удаляет:
текущий файл отодвигается в `<имя>.before-restore-<время>`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

BACKUP_DIR_NAME = "backups"
BACKUP_SUFFIX = ".bak.db"
KEEP_PRE_MIGRATION = 10
KEEP_SCHEDULED = 14
JOURNAL_TABLES: tuple[str, ...] = (
    "spend_ledger",
    "provider_task_ledger",
    "observation_executions",
    "observation_outbox",
    "observation_events",
    "observation_degradations",
    "observation_commit_uncertainty",
    "action_layer_plans",
    "action_layer_steps",
    "action_layer_approvals",
    "action_layer_spend",
    "action_layer_history",
    "action_layer_plan_context",
    "change_proposals",
    "config_versions",
    "config_pointers",
    "notification_log",
    "journal_archive_log",
    "operator_audit",
    "publications",
    "outsee_idempotency",
    "action_layer_calls",
    "action_layer_snapshots",
    "publish_approvals",
    "publication_manual_actions",
    "credit_entries",
    "llm_calls",
    "media_calls",
)


class BackupError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    source: Path
    manifest_path: Path
    sha256: str
    size: int
    tables: dict
    tag: str
    journal_path: Path | None = None


def _str_or_none(p) -> str | None:
    return str(p) if p is not None else None


def _safe_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", tag.lower()).strip("-") or "manual"


def _stamp() -> str:
    now = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _user_tables(db: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _table_counts(db: sqlite3.Connection) -> dict[str, int]:
    return {t: db.execute(f"SELECT count(*) FROM {_quote(t)}").fetchone()[0] for t in _user_tables(db)}


def _integrity(db: sqlite3.Connection) -> str:
    rows = [r[0] for r in db.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(map(str, rows))[:300]


def default_backup_dir(db_path: Path | str) -> Path:
    return Path(db_path).resolve().parent / BACKUP_DIR_NAME


def backup_files(dest_dir: Path | str, stem: str, tag: str | None = None) -> list[Path]:
    d = Path(dest_dir)
    if not d.is_dir():
        return []
    prefix = f"{stem}." + (f"{_safe_tag(tag)}." if tag else "")
    return sorted(
        p
        for p in d.iterdir()
        if p.name.startswith(prefix)
        and p.name.endswith(BACKUP_SUFFIX)
        and not p.name.startswith(f"{stem}.journal.")
    )


def latest_backup(dest_dir: Path | str, stem: str, tag: str | None = None) -> Path | None:
    files = backup_files(dest_dir, stem, tag)
    return files[-1] if files else None


def prune_backups(dest_dir: Path | str, stem: str, tag: str, keep: int) -> list[Path]:
    removed: list[Path] = []
    keep = max(1, int(keep))
    d = Path(dest_dir)
    for kind in (f"{stem}.{_safe_tag(tag)}.", f"{stem}.journal.{_safe_tag(tag)}."):
        files = sorted(d.glob(kind + "*" + BACKUP_SUFFIX)) if d.is_dir() else []
        for old in files[:-keep]:
            for victim in (old, old.with_name(old.name + ".json")):
                with contextlib.suppress(OSError):
                    victim.unlink()
            removed.append(old)
    return removed


def _check_space(dest_dir: Path, src: Path) -> None:
    need = src.stat().st_size
    wal = src.with_name(src.name + "-wal")
    if wal.exists():
        need += wal.stat().st_size
    free = shutil.disk_usage(dest_dir).free
    if free < int(need * 1.25) + (16 << 20):
        raise BackupError(
            f"мало места для копии базы {src.name}: нужно около {need >> 20} МБ, свободно {free >> 20} МБ"
        )


def backup_sqlite(
    src: Path | str,
    dest_dir: Path | str | None = None,
    *,
    tag: str = "manual",
    keep: int | None = None,
    journal: bool = True,
) -> BackupResult:
    src = Path(src).resolve()
    if not src.is_file():
        raise BackupError(f"нет файла базы: {src}")
    dest_dir = Path(dest_dir) if dest_dir else default_backup_dir(src)
    tag = _safe_tag(tag)
    stem = src.stem
    suffix = f"{tag}.{_stamp()}{BACKUP_SUFFIX}"
    final = dest_dir / f"{stem}.{suffix}"
    part = final.with_name(final.name + ".part")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        _check_space(dest_dir, src)
        with (
            contextlib.closing(sqlite3.connect(str(src), timeout=60)) as source,
            contextlib.closing(sqlite3.connect(str(part))) as target,
        ):
            source.backup(target)
        with contextlib.closing(sqlite3.connect(str(part))) as copy:
            verdict = _integrity(copy)
            if verdict != "ok":
                raise BackupError(f"копия {src.name} не прошла integrity_check: {verdict}")
            tables = _table_counts(copy)
        os.replace(part, final)
        digest = _sha256_file(final)
        manifest = final.with_name(final.name + ".json")
        manifest.write_text(
            json.dumps(
                {
                    "source": str(src),
                    "created_at": datetime.now(UTC).isoformat(),
                    "tag": tag,
                    "sha256": digest,
                    "size": final.stat().st_size,
                    "tables": tables,
                    "integrity": "ok",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except BackupError:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    except Exception as exc:
        with contextlib.suppress(OSError):
            part.unlink()
        raise BackupError(f"не удалось скопировать {src.name}: {type(exc).__name__}: {exc}"[:400]) from exc
    result = BackupResult(final, src, manifest, digest, final.stat().st_size, tables, tag)
    if journal:
        try:
            result.journal_path = extract_journal(final, dest_dir / f"{stem}.journal.{suffix}")
        except Exception as exc:
            logger.error("journal copy of {} failed: {}", src.name, exc)
    if keep:
        prune_backups(dest_dir, stem, tag, keep)
    logger.info("db backup {} -> {} ({} tables)", src.name, final, len(tables))
    return result


def extract_journal(backup: Path | str, dest: Path | str) -> Path | None:
    backup, dest = Path(backup), Path(dest)
    part = dest.with_name(dest.name + ".part")
    with contextlib.suppress(OSError):
        part.unlink()
    copied = 0
    with (
        contextlib.closing(sqlite3.connect(str(backup))) as src,
        contextlib.closing(sqlite3.connect(str(part))) as out,
    ):
        have = set(_user_tables(src))
        for name in JOURNAL_TABLES:
            if name not in have:
                continue
            ddl = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            out.execute(ddl)
            rows = src.execute(f"SELECT * FROM {_quote(name)}").fetchall()
            if rows:
                marks = ",".join("?" * len(rows[0]))
                out.executemany(f"INSERT INTO {_quote(name)} VALUES ({marks})", rows)
            copied += 1
        out.commit()
        verdict = _integrity(out)
    if copied == 0:
        with contextlib.suppress(OSError):
            part.unlink()
        return None
    if verdict != "ok":
        part.unlink()
        raise BackupError(f"копия журнала не прошла integrity_check: {verdict}")
    os.replace(part, dest)
    return dest


def verify_backup(path: Path | str) -> dict:
    path = Path(path)
    problems: list[str] = []
    out = {"ok": False, "problems": problems, "integrity": None, "tables": {}}
    if not path.is_file():
        problems.append("файла копии нет")
        return out
    manifest: dict = {}
    mpath = path.with_name(path.name + ".json")
    if mpath.is_file():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append("манифест копии не читается")
    else:
        problems.append("нет манифеста копии")
    if manifest.get("sha256") and manifest["sha256"] != _sha256_file(path):
        problems.append("sha256 не совпадает с манифестом: копия изменена или повреждена")
    tmp = Path(tempfile.mkdtemp(prefix="vp_restore_check_"))
    try:
        probe = tmp / "restored.db"
        shutil.copy2(path, probe)
        with contextlib.closing(sqlite3.connect(str(probe))) as db:
            out["integrity"] = _integrity(db)
            out["tables"] = _table_counts(db)
        if out["integrity"] != "ok":
            problems.append(f"integrity_check: {out['integrity']}")
        for name, count in (manifest.get("tables") or {}).items():
            if out["tables"].get(name) != count:
                problems.append(
                    f"таблица {name}: в манифесте {count} строк, в копии {out['tables'].get(name)}"
                )
    except sqlite3.Error as exc:
        problems.append(f"копия не открывается как база: {exc}"[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["ok"] = not problems
    return out


def restore_command(backup: Path | str, target: Path | str) -> str:
    return f'python scripts\\backup_db.py restore "{backup}" --target "{target}"'


def restore_backup(backup: Path | str, target: Path | str) -> dict:
    backup, target = Path(backup), Path(target)
    check = verify_backup(backup)
    if not check["ok"]:
        raise RestoreError(
            "копия не прошла проверку, восстановление отменено: " + "; ".join(check["problems"])
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restore-part")
    stamp = _stamp()
    moved: list[tuple[Path, Path]] = []
    try:
        shutil.copy2(backup, staged)
        with contextlib.closing(sqlite3.connect(str(staged))) as db:
            verdict = _integrity(db)
        if verdict != "ok":
            raise RestoreError(f"подготовленная копия не прошла integrity_check: {verdict}")
        if target.exists():
            try:
                with contextlib.closing(sqlite3.connect(str(target), timeout=1)) as live:
                    live.execute("BEGIN EXCLUSIVE")
                    live.rollback()
            except sqlite3.Error as exc:
                raise RestoreError(f"база занята другим процессом (остановите Studio): {exc}") from exc
        for ext in ("", "-wal", "-shm"):
            cur = target.with_name(target.name + ext)
            if cur.exists():
                aside = target.with_name(f"{target.name}.before-restore-{stamp}{ext}")
                os.replace(cur, aside)
                moved.append((cur, aside))
        os.replace(staged, target)
    except Exception as exc:
        for cur, aside in reversed(moved):
            with contextlib.suppress(OSError):
                os.replace(aside, cur)
        with contextlib.suppress(OSError):
            staged.unlink()
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(
            f"восстановление не удалось, файл возвращён на место: {type(exc).__name__}: {exc}"[:400]
        ) from exc
    return {
        "restored": str(target),
        "from": str(backup),
        "tables": check["tables"],
        "moved_aside": [str(a) for _, a in moved],
    }


def backup_due(dest_dir: Path | str, stem: str, interval_h: float, tag: str = "scheduled") -> bool:
    last = latest_backup(dest_dir, stem, tag)
    if last is None:
        return True
    return (time.time() - last.stat().st_mtime) >= interval_h * 3600


def run_scheduled_backups(
    db_paths,
    *,
    interval_h: float = 6.0,
    keep: int = KEEP_SCHEDULED,
    force: bool = False,
    dest_dir: Path | str | None = None,
) -> list[dict]:
    out: list[dict] = []
    for raw in db_paths:
        db = Path(raw)
        item: dict = {"db": str(db), "status": "skipped"}
        out.append(item)
        try:
            if not db.is_file():
                item["status"] = "missing"
                continue
            target_dir = Path(dest_dir) if dest_dir else default_backup_dir(db)
            if not force and not backup_due(target_dir, db.stem, interval_h):
                continue
            res = backup_sqlite(db, target_dir, tag="scheduled", keep=keep)
            check = verify_backup(res.path)
            item.update(
                status="backed_up" if check["ok"] else "backed_up_unverified",
                path=str(res.path),
                journal=_str_or_none(res.journal_path),
                verified=check["ok"],
                problems=check["problems"],
            )
            if not check["ok"]:
                logger.error(
                    "scheduled backup of {} failed its restore check: {}", db.name, check["problems"]
                )
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
            logger.exception("scheduled backup of {} failed", db)
    return out


async def scheduled_backup_loop(
    db_paths,
    *,
    interval_h: float = 6.0,
    check_every_s: float = 600.0,
    keep: int = KEEP_SCHEDULED,
    stop: asyncio.Event | None = None,
) -> None:
    while stop is None or not stop.is_set():
        paths = db_paths() if callable(db_paths) else db_paths
        await asyncio.to_thread(run_scheduled_backups, paths, interval_h=interval_h, keep=keep)
        try:
            if stop is None:
                await asyncio.sleep(check_every_s)
            else:
                await asyncio.wait_for(stop.wait(), timeout=check_every_s)
        except TimeoutError:
            pass
