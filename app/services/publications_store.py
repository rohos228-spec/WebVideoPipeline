"""Журнал публикаций: идемпотентность, намерение до клика, аренда площадки.

Одна строка `publications` на (проект, площадка, sha256 файла). Строка пишется
ДО клика «Опубликовать» и держит аренду; после клика автоповтора нет, решает
человек. Разрешения на публикацию (`publish_approvals`) одноразовые, ручные
действия оператора (`publication_manual_actions`) только дописываются.

Перенесено из движка (int/chat-v1). Там таблицы создавались сырым DDL SQLite;
здесь одна база через `app.db`, которая бывает и SQLite, и Postgres, поэтому
таблицы описаны через SQLAlchemy Core, создаются ревизией alembic 0018, а
`ensure_schema` — страховка для баз, где ревизия ещё не прогнана (тесты,
голый `create_all`). Вставка «если нет» идёт через `ON CONFLICT DO NOTHING`
диалекта, а не `INSERT OR IGNORE`, которого нет в Postgres.

Таблицы намеренно НЕ входят в `app.models.Base`: колонки арендатора и политики
RLS для них не заведены (решение за заказчиком), а `tenant_tables.check_coverage`
считает только таблицы моделей.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEASE_SECONDS = 900.0
MAX_PRE_CLICK_ATTEMPTS = 3

S_PLANNED = "planned"
S_CLICKED = "clicked"
S_CONFIRMED = "confirmed"
S_UNCONFIRMED = "unconfirmed"
S_FAILED = "failed"
S_REJECTED = "rejected"
S_SKIPPED = "skipped"
ALL_STATUSES = (S_PLANNED, S_CLICKED, S_CONFIRMED, S_UNCONFIRMED, S_FAILED, S_REJECTED, S_SKIPPED)

ACQUIRED = "acquired"
BUSY = "busy"
DONE = "done"
NEEDS_HUMAN = "needs_human"

metadata = sa.MetaData()

publications = sa.Table(
    "publications",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("project_id", sa.Integer, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("artifact_uuid", sa.Text),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("post_url", sa.Text),
    sa.Column("post_id", sa.Text),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("screenshot", sa.Text),
    sa.Column("error_code", sa.Text),
    sa.Column("lease_owner", sa.Text),
    sa.Column("lease_until", sa.Float),
    sa.Column("clicked_at", sa.Float),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
    sa.Index("ux_publications_idem", "project_id", "platform", "sha256", unique=True),
    sa.Index("ix_publications_project", "project_id"),
    sqlite_autoincrement=True,
)

publish_approvals = sa.Table(
    "publish_approvals",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("project_id", sa.Integer, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("caption_sha256", sa.Text, nullable=False),
    sa.Column("approved_by", sa.Text, nullable=False),
    sa.Column("approved_at", sa.Float, nullable=False),
    sa.Column("consumed_at", sa.Float),
    sa.Index("ux_publish_approvals_key", "project_id", "platform", "sha256", unique=True),
    sqlite_autoincrement=True,
)

publication_manual_actions = sa.Table(
    "publication_manual_actions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("publication_id", sa.Integer, nullable=False),
    sa.Column("project_id", sa.Integer, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("prev_status", sa.Text),
    sa.Column("prev_post_url", sa.Text),
    sa.Column("post_url", sa.Text),
    sa.Column("reason", sa.Text),
    sa.Column("actor", sa.Text, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Index("ix_publication_manual_project", "project_id"),
    sqlite_autoincrement=True,
)

TABLES = (publications, publish_approvals, publication_manual_actions)

# Ручные действия только дописываются. Триггеры — на SQLite, как у остальных
# журналов движка (spend_ledger, outsee_idempotency).
SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_update BEFORE UPDATE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_delete BEFORE DELETE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
)

_COLS = (
    "id, project_id, platform, artifact_uuid, sha256, account_id, status, post_url, post_id, "
    "attempts, screenshot, error_code, lease_owner, lease_until, clicked_at, updated_at"
)

MANUAL_FAILED_CODE = "manual_not_published"
MANUAL_CONFIRMED_ID = "manual-confirmed"
MIN_REASON_CHARS = 5
MAX_REASON_CHARS = 500

D_SKIPPED = "publish_skipped"
D_INTENT = "intent"
D_UNCONFIRMED = "unconfirmed"
D_CONFIRMED = "confirmed"
D_FAILED_BEFORE = "failed_before_click"
D_FAILED_AFTER = "failed_after_click"
DISPLAY_LABELS_RU = {
    D_SKIPPED: "пропущено (автопубликация выключена)",
    D_INTENT: "намерение записано, клика не было",
    D_UNCONFIRMED: "не подтверждено, ждёт проверки человека",
    D_CONFIRMED: "опубликовано",
    D_FAILED_BEFORE: "сбой до клика, ничего не опубликовано",
    D_FAILED_AFTER: "сбой после клика, автоповтора не будет",
}

_schema_ready_for: set[str] = set()


@dataclass
class Claim:
    outcome: str
    pub_id: int | None = None
    owner: str = ""
    status: str = ""
    attempts: int = 0


def new_owner() -> str:
    return uuid.uuid4().hex


def idempotency_key(project_id: int, platform: str, sha256: str) -> str:
    return f"{project_id}:{platform}:{sha256}"


def sha256_of_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def sha256_of_file_async(path: str | Path) -> str:
    return await asyncio.to_thread(sha256_of_file, path)


def _bind_key(session: AsyncSession) -> str:
    bind = session.get_bind()
    return str(getattr(bind, "url", id(bind)))


def create_schema_sync(sync_conn) -> None:
    # IF NOT EXISTS, а не checkfirst: два воркера на свежей базе иначе оба
    # видят «таблицы нет» и второй падает на CREATE TABLE.
    for table in TABLES:
        sync_conn.execute(sa.schema.CreateTable(table, if_not_exists=True))
        for index in sorted(table.indexes, key=lambda i: i.name):
            sync_conn.execute(sa.schema.CreateIndex(index, if_not_exists=True))
    if sync_conn.dialect.name == "sqlite":
        for ddl in SQLITE_TRIGGERS:
            sync_conn.exec_driver_sql(ddl)


async def ensure_schema(session: AsyncSession) -> None:
    key = _bind_key(session)
    if key in _schema_ready_for:
        try:
            for table in TABLES:
                await session.execute(text(f"SELECT 1 FROM {table.name} LIMIT 0"))
            return
        except Exception:
            await session.rollback()
            _schema_ready_for.discard(key)
    await session.run_sync(lambda s: create_schema_sync(s.connection()))
    await session.commit()
    _schema_ready_for.add(key)


def _insert(session: AsyncSession, table: sa.Table):
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


async def _insert_if_absent(session: AsyncSession, values: dict):
    stmt = (
        _insert(session, publications)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["project_id", "platform", "sha256"])
    )
    return await session.execute(stmt)


async def _commit(session: AsyncSession) -> None:
    from app.db import commit_with_retry

    await commit_with_retry(session)


async def _get(session: AsyncSession, project_id: int, platform: str, sha256: str) -> dict | None:
    row = (
        (
            await session.execute(
                text(f"SELECT {_COLS} FROM publications WHERE project_id=:p AND platform=:pl AND sha256=:s"),
                {"p": project_id, "pl": platform, "s": sha256},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


HUMAN_HOLD_CODES = frozenset(
    {
        "not_logged_in",
        "captcha",
        "two_factor",
        "rate_limited",
        "rejected",
        "profile_missing",
        "accounts_config_invalid",
    }
)


def is_auto_retryable(row: dict, now: float) -> bool:
    st = row["status"]
    if row.get("clicked_at"):
        return False
    if st == S_FAILED and row.get("error_code") in HUMAN_HOLD_CODES:
        return False
    if st == S_SKIPPED:
        return True
    if st == S_FAILED:
        return int(row.get("attempts") or 0) < MAX_PRE_CLICK_ATTEMPTS
    if st == S_PLANNED:
        return (row.get("lease_until") or 0) < now
    return False


async def claim(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    artifact_uuid: str | None,
    owner: str,
    account_id: str | None = None,
    lease_s: float = LEASE_SECONDS,
    now: float | None = None,
) -> Claim:
    await ensure_schema(session)
    now = time.time() if now is None else now
    ins = await _insert_if_absent(
        session,
        {
            "project_id": project_id,
            "platform": platform,
            "artifact_uuid": artifact_uuid,
            "sha256": sha256,
            "account_id": account_id,
            "status": S_PLANNED,
            "attempts": 1,
            "lease_owner": owner,
            "lease_until": now + lease_s,
            "created_at": now,
            "updated_at": now,
        },
    )
    if ins.rowcount == 1:
        await _commit(session)
        row = await _get(session, project_id, platform, sha256)
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, 1)

    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    if row is None:
        return Claim(BUSY)
    if row["status"] == S_CONFIRMED:
        return Claim(DONE, row["id"], status=row["status"], attempts=row["attempts"])
    if not is_auto_retryable(row, now):
        if row["status"] == S_PLANNED and not row.get("clicked_at"):
            return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])
        return Claim(NEEDS_HUMAN, row["id"], status=row["status"], attempts=row["attempts"])

    upd = await session.execute(
        text(
            "UPDATE publications SET status='planned', attempts=attempts+1, lease_owner=:o, lease_until=:lu, "
            "error_code=NULL, updated_at=:now "
            "WHERE id=:id AND clicked_at IS NULL AND ("
            "  status='skipped' OR (status='failed' AND attempts<:mx) OR (status='planned' AND lease_until<:now))"
        ),
        {"o": owner, "lu": now + lease_s, "now": now, "id": row["id"], "mx": MAX_PRE_CLICK_ATTEMPTS},
    )
    await _commit(session)
    if upd.rowcount == 1:
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, int(row["attempts"]) + 1)
    return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])


async def mark_clicked(session: AsyncSession, pub_id: int, owner: str, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    upd = await session.execute(
        text(
            "UPDATE publications SET status='clicked', clicked_at=:now, updated_at=:now "
            "WHERE id=:id AND lease_owner=:o AND status='planned'"
        ),
        {"now": now, "id": pub_id, "o": owner},
    )
    await _commit(session)
    return upd.rowcount == 1


async def record_result(
    session: AsyncSession,
    pub_id: int,
    owner: str,
    *,
    status: str,
    post_url: str | None = None,
    post_id: str | None = None,
    error_code: str | None = None,
    screenshot: str | None = None,
    account_id: str | None = None,
    now: float | None = None,
) -> str:
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown publication status {status!r}")
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await session.execute(
        text(
            "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, screenshot=:sc, "
            "account_id=COALESCE(:acc, account_id), "
            "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND lease_owner=:o"
        ),
        {
            "st": status,
            "u": post_url,
            "pid": post_id,
            "e": error_code,
            "sc": screenshot,
            "acc": account_id,
            "now": now,
            "id": pub_id,
            "o": owner,
        },
    )
    await _commit(session)
    row = (
        await session.execute(text("SELECT status FROM publications WHERE id=:id"), {"id": pub_id})
    ).first()
    await _commit(session)
    return row[0] if row else status


async def record_unclaimed(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    artifact_uuid: str | None,
    status: str,
    clicked: bool,
    post_url: str | None = None,
    post_id: str | None = None,
    error_code: str | None = None,
    screenshot: str | None = None,
    account_id: str | None = None,
    now: float | None = None,
) -> str:
    await ensure_schema(session)
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await _insert_if_absent(
        session,
        {
            "project_id": project_id,
            "platform": platform,
            "artifact_uuid": artifact_uuid,
            "sha256": sha256,
            "status": S_PLANNED,
            "attempts": 1,
            "clicked_at": now if clicked else None,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.execute(
        text(
            "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, "
            "screenshot=COALESCE(:sc, screenshot), account_id=COALESCE(:acc, account_id), "
            "clicked_at=CASE WHEN CAST(:cl AS INTEGER)=1 AND clicked_at IS NULL THEN :now ELSE clicked_at END, "
            "lease_owner=NULL, lease_until=NULL, updated_at=:now "
            "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status<>'confirmed'"
        ),
        {
            "st": status,
            "u": post_url,
            "pid": post_id,
            "e": error_code,
            "sc": screenshot,
            "acc": account_id,
            "cl": 1 if clicked else 0,
            "now": now,
            "p": project_id,
            "pl": platform,
            "s": sha256,
        },
    )
    await _commit(session)
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    return row["status"] if row else status


async def mark_skipped(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    artifact_uuid: str | None,
    reason: str,
    now: float | None = None,
) -> None:
    await ensure_schema(session)
    now = time.time() if now is None else now
    await _insert_if_absent(
        session,
        {
            "project_id": project_id,
            "platform": platform,
            "artifact_uuid": artifact_uuid,
            "sha256": sha256,
            "status": S_SKIPPED,
            "attempts": 0,
            "error_code": reason,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.execute(
        text(
            "UPDATE publications SET status='skipped', error_code=:e, updated_at=:now "
            "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status='planned' AND clicked_at IS NULL "
            "AND (lease_until IS NULL OR lease_until<:now)"
        ),
        {"e": reason, "now": now, "p": project_id, "pl": platform, "s": sha256},
    )
    await _commit(session)


async def list_for(session: AsyncSession, project_id: int, sha256: str | None = None) -> list[dict]:
    await ensure_schema(session)
    sql = f"SELECT {_COLS} FROM publications WHERE project_id=:p"
    params: dict = {"p": project_id}
    if sha256 is not None:
        sql += " AND sha256=:s"
        params["s"] = sha256
    rows = (await session.execute(text(sql + " ORDER BY id"), params)).mappings().all()
    await _commit(session)
    return [dict(r) for r in rows]


def summarize(rows: list[dict], platforms: list[str], now: float | None = None) -> dict:
    now = time.time() if now is None else now
    by = {r["platform"]: r for r in rows}
    confirmed = [
        p
        for p in platforms
        if p in by and by[p]["status"] == S_CONFIRMED and (by[p]["post_url"] or by[p]["post_id"])
    ]
    retryable, busy, human, skipped, manual_failed = [], [], [], [], []
    for p in platforms:
        r = by.get(p)
        if r is None:
            retryable.append(p)
        elif p in confirmed:
            continue
        elif r["status"] == S_PLANNED and not r.get("clicked_at") and (r.get("lease_until") or 0) >= now:
            busy.append(p)
        elif r["status"] == S_SKIPPED:
            skipped.append(p)
        elif is_auto_retryable(r, now):
            retryable.append(p)
        elif r["status"] == S_FAILED and r.get("error_code") == MANUAL_FAILED_CODE:
            manual_failed.append(p)
        else:
            human.append(p)
    return {
        "all_confirmed": len(confirmed) == len(platforms),
        "confirmed": confirmed,
        "retryable": retryable,
        "busy": busy,
        "needs_human": human,
        "skipped": skipped,
        "manual_failed": manual_failed,
    }


def display_state(row: dict) -> str:
    st = row["status"]
    clicked = bool(row.get("clicked_at"))
    if st == S_CONFIRMED:
        return D_CONFIRMED if (row.get("post_url") or row.get("post_id")) else D_UNCONFIRMED
    if st == S_SKIPPED:
        return D_SKIPPED
    if st == S_PLANNED:
        return D_UNCONFIRMED if clicked else D_INTENT
    if st in (S_CLICKED, S_UNCONFIRMED):
        return D_UNCONFIRMED
    return D_FAILED_AFTER if clicked else D_FAILED_BEFORE


class PublicationRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _clean_reason(reason: str | None) -> str:
    return " ".join((reason or "").split())[:MAX_REASON_CHARS]


def _clean_url(url: str | None) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if len(u) > 1000 or not u.lower().startswith(("http://", "https://")):
        raise PublicationRefused("bad_post_url", "Ссылка на пост должна начинаться с http:// или https://.")
    return u


async def grant_approval(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    caption_sha256: str,
    approved_by: str,
    now: float | None = None,
) -> None:
    await ensure_schema(session)
    now = time.time() if now is None else now
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    if row is not None and row["status"] == S_CONFIRMED:
        raise PublicationRefused(
            "already_confirmed", "Эта площадка уже подтверждена: повторная публикация не нужна."
        )
    if (
        row is not None
        and not is_auto_retryable(row, now)
        and not (row["status"] == S_PLANNED and not row.get("clicked_at"))
    ):
        raise PublicationRefused(
            "needs_human_first",
            "По этой площадке клик мог состояться или попытки исчерпаны: сначала подтвердите вручную "
            "или пометьте «не опубликовано».",
        )
    stmt = _insert(session, publish_approvals).values(
        project_id=project_id,
        platform=platform,
        sha256=sha256,
        caption_sha256=caption_sha256,
        approved_by=approved_by[:120],
        approved_at=now,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["project_id", "platform", "sha256"],
            set_={
                "caption_sha256": caption_sha256,
                "approved_by": approved_by[:120],
                "approved_at": now,
                "consumed_at": None,
            },
        )
    )
    await _commit(session)


async def list_approvals(
    session: AsyncSession, project_id: int, sha256: str, caption_sha256: str | None = None
) -> dict[str, dict]:
    await ensure_schema(session)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT platform, caption_sha256, approved_by, approved_at, consumed_at FROM publish_approvals "
                    "WHERE project_id=:p AND sha256=:s"
                ),
                {"p": project_id, "s": sha256},
            )
        )
        .mappings()
        .all()
    )
    await _commit(session)
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["valid"] = d["consumed_at"] is None and (
            caption_sha256 is None or d["caption_sha256"] == caption_sha256
        )
        out[d["platform"]] = d
    return out


async def consume_approval(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    caption_sha256: str,
    now: float | None = None,
) -> bool:
    await ensure_schema(session)
    now = time.time() if now is None else now
    upd = await session.execute(
        text(
            "UPDATE publish_approvals SET consumed_at=:now WHERE project_id=:p AND platform=:pl AND sha256=:s "
            "AND caption_sha256=:c AND consumed_at IS NULL"
        ),
        {"now": now, "p": project_id, "pl": platform, "s": sha256, "c": caption_sha256},
    )
    await _commit(session)
    return upd.rowcount == 1


async def revoke_approval(
    session: AsyncSession, *, project_id: int, platform: str, sha256: str, now: float | None = None
) -> dict:
    await ensure_schema(session)
    now = time.time() if now is None else now
    upd = await session.execute(
        text(
            "UPDATE publish_approvals SET consumed_at=:now WHERE project_id=:p AND platform=:pl AND sha256=:s "
            "AND consumed_at IS NULL"
        ),
        {"now": now, "p": project_id, "pl": platform, "s": sha256},
    )
    await _commit(session)
    if upd.rowcount != 1:
        raise PublicationRefused(
            "no_active_approval",
            "Действующего разрешения на эту площадку нет: оно не выдавалось, уже отозвано или уже использовано.",
        )
    return {"platform": platform, "sha256": sha256, "revoked_at": now}


async def _manual_target(
    session: AsyncSession, project_id: int, platform: str, sha256: str, now: float
) -> dict:
    await ensure_schema(session)
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    if row is None:
        raise PublicationRefused(
            "no_publication_record", "Для этого файла и площадки нет записи журнала: нечего подтверждать."
        )
    if row["status"] == S_PLANNED and not row.get("clicked_at") and (row.get("lease_until") or 0) >= now:
        raise PublicationRefused("busy", "Площадку сейчас обрабатывает воркер: подождите окончания шага.")
    return row


async def _log_manual(
    session: AsyncSession,
    row: dict,
    *,
    action: str,
    post_url: str | None,
    reason: str,
    actor: str,
    now: float,
) -> None:
    await session.execute(
        text(
            "INSERT INTO publication_manual_actions (publication_id, project_id, platform, sha256, action, "
            "prev_status, prev_post_url, post_url, reason, actor, created_at) "
            "VALUES (:i, :p, :pl, :s, :a, :ps, :pu, :u, :r, :by, :now)"
        ),
        {
            "i": row["id"],
            "p": row["project_id"],
            "pl": row["platform"],
            "s": row["sha256"],
            "a": action,
            "ps": row["status"],
            "pu": row.get("post_url"),
            "u": post_url,
            "r": reason,
            "by": actor[:120],
            "now": now,
        },
    )


async def confirm_manual(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    actor: str,
    post_url: str | None = None,
    reason: str | None = None,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    url, why = _clean_url(post_url), _clean_reason(reason)
    if url is None and len(why) < MIN_REASON_CHARS:
        raise PublicationRefused(
            "evidence_required", f"Нужна ссылка на пост или причина не короче {MIN_REASON_CHARS} символов."
        )
    row = await _manual_target(session, project_id, platform, sha256, now)
    if row["status"] == S_CONFIRMED:
        return {"changed": False, "status": S_CONFIRMED, "state": display_state(row), "pub_id": row["id"]}
    post_id = None if url else MANUAL_CONFIRMED_ID
    upd = await session.execute(
        text(
            "UPDATE publications SET status='confirmed', post_url=:u, post_id=:pid, error_code='manual_confirmed', "
            "clicked_at=COALESCE(clicked_at, :now), lease_owner=NULL, lease_until=NULL, updated_at=:now "
            "WHERE id=:id AND status=:prev"
        ),
        {"u": url, "pid": post_id, "now": now, "id": row["id"], "prev": row["status"]},
    )
    if upd.rowcount != 1:
        await session.rollback()
        raise PublicationRefused(
            "state_changed", "Запись изменилась во время действия: обновите список и повторите."
        )
    await _log_manual(session, row, action="confirm", post_url=url, reason=why, actor=actor, now=now)
    await _commit(session)
    return {
        "changed": True,
        "status": S_CONFIRMED,
        "state": D_CONFIRMED,
        "pub_id": row["id"],
        "prev_status": row["status"],
    }


async def mark_failed_manual(
    session: AsyncSession,
    *,
    project_id: int,
    platform: str,
    sha256: str,
    actor: str,
    reason: str | None,
    allow_republish: bool = False,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    why = _clean_reason(reason)
    if len(why) < MIN_REASON_CHARS:
        raise PublicationRefused("reason_required", f"Нужна причина не короче {MIN_REASON_CHARS} символов.")
    row = await _manual_target(session, project_id, platform, sha256, now)
    if row["status"] == S_CONFIRMED:
        raise PublicationRefused(
            "already_confirmed", "Площадка подтверждена: пометить её «не опубликовано» нельзя."
        )
    params = {"code": MANUAL_FAILED_CODE, "now": now, "id": row["id"], "prev": row["status"]}
    if allow_republish:
        sql = (
            "UPDATE publications SET status='failed', error_code=:code, clicked_at=NULL, attempts=0, "
            "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND status=:prev"
        )
    else:
        sql = (
            "UPDATE publications SET status='failed', error_code=:code, "
            "attempts=CASE WHEN attempts < :mx THEN :mx ELSE attempts END, "
            "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND status=:prev"
        )
        params["mx"] = MAX_PRE_CLICK_ATTEMPTS
    upd = await session.execute(text(sql), params)
    if upd.rowcount != 1:
        await session.rollback()
        raise PublicationRefused(
            "state_changed", "Запись изменилась во время действия: обновите список и повторите."
        )
    await _log_manual(
        session,
        row,
        action="mark_failed_republish" if allow_republish else "mark_failed",
        post_url=None,
        reason=why,
        actor=actor,
        now=now,
    )
    await _commit(session)
    return {
        "changed": True,
        "status": S_FAILED,
        "pub_id": row["id"],
        "allow_republish": bool(allow_republish),
        "prev_status": row["status"],
    }


async def list_manual_actions(session: AsyncSession, project_id: int) -> list[dict]:
    await ensure_schema(session)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, publication_id, platform, sha256, action, prev_status, post_url, reason, actor, created_at "
                    "FROM publication_manual_actions WHERE project_id=:p ORDER BY id"
                ),
                {"p": project_id},
            )
        )
        .mappings()
        .all()
    )
    await _commit(session)
    return [dict(r) for r in rows]


PS_PUBLISHED = "published"
PS_WORKING = "publishing"
PS_NEEDS_HUMAN = "needs_human"
PS_AWAITING_APPROVAL = "awaiting_approval"
PS_CLOSED = "resolved_unpublished"


def derive_project_state(
    rows: list[dict],
    platforms: list[str],
    approved: set[str],
    *,
    legacy: dict[str, bool] | None = None,
    now: float | None = None,
    publish_enabled: bool | None = None,
) -> dict:
    summ = summarize(rows, platforms, now)
    confirmed = list(summ["confirmed"])
    human = list(summ["needs_human"])
    retry = list(summ["retryable"]) + list(summ["skipped"])
    for p in list(retry):
        if legacy and p in legacy:
            retry.remove(p)
            (confirmed if legacy[p] else human).append(p)
    publish_off = publish_enabled is False
    approved_off = [p for p in retry if p in approved] if publish_off else []
    working_retry = [p for p in retry if p in approved] if not publish_off else []
    awaiting = [p for p in retry if p not in approved or publish_off]
    if len(set(confirmed)) == len(platforms):
        state = PS_PUBLISHED
    elif summ["busy"] or working_retry:
        state = PS_WORKING
    elif human:
        state = PS_NEEDS_HUMAN
    elif awaiting:
        state = PS_AWAITING_APPROVAL
    else:
        state = PS_CLOSED
    return {
        "state": state,
        "confirmed": confirmed,
        "needs_human": human,
        "awaiting_approval": [p for p in awaiting if p not in approved_off],
        "approved_publish_off": approved_off,
        "retry_approved": working_retry,
        "busy": list(summ["busy"]),
        "manual_failed": list(summ["manual_failed"]),
    }
