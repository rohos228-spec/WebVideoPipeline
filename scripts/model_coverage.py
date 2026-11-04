"""Модель на ноде против модели, которая реально ушла провайдеру.

Только чтение: SQLite-файлы папки данных (state.db и, если остались от линии video-pipeline,
project.db проектов) и studio-live.log. На этой линии база одна (DATABASE_URL); на Postgres
скрипт не работает — он читает файлы SQLite напрямую.
Для каждого проекта и каждой ноды с моделью: что задано на ноде сейчас, что
привязалось к шагу в последнем прогоне (node_llm), что ушло провайдеру
(text_llm.chat / outsee_api / kie_http) и совпадает ли.

Usage:
  python scripts/model_coverage.py --data-dir C:\\video-pipeline\\_chat_test2\\data
  python scripts/model_coverage.py --data-dir ... --project 6 --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import model_coverage as mc  # noqa: E402

_COLUMNS = (
    "node_key",
    "title",
    "configured",
    "bound",
    "sent",
    "sent_provider",
    "sent_at",
    "verdict_ru",
    "note",
)
_HEADERS = (
    "Нода",
    "Название",
    "Задано на ноде",
    "Привязано в прогоне",
    "Ушло провайдеру",
    "Провайдер",
    "Когда",
    "Итог",
    "Пометка",
)


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _json(raw: str | None) -> dict:
    try:
        val = json.loads(raw or "{}")
    except ValueError:
        return {}
    return val if isinstance(val, dict) else {}


def _project_dir(data_dir: Path, slug: str, batch_slug: str | None) -> Path:
    if batch_slug:
        return data_dir / "batches" / batch_slug / "sub" / slug
    return data_dir / "videos" / slug


def load_projects(data_dir: Path, only: set[int] | None = None) -> list[SimpleNamespace]:
    """Проекты из state.db; meta и генераторы — из project.db, если он есть."""
    out = []
    with _read_only(data_dir / "state.db") as db:
        rows = db.execute(
            "select id, slug, title, batch_slug, meta, image_generator, video_generator "
            "from projects order by id"
        ).fetchall()
    for pid, slug, title, batch_slug, meta, image_gen, video_gen in rows:
        if only and pid not in only:
            continue
        source = "state.db"
        own = _project_dir(data_dir, slug, batch_slug) / "project.db"
        if own.exists():
            with _read_only(own) as db:
                local = db.execute(
                    "select meta, image_generator, video_generator from projects where id = ?", (pid,)
                ).fetchone()
            if local is not None:
                meta, image_gen, video_gen = local
                source = "project.db"
        out.append(
            SimpleNamespace(
                id=pid,
                slug=slug,
                title=title or slug,
                meta=_json(meta),
                image_generator=image_gen,
                video_generator=video_gen,
                source=source,
            )
        )
    return out


def _md(value) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "/").replace("\n", " ")


def render(projects: list[SimpleNamespace], rows: list[dict], scan: mc.LogScan) -> str:
    lines = []
    for p in projects:
        mine = [r for r in rows if r["project_id"] == p.id]
        lines.append(f"## #{p.id} {p.title} (данные из {p.source})")
        if not mine:
            lines += ["", "Нод с моделью на канвасе нет.", ""]
            continue
        lines += ["", "| " + " | ".join(_HEADERS) + " |", "|" + "---|" * len(_HEADERS)]
        lines += ["| " + " | ".join(_md(r[c]) for c in _COLUMNS) + " |" for r in mine]
        lines.append("")
    total = mc.summary(rows)
    lines.append("Итого: " + ", ".join(f"{mc.VERDICT_RU[k]} {v}" for k, v in total.items()))
    lines.append(
        f"Текстовых вызовов в логе {scan.text_calls}, без привязки к ноде (чат, служебные) "
        f"{scan.unmatched_text_calls}."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument(
        "--log", type=Path, action="append", help="лог Студии; по умолчанию <data-dir>/studio-live.log"
    )
    ap.add_argument("--project", type=int, action="append")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    logs = args.log or [args.data_dir / "studio-live.log"]
    lines: list[str] = []
    for path in logs:
        lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    scan = mc.scan_log(lines)
    projects = load_projects(args.data_dir, set(args.project or []))
    rows = [r for p in projects for r in mc.project_rows(p, scan)]
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        print(
            json.dumps(
                {
                    "rows": rows,
                    "summary": mc.summary(rows),
                    "text_calls": scan.text_calls,
                    "unmatched_text_calls": scan.unmatched_text_calls,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render(projects, rows, scan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
