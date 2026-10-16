"""Здоровье моделей: агрегаты реальных успехов/ошибок по каждой модели.

Пишется из ``api_tracker_hook.record_api_call`` (все реальные вызовы генерации/LLM)
и тестового хука. Наружу отдаются только имена моделей — без провайдеров.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LAST_RUNS = 20

_PROVIDER_RE = re.compile(
    r"(?i)\b(52pay|kie(\.ai)?|outsee|vibecode|wavespeed|elevenlabs|anthropic|openai|grsai)\b"
)


def _db_path() -> Path:
    env = os.environ.get("VIA_MODEL_HEALTH_DB", "").strip()
    if env:
        return Path(env)
    data = os.environ.get("VIA_DATA_DIR", "").strip()
    if data:
        return Path(data) / "model_health.db"
    try:
        from app.settings import settings

        return Path(settings.data_dir) / "model_health.db"
    except Exception:  # noqa: BLE001
        return Path("data") / "model_health.db"


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS model_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model TEXT NOT NULL,
            call_type TEXT DEFAULT '',
            ok INTEGER NOT NULL,
            duration_sec REAL DEFAULT 0,
            error TEXT DEFAULT ''
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_model_runs_model ON model_runs(model, id)")
    return conn


def scrub(text: str) -> str:
    """Убирает имена провайдеров из текста ошибки."""
    return _PROVIDER_RE.sub("VIA", text or "")


def public_model_name(model: str) -> str:
    m = (model or "").strip()
    # "kie/veo-3" / "outsee:flux" -> "veo-3" / "flux"
    m = re.split(r"[/:]", m)[-1] if re.search(r"[/:]", m) and _PROVIDER_RE.search(m) else m
    return scrub(m).strip() or "unknown"


def record_run(
    *,
    model: str,
    ok: bool,
    call_type: str = "",
    duration_sec: float = 0.0,
    error: str = "",
) -> None:
    name = public_model_name(model)
    ts = datetime.now(timezone.utc).isoformat()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO model_runs(ts, model, call_type, ok, duration_sec, error) VALUES (?,?,?,?,?,?)",
            (ts, name, call_type or "", 1 if ok else 0, float(duration_sec or 0), scrub(error)[:500]),
        )


def get_health(window: int = 500) -> list[dict[str, Any]]:
    """Сводка по моделям за последние ``window`` запусков каждой модели."""
    with _LOCK, _connect() as conn:
        models = [r[0] for r in conn.execute("SELECT DISTINCT model FROM model_runs ORDER BY model")]
        out: list[dict[str, Any]] = []
        for m in models:
            rows = conn.execute(
                "SELECT ts, ok, duration_sec, error, call_type FROM model_runs "
                "WHERE model=? ORDER BY id DESC LIMIT ?",
                (m, window),
            ).fetchall()
            ok = sum(1 for r in rows if r[1])
            fail = len(rows) - ok
            durs = [r[2] for r in rows if r[1] and r[2]]
            last_err = next((r[3] for r in rows if not r[1] and r[3]), "")
            out.append(
                {
                    "model": m,
                    "call_type": rows[0][4] if rows else "",
                    "success": ok,
                    "errors": fail,
                    "total": len(rows),
                    "success_rate": round(ok / len(rows), 4) if rows else None,
                    "avg_duration_sec": round(sum(durs) / len(durs), 2) if durs else None,
                    "updated_at": rows[0][0] if rows else None,
                    "last_error": last_err,
                    "last_runs": [
                        {"ts": r[0], "ok": bool(r[1])} for r in rows[:_LAST_RUNS]
                    ],
                }
            )
        return out