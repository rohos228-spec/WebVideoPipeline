"""Здоровье моделей: агрегаты реальных успехов/ошибок по каждой модели.

Пишется из ``api_tracker_hook.record_api_call`` (все реальные вызовы генерации/LLM)
и тестового хука. Наружу отдаются только имена моделей — без провайдеров.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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


_KIND = {"text": "llm", "llm": "llm", "chat": "llm", "image": "image", "video": "video", "audio": "audio", "tts": "audio", "music": "audio", "sfx": "audio"}


def parse_window(window: str | None) -> int:
    """"24h" / "7d" / "48" -> hours (default 24, max 90 days)."""
    w = (window or "24h").strip().lower()
    try:
        if w.endswith("d"):
            h = int(float(w[:-1]) * 24)
        else:
            h = int(float(w.rstrip("h")))
    except ValueError:
        h = 24
    return max(1, min(h, 24 * 90))


def get_health(window_hours: int = 24) -> dict[str, Any]:
    """Real per-model success/error stats over the last ``window_hours``."""
    since_dt = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    since = since_dt.isoformat()
    bucket_h = 1 if window_hours <= 48 else 24
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT model, ts, ok, duration_sec, error, call_type FROM model_runs "
            "WHERE ts >= ? ORDER BY id DESC",
            (since,),
        ).fetchall()
    by: dict[str, list[tuple]] = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    models: list[dict[str, Any]] = []
    for m in sorted(by):
        rs = by[m]
        ok = sum(1 for r in rs if r[2])
        fail = len(rs) - ok
        durs = [r[3] for r in rs if r[2] and r[3]]
        series: dict[str, dict[str, Any]] = {}
        for r in rs:
            t = datetime.fromisoformat(r[1])
            t = t.replace(minute=0, second=0, microsecond=0)
            if bucket_h == 24:
                t = t.replace(hour=0)
            k = t.isoformat()
            b = series.setdefault(k, {"ts": k, "success": 0, "errors": 0})
            b["success" if r[2] else "errors"] += 1
        models.append(
            {
                "model": m,
                "title": m,
                "kind": _KIND.get((rs[0][5] or "").lower(), (rs[0][5] or "").lower() or None),
                "success": ok,
                "errors": fail,
                "total": len(rs),
                "success_rate": round(ok / len(rs), 4),
                "avg_latency_s": round(sum(durs) / len(durs), 2) if durs else None,
                "last_error": next((r[4] for r in rs if not r[2] and r[4]), ""),
                "updated_at": rs[0][1],
                "last_runs": [{"ts": r[1], "ok": bool(r[2])} for r in rs[:_LAST_RUNS]],
                "series": sorted(series.values(), key=lambda b: b["ts"]),
            }
        )
    return {
        "models": models,
        "window_hours": window_hours,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
