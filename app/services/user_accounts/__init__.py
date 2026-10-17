"""VIA user accounts: users, sessions, email verification, password reset, social links, teams.

Storage: SQLite (local/test) via db/migrations/accounts_*.sql; DDL is portable to PostgreSQL.
Mail goes through ``mailer.send_mail`` (looked up at call time so it can be swapped in tests).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from app import settings_auth
from app.services.user_accounts import mailer, templates

log = logging.getLogger("via.auth")

SESSION_TTL = 86400 * 7
RESET_TTL = 3600
VERIFY_TTL = 86400
_PBKDF_ITER = 260_000
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
ROLES = ("owner", "admin", "user", "member")
_ready: set[str] = set()


# ---------------------------------------------------------------- db
def _migrate(c: sqlite3.Connection, path: Path) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)")
    done = {r[0] for r in c.execute("SELECT name FROM schema_migrations")}
    for f in sorted(MIGRATIONS_DIR.glob("accounts_*.sql")):
        if f.name.endswith(".postgres.sql") or f.name in done:
            continue
        c.executescript(f.read_text(encoding="utf-8"))
        c.execute("INSERT INTO schema_migrations VALUES(?,?)", (f.name, time.time()))
    _import_legacy(c, path)


def _import_legacy(c: sqlite3.Connection, path: Path) -> None:
    """One-time import of users from the pre-merge data/users.db (same password hash format)."""
    legacy = path.with_name("users.db")
    if not legacy.exists() or c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    try:
        src = sqlite3.connect(legacy)
        src.row_factory = sqlite3.Row
        for u in src.execute("SELECT * FROM users"):
            c.execute("INSERT OR IGNORE INTO users(email,username,name,password_hash,role,plan,email_verified,created_at,last_login)"
                      " VALUES(?,?,?,?,?,?,?,?,?)",
                      (u["email"], u["username"], u["name"], u["password_hash"],
                       u["role"] if u["role"] in ROLES else "user", u["plan"], u["email_verified"],
                       u["created_at"], u["last_login"]))
        src.close()
    except Exception as e:  # noqa: BLE001
        log.warning("legacy users import skipped: %s", e)


def _conn() -> sqlite3.Connection:
    path = settings_auth.accounts_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    if key not in _ready or not path.exists():
        with c:
            _migrate(c, path)
        _ready.add(key)
    return c


class _Tx:
    def __enter__(self) -> sqlite3.Connection:
        self.c = _conn()
        return self.c

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self.c.commit()
        else:
            self.c.rollback()
        self.c.close()


def db() -> _Tx:
    return _Tx()


# ---------------------------------------------------------------- helpers
def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF_ITER)
    return f"pbkdf2_sha256${_PBKDF_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:  # noqa: BLE001
        return False


def links_for(user_id: int) -> list[dict]:
    with db() as c:
        return [{"provider": r["provider"], "name": r["display_name"], "linked_at": r["linked_at"]}
                for r in c.execute("SELECT * FROM oauth_links WHERE user_id=? ORDER BY provider", (user_id,))]


def public(u: sqlite3.Row | dict) -> dict:
    u = dict(u)
    out = {k: u.get(k) for k in ("id", "email", "username", "name", "role", "plan", "team_owner_id",
                                 "created_at", "last_login")}
    out["email_verified"] = bool(u.get("email_verified"))
    out["oauth"] = links_for(u["id"])
    return out


def get(user_id: int):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def get_by_login(login: str):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE lower(email)=lower(?) OR lower(username)=lower(?)",
                         (login, login)).fetchone()


def _free_username(c: sqlite3.Connection, base: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", base)[:24] or "user"
    if len(base) < 3:
        base = (base + "user")[:24]
    name, i = base, 1
    while c.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (name,)).fetchone():
        i += 1
        name = f"{base}{i}"
    return name


def create_user(email: str, password: str, username: str | None = None, name: str = "",
                plan: str | None = None) -> dict:
    email = email.strip().lower()
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
            raise ValueError("email_taken")
        if username:
            if c.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
                raise ValueError("username_taken")
        else:
            username = _free_username(c, email.split("@")[0])
        role = "owner" if email and email == settings_auth.owner_email() else "user"
        cur = c.execute(
            "INSERT INTO users(email,username,name,password_hash,role,plan,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, username, name or username, hash_password(password), role, plan, time.time()))
        uid = cur.lastrowid
    return public(get(uid))


def set_role(user_id: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError("bad_role")
    with db() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))


def set_plan(user_id: int, plan: str | None) -> None:
    """Called by billing when a subscription changes."""
    with db() as c:
        c.execute("UPDATE users SET plan=? WHERE id=?", (plan, user_id))


def set_team(user_id: int, team_owner_id: int | None) -> None:
    with db() as c:
        c.execute("UPDATE users SET team_owner_id=? WHERE id=?", (team_owner_id, user_id))


def team_of(owner_id: int) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM users WHERE team_owner_id=? ORDER BY id", (owner_id,)).fetchall()
    return [public(r) for r in rows]


def list_users() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [public(r) for r in rows]


# ---------------------------------------------------------------- sessions
def new_session(user_id: int, ua: str = "") -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent) VALUES(?,?,?,?,?)",
                  (_h(tok), user_id, now, now + SESSION_TTL, (ua or "")[:200]))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
    return tok


def user_for_session(tok: str | None):
    if not tok:
        return None
    with db() as c:
        return c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                         "WHERE s.token_hash=? AND s.expires_at>?", (_h(tok), time.time())).fetchone()


def list_sessions(user_id: int, current_tok: str | None) -> list[dict]:
    cur = _h(current_tok) if current_tok else ""
    with db() as c:
        rows = c.execute("SELECT * FROM sessions WHERE user_id=? AND expires_at>? ORDER BY created_at DESC",
                         (user_id, time.time())).fetchall()
    return [{"id": r["id"], "created_at": r["created_at"], "expires_at": r["expires_at"],
             "user_agent": r["user_agent"], "current": r["token_hash"] == cur} for r in rows]


def drop_session(tok: str) -> None:
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_h(tok),))


def drop_session_id(user_id: int, sid: int) -> bool:
    with db() as c:
        return c.execute("DELETE FROM sessions WHERE id=? AND user_id=?", (sid, user_id)).rowcount > 0


# ---------------------------------------------------------------- tokens
def _issue(user_id: int, kind: str, tok: str, ttl: int, key: str | None = None) -> str:
    with db() as c:
        c.execute("UPDATE tokens SET used=1 WHERE user_id=? AND kind=?", (user_id, kind))
        c.execute("INSERT OR REPLACE INTO tokens VALUES(?,?,?,?,0)", (_h(key or tok), user_id, kind, time.time() + ttl))
    return tok


def _consume(key: str, kind: str):
    with db() as c:
        r = c.execute("SELECT * FROM tokens WHERE token_hash=? AND kind=? AND used=0 AND expires_at>?",
                      (_h(key), kind, time.time())).fetchone()
        if r:
            c.execute("UPDATE tokens SET used=1 WHERE token_hash=?", (_h(key),))
        return r


def check_reset(tok: str) -> bool:
    with db() as c:
        return bool(c.execute("SELECT 1 FROM tokens WHERE token_hash=? AND kind='reset' AND used=0 AND expires_at>?",
                              (_h(tok), time.time())).fetchone())


def _mark_verified(user_id: int) -> None:
    with db() as c:
        c.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
        c.execute("UPDATE tokens SET used=1 WHERE user_id=? AND kind IN ('verify','verify_code')", (user_id,))


def verify_by_token(tok: str) -> bool:
    r = _consume(tok, "verify")
    if r:
        _mark_verified(r["user_id"])
    return bool(r)


def verify_by_code(user_id: int, code: str) -> bool:
    r = _consume(f"code:{user_id}:{code}", "verify_code")
    if r:
        _mark_verified(user_id)
    return bool(r)


def send_verification(u: Any, base_url: str) -> dict:
    tok = _issue(u["id"], "verify", secrets.token_urlsafe(32), VERIFY_TTL)
    code = f"{secrets.randbelow(10**6):06d}"
    _issue(u["id"], "verify_code", code, VERIFY_TTL, key=f"code:{u['id']}:{code}")
    link = f"{base_url}/verify-email?token={tok}"
    subject, text, html = templates.verify_email(u["name"] or u["username"], link, code)
    sent = mailer.send_mail(u["email"], subject, text, html=html)
    return {"mail_sent": bool(sent)}


def send_reset(u: Any, base_url: str) -> dict:
    tok = _issue(u["id"], "reset", secrets.token_urlsafe(32), RESET_TTL)
    link = f"{base_url}/reset-password?token={tok}"
    subject, text, html = templates.reset_password(u["name"] or u["username"], link)
    return {"mail_sent": bool(mailer.send_mail(u["email"], subject, text, html=html))}


def reset_password(tok: str, pw: str) -> bool:
    r = _consume(tok, "reset")
    if not r:
        return False
    set_password(r["user_id"], pw)
    return True


def set_password(user_id: int, pw: str) -> None:
    with db() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), user_id))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def delete_user(user_id: int) -> None:
    with db() as c:
        for t in ("sessions", "tokens", "oauth_links"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))  # noqa: S608
        c.execute("UPDATE users SET team_owner_id=NULL WHERE team_owner_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


# ---------------------------------------------------------------- social links
def link_oauth(user_id: int, provider: str, external_id: str, name: str = "") -> None:
    with db() as c:
        r = c.execute("SELECT user_id FROM oauth_links WHERE provider=? AND external_id=?",
                      (provider, external_id)).fetchone()
        if r and r["user_id"] != user_id:
            raise ValueError("linked_to_other")
        c.execute("DELETE FROM oauth_links WHERE provider=? AND user_id=?", (provider, user_id))
        c.execute("INSERT OR REPLACE INTO oauth_links VALUES(?,?,?,?,?)",
                  (provider, external_id, user_id, name[:120], time.time()))


def unlink_oauth(user_id: int, provider: str) -> bool:
    with db() as c:
        return c.execute("DELETE FROM oauth_links WHERE provider=? AND user_id=?", (provider, user_id)).rowcount > 0


def user_by_oauth(provider: str, external_id: str):
    with db() as c:
        return c.execute("SELECT u.* FROM oauth_links o JOIN users u ON u.id=o.user_id "
                         "WHERE o.provider=? AND o.external_id=?", (provider, external_id)).fetchone()
