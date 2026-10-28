"""Чтение отклонённых попыток LLM (``<data_dir>/llm_rejects``) для диагноза.

Пишет их ``app.contracts.policy._write_reject``: на каждую отклонённую
попытку ``<label>_a<N>_<kind>.txt`` (заголовок + сырой ответ) и сайдкар
``.json`` (модель, выдержка промпта, размер входа, пояснение модели).
Старые reject'ы без сайдкара тоже читаются — поля берутся из заголовка .txt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.contracts.policy import _REPLY_EXCERPT, excerpt

_NAME_RE = re.compile(r"^(?P<label>.+)_a(?P<attempt>\d+)_(?P<kind>[A-Za-z]+)$")
_HEADER_KEYS = ("contract", "kind", "error", "model", "input", "report", "reply_len")


def _attempt_no(path: Path) -> int:
    m = _NAME_RE.match(path.stem)
    return int(m.group("attempt")) if m else 0


def _parse_txt(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    head, _, body = text.partition("\n\n")
    hdr: dict[str, str] = {}
    last = ""
    for line in head.splitlines():
        key, _, val = line[2:].partition(": ") if line.startswith("# ") else ("", "", "")
        if key in _HEADER_KEYS:
            hdr[key] = val.strip()
            last = key
        elif last:
            # Многострочная ошибка (список проблем Pydantic) — продолжение.
            hdr[last] = f"{hdr[last]}\n{line}"
    rec: dict[str, Any] = {
        "contract": hdr.get("contract", ""),
        "kind": hdr.get("kind", ""),
        "error": hdr.get("error", ""),
        "model": "" if hdr.get("model") in (None, "-") else hdr["model"],
        "report": "" if hdr.get("report") in (None, "-") else hdr["report"],
        "reply_excerpt": excerpt(body, _REPLY_EXCERPT),
        "reply_len": len(body),
    }
    m = re.search(r"frames=(\S+)\s+chars=(\S+)", hdr.get("input", ""))
    if m:
        rec["input_frames"] = int(m.group(1)) if m.group(1).isdigit() else None
        rec["input_chars"] = int(m.group(2)) if m.group(2).isdigit() else None
    return rec


def recent_rejects(
    data_dir: str | Path,
    label_prefix: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Последние отклонённые ответы LLM проекта, новые первыми.

    Args:
        data_dir: папка данных проекта (``project.data_dir``) или сама
            ``.../llm_rejects``.
        label_prefix: фильтр по началу метки, напр. ``"apply_ops_n_excel_gpt_1"``
            (метка вызова гпт-оператора — ``apply_ops_<node_key>``). Пробелы
            и прочие символы, которые в имени файла заменены на ``_``, учтены.
        limit: сколько записей вернуть (≤0 — все).

    Returns:
        Список dict, каждый с ключами: ``label``, ``attempt`` (int),
        ``kind`` (parse|validate), ``contract``, ``error``, ``reply_excerpt``,
        ``reply_len``, ``report`` (пояснение модели, «» если нет),
        ``model`` («» если неизвестна), ``input_frames``, ``input_chars``
        (None если неизвестно), ``expected_uuids`` (list), ``prompt_excerpt``
        («» для старых записей), ``mtime`` (float, unix), ``path`` (str).
        Нет папки / ошибки чтения — пустой список или запись пропускается;
        функция не бросает.
    """
    base = Path(data_dir)
    reject_dir = base if base.name == "llm_rejects" else base / "llm_rejects"
    if not reject_dir.is_dir():
        return []
    prefix = None
    if label_prefix:
        prefix = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label_prefix)
    try:
        files = [p for p in reject_dir.glob("*.txt") if p.is_file()]
        # Попытки одной единицы пишутся почти одновременно — при равном
        # mtime порядок держит номер попытки.
        files.sort(key=lambda p: (p.stat().st_mtime, _attempt_no(p)), reverse=True)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for p in files:
        m = _NAME_RE.match(p.stem)
        label = m.group("label") if m else p.stem
        if prefix and not label.startswith(prefix):
            continue
        try:
            rec = _parse_txt(p)
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rec.update(
            {
                "label": label,
                "attempt": int(m.group("attempt")) if m else 0,
                "kind": rec.get("kind") or (m.group("kind") if m else ""),
                "expected_uuids": [],
                "prompt_excerpt": "",
                "mtime": mtime,
                "path": str(p),
            }
        )
        rec.setdefault("input_frames", None)
        rec.setdefault("input_chars", None)
        side = p.with_suffix(".json")
        if side.is_file():
            try:
                data = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                for k in (
                    "label",
                    "contract",
                    "error",
                    "model",
                    "report",
                    "input_frames",
                    "input_chars",
                    "expected_uuids",
                    "prompt_excerpt",
                    "reply_excerpt",
                    "reply_len",
                ):
                    if data.get(k) not in (None, ""):
                        rec[k] = data[k]
        out.append(rec)
        if limit > 0 and len(out) >= limit:
            break
    return out
