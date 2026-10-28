"""diag.node_failure: почему упала нода — по сырым данным на диске, а не пересказ ошибки.

Чат на «разберись, в чём проблема» видел только «схема отклонена 3 раза» и отвечал,
что причину установить нельзя. Сами отклонённые ответы модели лежат в
``<проект>/llm_rejects/<label>_a<N>_<kind>.txt`` (``app/contracts/policy.py``), вход ноды —
в ``excel_gpt_uploads/<нода>/db_frames.json``, ошибки пачек — в ``node_trace``. Здесь это
читается, причина раскладывается правилами (без LLM) и к ней даются только существующие
действия: повтор шага, модель не дороже, вариант промта, предложение правки промта.
Только чтение: ничего не запускает и не меняет.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.action_layer import builtin_insight as bi
from app.services.action_layer.builtin_group import _NODE_KEY, _PID, _effective_project
from app.services.action_layer.builtin_montage import _PRE_PROJECT
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import RISK_READ, ActionSpec

ACTION_ID = "diag.node_failure"
_ERROR_CHARS = 1500
_REPLY_CHARS = 600
_REPORT_CHARS = 400
_TRACE_CHARS = 800
_REJECTS_MAX = 4
# Попытки одного прогона пишутся подряд; файл старше на полчаса — уже прошлый запуск.
_RUN_WINDOW_S = 30 * 60
_KEY_IN_LABEL = re.compile(r"(n_[a-z0-9_]+?)_a(\d+)_([a-z_]+)\.txt$")

CAUSE_RU = {
    "empty_input": "на входе ноды нет кадров",
    "empty_ops": "модель вернула пустой список правок",
    "unknown_fields": "модель назвала поля не теми именами",
    "truncated": "ответ модели оборван",
    "refusal": "модель отказалась отвечать",
    "provider_error": "сбой у провайдера модели",
    "schema_other": "ответ модели не прошёл проверку формата",
    "unknown": "причина по данным не видна",
}

_PROVIDER = re.compile(
    r"(?:http|status|код|error)\W{0,3}(?:5\d\d|429)\b|internal server error|bad gateway|"
    r"service unavailable|gateway timeout|too many requests|timeout|timed out|таймаут|"
    r"время ожидания|quota|rate.?limit|insufficient|баланс|overloaded|"
    r"connection (?:reset|error|refused)",
    re.I,
)
_TRUNC = re.compile(
    r"finish_reason\W*length|max_tokens|unterminated|обрез|оборван|незакрыт",
    re.I,
)
_REFUSAL = re.compile(
    r"i can(?:no|')t|i'm sorry|i am sorry|i cannot|не могу (?:помочь|выполнить)|извините|"
    r"к сожалению|as an ai|не имею возможности",
    re.I,
)
_UNKNOWN_FIELDS = re.compile(r"неизвестн\w* пол\w*\s*\[([^\]]*)\]", re.I)
_EMPTY_OPS = re.compile(r"пуст\w* ops|ops.{0,20}пуст|нечего применять|empty ops", re.I)
_EMPTY_INPUT = re.compile(r"нет кадров|кадров нет|0 кадров|no frames|пуст\w* (?:вход|таблиц)", re.I)


# ---------- диск ----------


def parse_reject_text(text: str) -> dict[str, Any]:
    """Заголовок «# contract/# kind/# error/# model/# input/# report/# reply_len» и сам ответ.

    Ошибка бывает многострочной (список нарушений схемы) — до следующей строки «# ключ:»;
    без «# reply_len» (старые файлы) ответ начинается после первой пустой строки.
    """
    out: dict[str, Any] = {
        "contract": "",
        "kind": "",
        "error": "",
        "model": "",
        "input": "",
        "report": "",
        "reply": "",
    }
    text = text or ""
    head, sep, body = text.partition("\n\n")
    if not sep:
        head, body = text, ""
    m = re.search(r"^# reply_len:.*$", text, re.M)
    if m:
        head, body = text[: m.start()], text[m.end() :].lstrip("\r\n")
    current = ""
    for line in head.splitlines():
        hit = re.match(r"^# (contract|kind|error|model|input|report|reply_len):\s?(.*)$", line)
        if hit:
            current = hit.group(1)
            if current in out:
                out[current] = hit.group(2)
            continue
        if current == "error":
            out["error"] += "\n" + line
    out["error"] = out["error"].strip()
    for key in ("model", "input", "report"):
        out[key] = "" if out[key].strip() == "-" else out[key].strip()
    out["reply"] = body
    return out


def _reply_report(reply: str) -> str:
    """Поле report из ответа модели: там она часто сама пишет, почему ничего не вернула."""
    raw = (reply or "").strip()
    data: Any = None
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(raw[start : end + 1])
            except ValueError:
                data = None
    if isinstance(data, dict):
        report = data.get("report") or data.get("отчёт") or data.get("error")
        if report:
            return bi._clip(
                report if isinstance(report, str) else json.dumps(report, ensure_ascii=False), _REPORT_CHARS
            )
        return ""
    hit = re.search(r'"report"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
    return bi._clip(hit.group(1), _REPORT_CHARS) if hit else ""


def _ops_count(reply: str) -> int | None:
    raw = (reply or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if not 0 <= start < end:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    ops = data.get("ops") if isinstance(data, dict) else None
    return len(ops) if isinstance(ops, list) else None


def _from_shared(data_dir: Path, node_key: str) -> list[dict[str, Any]] | None:
    """Общий разбор отказов, если его уже добавили; без него — свой разбор файлов ниже."""
    fn = None
    for mod in ("app.services.llm_attempts", "app.contracts.policy"):
        try:
            fn = getattr(__import__(mod, fromlist=["recent_rejects"]), "recent_rejects", None)
        except Exception:
            fn = None
        if callable(fn):
            break
    if not callable(fn):
        return None
    try:
        rows = fn(data_dir, label_prefix=None, limit=0)
    except Exception:
        return None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("file") or row.get("path") or "")
        if not re.search(rf"(?:^|_){re.escape(node_key)}(?:_a\d+|$)", Path(label).name):
            continue
        reply = str(row.get("reply") or row.get("reply_excerpt") or "")
        out.append(
            {
                "file": Path(str(row.get("path") or row.get("file") or label)).name,
                "attempt": row.get("attempt"),
                "kind": str(row.get("kind") or ""),
                "contract": str(row.get("contract") or ""),
                "error": str(row.get("error") or ""),
                "reply": reply,
                "report": str(row.get("report") or "") or _reply_report(reply),
                "model": row.get("model"),
                "prompt_excerpt": row.get("prompt_excerpt") or row.get("prompt"),
                "input_frames": row.get("input_frames"),
                "at": row.get("ts") or row.get("at") or _iso_ts(row.get("mtime")),
                "_ts": _ts_of(row.get("mtime") or row.get("ts") or row.get("at")),
            }
        )
    return out


def _iso_ts(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")


def _ts_of(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def read_rejects(data_dir: Path | str, node_key: str) -> list[dict[str, Any]]:
    """Отклонённые ответы последнего прогона ноды, старые → новые."""
    data_dir = Path(data_dir)
    rows = _from_shared(data_dir, node_key)
    if not rows:
        rows = []
        folder = data_dir / "llm_rejects"
        try:
            files = [p for p in folder.iterdir() if p.is_file() and p.suffix == ".txt"]
        except OSError:
            files = []
        for path in files:
            hit = _KEY_IN_LABEL.search(path.name)
            if not hit or hit.group(1) != node_key:
                continue
            try:
                parsed = parse_reject_text(path.read_text(encoding="utf-8", errors="replace"))
                ts = path.stat().st_mtime
            except OSError:
                continue
            rows.append(
                {
                    "file": path.name,
                    "attempt": int(hit.group(2)),
                    "kind": parsed["kind"] or hit.group(3),
                    "contract": parsed["contract"],
                    "error": parsed["error"],
                    "reply": parsed["reply"],
                    "report": parsed["report"] or _reply_report(parsed["reply"]),
                    "model": parsed["model"] or None,
                    "prompt_excerpt": None,
                    "input_frames": _frames_of(parsed["input"]),
                    "at": _iso_ts(ts),
                    "_ts": ts,
                }
            )
    if not rows:
        return []
    rows.sort(key=lambda r: (r["_ts"], r.get("attempt") or 0))
    newest = rows[-1]["_ts"]
    fresh = [r for r in rows if not newest or newest - r["_ts"] <= _RUN_WINDOW_S]
    return fresh[-_REJECTS_MAX:]


def _frames_of(header: str) -> int | None:
    hit = re.search(r"frames=(\d+)", header or "")
    return int(hit.group(1)) if hit else None


def input_size(data_dir: Path | str, node_key: str) -> dict[str, Any] | None:
    """Сколько кадров ушло модели: db_frames.json ноды и сколько пачек."""
    from app.services.excel_gpt_node import safe_upload_node_key

    folder = Path(data_dir) / "excel_gpt_uploads" / safe_upload_node_key(node_key)
    path = folder / "db_frames.json"
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        batches = len(list(folder.glob("db_frames_batch_*.json")))
    except (OSError, ValueError):
        return None
    frames = data.get("frames") if isinstance(data, dict) else data
    count = len(frames) if isinstance(frames, list) else None
    return {"frames": count, "batches": batches, "file": "db_frames.json"}


def trace_failure(data_dir: Path | str, node_key: str) -> dict[str, Any] | None:
    """Последний прогон ноды в дневнике: статус, ошибка, ошибка первой упавшей пачки."""
    from app.services.node_trace import _safe_name, read_run, trace_root

    folder = trace_root(data_dir) / _safe_name(node_key)
    try:
        runs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.endswith(".del"))
    except OSError:
        return None
    if not runs:
        return None
    run = read_run(runs[-1])
    batch_error = ""
    for call_dir in sorted(runs[-1].glob("call_*")):
        path = call_dir / "error.txt"
        try:
            if path.is_file():
                batch_error = f"{call_dir.name}: " + path.read_text(encoding="utf-8", errors="replace")
                break
        except OSError:
            continue
    skipped = [s for s in run.get("skipped") or [] if isinstance(s, dict)]
    return {
        "status": run.get("status"),
        "error": bi._clip(run.get("error"), _ERROR_CHARS) or None,
        "batch_error": bi._clip(batch_error, _TRACE_CHARS) or None,
        "skipped": [bi._clip(s.get("reason"), 300) for s in skipped[:3]],
        "calls": run.get("calls"),
    }


def metrics_line(data_dir: Path | str, node_key: str) -> dict[str, Any] | None:
    """Последняя запись llm_metrics.jsonl по ноде: попытки и сколько отклонено."""
    path = Path(data_dir) / "llm_metrics.jsonl"
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 256 * 1024))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        label = str(row.get("label") or "") if isinstance(row, dict) else ""
        if re.search(rf"(?:^|_){re.escape(node_key)}$", label):
            return row
    return None


# ---------- причина ----------


def classify(
    *,
    errors: list[str],
    rejects: list[dict[str, Any]],
    frames: int | None,
    is_check: bool,
) -> dict[str, Any]:
    """Причина правилами. Порядок: вход, провайдер, обрыв, отказ, поля, пустой ответ, прочая схема."""
    reject_err = "\n".join(str(r.get("error") or "") for r in rejects)
    replies = [str(r.get("reply") or "") for r in rejects]
    all_err = "\n".join([*errors, reject_err])
    found: dict[str, Any] = {}
    if frames == 0 or (_EMPTY_INPUT.search(all_err) and not rejects):
        code = "empty_input"
        explain = "Модели нечего было обрабатывать: во входной таблице ноды нет ни одного кадра."
    elif _PROVIDER.search(all_err) and not rejects:
        code = "provider_error"
        explain = "Запрос не дошёл до ответа модели: провайдер вернул сбой, таймаут или кончилась квота."
    elif _TRUNC.search(all_err) or any(
        r.get("kind") == "parse" and _looks_cut(rep) for r, rep in zip(rejects, replies, strict=False)
    ):
        code = "truncated"
        explain = "Ответ модели оборвался на середине JSON: ответ не влез в лимит длины модели."
    elif (
        rejects
        and all(_REFUSAL.search(rep) and "{" not in rep for rep in replies if rep.strip())
        and any(rep.strip() for rep in replies)
    ):
        code = "refusal"
        explain = "Вместо данных модель ответила текстом отказа."
    elif _UNKNOWN_FIELDS.search(all_err):
        names: list[str] = []
        for hit in _UNKNOWN_FIELDS.finditer(all_err):
            names += [n.strip(" '\"") for n in hit.group(1).split(",") if n.strip(" '\"")]
        names = list(dict.fromkeys(names))[:10]
        found["unknown_fields"] = names
        code = "unknown_fields"
        explain = (
            "Модель записала поля, которых нет в базе кадров: "
            + ", ".join(f"«{n}»" for n in names)
            + ". Это ошибка модели в именах полей, сами данные она вернула."
        )
    elif _EMPTY_OPS.search(all_err) or (rejects and all(_ops_count(rep) == 0 for rep in replies)):
        code = "empty_ops"
        if is_check:
            explain = (
                "Модель вернула пустой список правок. Нода — проверка: «править нечего» для неё "
                "законный ответ, а контракт записи требует хотя бы одну правку — это несовпадение "
                "правил ноды, а не обязательно ошибка модели."
            )
        else:
            explain = (
                "Модель ответила, но не вернула ни одной правки кадров: по промту ей было неясно, "
                "что писать, или она решила, что всё уже заполнено."
            )
        found["check_node"] = is_check
    elif _PROVIDER.search(all_err):
        code = "provider_error"
        explain = "В ошибке сбой провайдера модели: таймаут, 5xx или квота."
    elif rejects:
        code = "schema_other"
        explain = "Ответ модели не прошёл проверку формата записи."
    else:
        code = "unknown"
        explain = "Отклонённых ответов модели и ошибок пачек на диске нет — по данным причина не видна."
    return {"code": code, "title_ru": CAUSE_RU[code], "explain_ru": explain, **found}


def _looks_cut(reply: str) -> bool:
    raw = (reply or "").rstrip()
    return bool(raw) and raw.count("{") > raw.count("}")


def fix_options(
    cause: dict[str, Any],
    *,
    project_id: int,
    node_key: str,
    step_code: str,
    upstream: str | None = None,
) -> list[dict[str, Any]]:
    """Что можно сделать существующими действиями. Платное и правка промта — через карточку человека."""
    code = cause.get("code")
    pid, key = int(project_id), node_key
    run_params: dict[str, Any] = {"project_id": pid, "step_code": step_code}
    if step_code == "excel_gpt":
        run_params["node_key"] = key
    retry = {
        "action": "step.run_step",
        "params": run_params,
        "title_ru": "Повторить запуск ноды",
        "phrase_ru": f"перезапусти ноду {key} в проекте {pid}",
    }
    models = {
        "action": "node.set_model",
        "params": {"project_id": pid, "node_key": key},
        "title_ru": "Поставить другую модель не дороже текущей (сначала список моделей)",
        "phrase_ru": f"покажи, на какие модели можно поменять ноду {key} в проекте {pid}",
    }
    variant = {
        "action": "node.set_prompt_variant",
        "params": {"project_id": pid, "node_key": key},
        "title_ru": "Выбрать другой вариант промта из библиотеки",
        "phrase_ru": f"какие варианты промта есть у ноды {key} в проекте {pid}",
    }

    def prompt_edit(append: str, why: str) -> dict[str, Any]:
        return {
            "action": "prompt.edit_text",
            "params": {"project_id": pid, "node_key": key, "rationale": why, "append": append},
            "title_ru": "Предложить правку промта ноды (применяет человек кнопкой)",
            "phrase_ru": f"допиши в промт ноды {key} проекта {pid}: {append}",
        }

    if code == "empty_input":
        out = []
        if upstream:
            out.append(
                {
                    "action": "node.output",
                    "params": {"project_id": pid, "node_key": upstream},
                    "title_ru": f"Проверить ноду-источник {upstream}: что она отдала",
                    "phrase_ru": f"что выдала нода {upstream} в проекте {pid}",
                }
            )
        out.append(dict(retry, title_ru="Перезапустить ноду, когда у источника появятся кадры"))
        return out
    if code == "empty_ops":
        if cause.get("check_node"):
            return [
                {
                    "action": None,
                    "params": {},
                    "title_ru": "Пустой ответ проверки должен приниматься как «правок нет» — "
                    "это правка правил записи в коде, чат её не делает",
                    "phrase_ru": "",
                },
                retry,
            ]
        return [
            prompt_edit(
                "Верни непустой ops: для каждого кадра из db_frames.json хотя бы одну правку поля; "
                "если менять нечего, всё равно перепиши текущее значение поля.",
                "модель вернула пустой список правок",
            ),
            models,
            retry,
        ]
    if code == "unknown_fields":
        names = ", ".join(f"«{n}»" for n in cause.get("unknown_fields") or []) or "из ошибки"
        return [
            prompt_edit(
                f"Пиши только канонические имена полей кадра; полей {names} нет — используй "
                "существующие (например shot01_description для описания).",
                "модель записала неизвестные поля",
            ),
            retry,
            variant,
        ]
    if code == "truncated":
        return [
            models,
            prompt_edit(
                "Отвечай коротко: только JSON с ops, без пояснений; report — одна фраза.",
                "ответ модели обрывается по длине",
            ),
            retry,
        ]
    if code == "refusal":
        return [models, variant, retry]
    if code == "provider_error":
        return [dict(retry, title_ru="Повторить позже: сбой был у провайдера"), models]
    if code == "schema_other":
        return [
            prompt_edit(
                'Верни ровно один JSON-объект по контракту записи: {"ops": [...], "report": "..."}, '
                "без markdown и лишних полей.",
                "ответ модели не прошёл проверку формата",
            ),
            retry,
            models,
        ]
    return [
        {
            "action": "logs.tail",
            "params": {"project_id": pid, "kind": "errors"},
            "title_ru": "Посмотреть ошибки сервера по проекту",
            "phrase_ru": f"покажи ошибки в логах по проекту {pid}",
        },
        retry,
    ]


# ---------- выбор ноды ----------


def _when(nr: dict) -> float:
    return max(_ts_of(nr.get("finished_at")), _ts_of(nr.get("started_at")))


def pick_node(meta: dict, node_runs: list[dict], data_dir: Path | str | None) -> tuple[str | None, str]:
    """Упавшая нода без явного ключа: запуск со статусом failed, ошибка шага, дневник, отказы."""
    failed = [nr for nr in node_runs if str(nr.get("status")) == "failed" and nr.get("node_key")]
    if failed:
        return str(max(failed, key=_when)["node_key"]), "последний запуск с ошибкой"
    fs = bi._step_failure(meta)
    if fs and fs.get("error"):
        active = meta.get("active_excel_gpt_node_key")
        if active:
            return str(active), "активная нода при ошибке шага"
        status = str(fs.get("status") or "")
        try:
            from app.orchestrator.node_registry import NODE_TYPE_TO_RUNNING

            types = {s.value: t for t, s in NODE_TYPE_TO_RUNNING.items()}
        except Exception:
            types = {}
        typ = types.get(status)
        for node in bi._canvas_nodes(meta):
            if typ and bi._node_types(node)[1] == typ:
                return str(node["id"]), "нода упавшего шага"
    if data_dir is not None:
        try:
            from app.services.node_trace import list_runs

            bad = [r for r in list_runs(data_dir) if r.get("status") == "failed"]
            if bad:
                return str(bad[-1]["node_key"]), "последний прогон с ошибкой в дневнике"
        except Exception:
            pass
        folder = Path(data_dir) / "llm_rejects"
        try:
            files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        except OSError:
            files = []
        for path in reversed(files):
            hit = _KEY_IN_LABEL.search(path.name)
            if hit:
                return hit.group(1), "последний отклонённый ответ модели"
    return None, ""


def _is_check(meta: dict, key: str) -> bool:
    try:
        from app.services.node_groups import is_script_frames_qc_gpt_node

        if is_script_frames_qc_gpt_node(meta, node_key=key):
            return True
    except Exception:
        pass
    try:
        from app.services.gpt_operator import is_check_operator
        from app.services.node_config import excel_gpt_config_for_node

        return bool(is_check_operator(excel_gpt_config_for_node(meta, key)))
    except Exception:
        return False


def _step_failure_is_ours(meta: dict, fs: dict, key: str, eff: str) -> bool:
    """Ошибка шага проекта относится к этой ноде: иначе чужая ошибка выдаётся за причину."""
    if str(meta.get("active_excel_gpt_node_key") or "") == key:
        return True
    try:
        from app.orchestrator.node_registry import NODE_TYPE_TO_RUNNING

        running = NODE_TYPE_TO_RUNNING.get(eff)
    except Exception:
        return False
    return running is not None and running.value == str(fs.get("last_running") or "")


def _upstream(meta: dict, key: str) -> str | None:
    graph = meta.get("canvas_graph") if isinstance(meta.get("canvas_graph"), dict) else {}
    for edge in graph.get("edges") or []:
        if isinstance(edge, dict) and str(edge.get("target")) == key and edge.get("source"):
            return str(edge["source"])
    return None


# ---------- действие ----------


def _reject_lines(rejects: list[dict[str, Any]]) -> list[str]:
    lines = []
    for r in rejects:
        reply = str(r.get("reply") or "").strip()
        part = f"Попытка {r.get('attempt') or '?'} ({r.get('kind') or '?'})"
        if r.get("model"):
            part += f", модель {r['model']}"
        part += f": {bi._clip(r.get('error'), 500) or 'ошибка не записана'}."
        part += f" Ответ модели: «{bi._clip(reply, _REPLY_CHARS)}»." if reply else " Ответ модели пустой."
        if r.get("report"):
            part += f" Модель сама пишет в report: «{r['report']}»."
        lines.append(part)
    return lines


async def _diag_node_failure(ctx: ActionContext, params: dict) -> dict:
    pid = int(params["project_id"])
    detail = await _effective_project(ctx, pid)
    meta = bi._meta(detail)
    project = bi._transient(pid, detail, await bi._project_row(pid))
    try:
        data_dir: Path | None = Path(project.data_dir)
    except Exception:
        data_dir = None
    runs = await bi._node_runs(ctx, pid)
    key = str(params.get("node_key") or "").strip() or None
    how = "названа в просьбе"
    if key is None:
        key, how = pick_node(meta, runs, data_dir)
    if key is None:
        return {
            "project_id": pid,
            "found": False,
            "text_ru": f"В проекте #{pid} нет упавших нод: ни запуска с ошибкой, ни ошибки шага, "
            "ни отклонённых ответов модели на диске.",
        }
    nodes = {str(n["id"]): n for n in bi._canvas_nodes(meta)}
    node = nodes.get(key)
    nr = next((r for r in runs if str(r.get("node_key")) == key), None)
    raw, eff = bi._node_types(node, str((nr or {}).get("node_type") or ""))
    step = bi._step_code(raw, eff)
    from app.services.excel_gpt_node import EXCEL_GPT_STEP_CODE, is_excel_gpt_node_type

    if is_excel_gpt_node_type(raw):
        step = EXCEL_GPT_STEP_CODE
    title = bi._labels(meta).get(key) or bi._node_title(node, eff)
    status = bi._NODE_STATUS_RU.get(
        str((nr or {}).get("status")), str((nr or {}).get("status") or "не запускалась")
    )
    errors: list[str] = []
    if nr and nr.get("error"):
        errors.append(bi._clip(nr["error"], _ERROR_CHARS))
    fs_raw = (
        meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else meta.get("failure_state")
    )
    if (
        isinstance(fs_raw, dict)
        and fs_raw.get("last_error")
        and _step_failure_is_ours(meta, fs_raw, key, eff)
    ):
        text = bi._clip(fs_raw["last_error"], _ERROR_CHARS)
        if text not in errors:
            errors.append(text)
    trace = trace_failure(data_dir, key) if data_dir is not None else None
    if trace and trace.get("status") == "failed" and trace.get("error") and trace["error"] not in errors:
        errors.append(trace["error"])
    rejects = read_rejects(data_dir, key) if data_dir is not None else []
    size = input_size(data_dir, key) if data_dir is not None else None
    sent = next(
        (r["input_frames"] for r in reversed(rejects) if isinstance(r.get("input_frames"), int)), None
    )
    if size is None and sent is not None:
        # Файла входа нет (не «Работа с GPT»), но отказ записал, сколько кадров ушло модели.
        size = {"frames": sent, "batches": 0, "file": "llm_rejects"}
    metrics = metrics_line(data_dir, key) if data_dir is not None else None
    is_check = _is_check(meta, key)
    cause = classify(errors=errors, rejects=rejects, frames=(size or {}).get("frames"), is_check=is_check)
    fixes = fix_options(cause, project_id=pid, node_key=key, step_code=step, upstream=_upstream(meta, key))
    model = bi._model_for(meta, key, node, raw, eff) or None
    prompt = bi._prompt_for(detail, key, raw, eff)

    lines = [f"Нода «{title}» ({key}), шаг {bi._step_ru(step)}: {status}."]
    if how != "названа в просьбе":
        lines.append(f"Нода выбрана сама: {how}.")
    if nr:
        lines.append(f"Попыток запуска: {int(nr.get('attempts') or 0)}.")
    if model:
        lines.append(f"Модель: {model}.")
    if prompt:
        lines.append(f"Промт: {prompt['variant']} ({prompt['source']}).")
    if is_check:
        lines.append("Это нода проверки.")
    if size:
        lines.append(
            f"На входе кадров: {size['frames']}"
            + (f", пачек: {size['batches']}." if size["batches"] else ".")
        )
    if metrics:
        lines.append(
            f"Ответов модели в последнем прогоне: {metrics.get('attempts')}, отклонено: "
            f"{int(metrics.get('parse_fails') or 0) + int(metrics.get('validate_fails') or 0)}, "
            f"итог {'принят' if metrics.get('ok') else 'не принят'}."
        )
    if errors:
        lines.append("Ошибка: " + " | ".join(errors[:2]))
    lines += _reject_lines(rejects)
    if trace and trace.get("batch_error"):
        lines.append(f"Ошибка пачки в дневнике: {trace['batch_error']}")
    for reason in (trace or {}).get("skipped") or []:
        lines.append(f"Пачка не записана: {reason}")
    lines.append(f"Причина: {cause['title_ru']}. {cause['explain_ru']}")
    usable = [f for f in fixes if f.get("phrase_ru")]
    if fixes:
        lines.append(
            "Что можно сделать: "
            + "; ".join(
                f"{f['title_ru']}" + (f" — «{f['phrase_ru']}»" if f.get("phrase_ru") else "") for f in fixes
            )
            + "."
        )
    out = {
        "project_id": pid,
        "found": True,
        "node_key": key,
        "picked": how,
        "title": title,
        "step": step,
        "status_ru": status,
        "attempts": int((nr or {}).get("attempts") or 0),
        "model": model,
        "prompt": prompt,
        "check_node": is_check,
        "errors": errors,
        "rejects": [
            {
                k: (bi._clip(v, _REPLY_CHARS) if k == "reply" else v)
                for k, v in r.items()
                if not k.startswith("_")
            }
            for r in rejects
        ],
        "input": size,
        "metrics": metrics,
        "trace": trace,
        "cause": cause,
        "fix_options": fixes,
        "suggested_phrase": usable[0]["phrase_ru"] if usable else None,
        "text_ru": " ".join(lines),
    }
    return bi._safe(out)


def node_failure_spec() -> ActionSpec:
    return ActionSpec(
        id=ACTION_ID,
        title_ru="Почему упала нода",
        description_ru="Разбор упавшей ноды до причины: статус, попытки, модель, вариант промта, "
        "полный текст ошибки, отклонённые ответы модели (что она вернула и что написала в "
        "report), сколько кадров было на входе, ошибка пачки из дневника. Причина правилами: "
        "пустой вход, пустой список правок, неизвестные поля, оборванный ответ, отказ, сбой "
        "провайдера, прочий формат; и варианты исправления существующими действиями. Без "
        "node_key — сама берёт упавшую ноду проекта. Только чтение.",
        params_schema=bi._schema({"project_id": _PID, "node_key": _NODE_KEY}, ["project_id"]),
        executor=_diag_node_failure,
        risk=RISK_READ,
        idempotent=True,
        preconditions=(_PRE_PROJECT,),
        tags=(
            "diag",
            "node",
            "failure",
            "причина",
            "почему",
            "упала",
            "сломалась",
            "ошибка",
            "разберись",
            "найди",
            "нода",
        ),
    )
