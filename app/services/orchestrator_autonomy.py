"""Автономность оркестратора при сбое ноды: настройки и чистое правило «что можно без карточки».

Три переключателя, у каждого два режима: «с подтверждением» (по умолчанию, False) и
«автономно» (True). Деньги тратятся только с явного согласия владельца студии:

- ``auto_fix_on_failure`` — после разбора сбоя (он пишется в чат всегда) оркестратор сам
  начинает чинить; выключен — разбор и карточка с предложением, без исполнения;
- ``auto_retry`` — сам перезапускает упавшую ноду (новый платный вызов модели, текст ошибки
  уходит модели), не больше ``max_auto_retries_per_node`` раз подряд на ноду;
- ``auto_model_switch`` — сам ставит на ноду другую модель, только если она не дороже
  текущей; дороже или цена неизвестна — карточка.

Потолок ``auto_spend_limit_usd`` — на один автозапуск: оценка выше или неизвестна — карточка.
Бесплатная однозначная правка данных по находке проверки содержания (``content_checks``:
закадр ячейки из voiceover.txt, канон героя) — без карточки при ``auto_fix_on_failure``.
Правка промтов, удаление, мастер-промты и всё прочее — всегда карточка.

Хранение — ``data/orchestrator_autonomy.json``, свой раздел у каждого арендатора (как
``sidebar_layout.json``). Автозапуск идёт от имени человека, который включил режим
(``updated_by``): без этой отметки автономности нет, даже если флаги стоят в файле.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings

SWITCHES = ("auto_retry", "auto_model_switch", "auto_fix_on_failure")
DEFAULTS: dict[str, Any] = {
    "auto_retry": False,
    "auto_model_switch": False,
    "auto_fix_on_failure": False,
    "max_auto_retries_per_node": 2,
    "auto_spend_limit_usd": 0.5,
}
MAX_RETRIES_CAP = 5
SPEND_LIMIT_CAP_USD = 20.0
OWNER = "_owner"

# Правки данных, правильное значение которых известно из самого проекта (закадр ячейки
# из voiceover.txt, канон героя из паспорта — ``content_checks``): бесплатны и однозначны.
# Без карточки — только с отметкой ``deterministic`` и при включённом автоисправлении.
DATA_FIX_ACTIONS = frozenset({"shot.edit_cell_voiceover", "shot.edit_field"})
# Только эти действия бывают без карточки; всё прочее из разбора — кнопка с подтверждением.
AUTO_ACTIONS = frozenset({"step.run_step", "node.set_model", *DATA_FIX_ACTIONS})
# Повтор с той же причиной даст тот же ответ: на входе нет кадров.
NO_AUTO_RETRY_CAUSES = frozenset({"empty_input", "budget_cap"})

_LOCK = threading.Lock()


# ---------- хранение ----------


def _path() -> Path:
    path = Path(settings.data_dir) / "orchestrator_autonomy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _tenant_key(tenant: str | None = None) -> str:
    if tenant is None:
        from app.services.tenant import current_tenant

        tenant = current_tenant()
    return str(tenant) if tenant else OWNER


def _load_all() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator_autonomy: read failed: {}", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def normalize(raw: Any) -> dict[str, Any]:
    """Сырые значения из файла → полная настройка; мусор и выход за пределы — по умолчанию."""
    raw = raw if isinstance(raw, dict) else {}
    # Включает только настоящий true: строка "false" или 1 из файла не делает режим автономным.
    out: dict[str, Any] = {k: raw.get(k) is True for k in SWITCHES}
    retries = raw.get("max_auto_retries_per_node", DEFAULTS["max_auto_retries_per_node"])
    out["max_auto_retries_per_node"] = (
        retries
        if isinstance(retries, int) and not isinstance(retries, bool) and 0 <= retries <= MAX_RETRIES_CAP
        else DEFAULTS["max_auto_retries_per_node"]
    )
    limit = raw.get("auto_spend_limit_usd", DEFAULTS["auto_spend_limit_usd"])
    ok = isinstance(limit, (int, float)) and not isinstance(limit, bool) and 0 <= limit <= SPEND_LIMIT_CAP_USD
    out["auto_spend_limit_usd"] = round(float(limit), 4) if ok else DEFAULTS["auto_spend_limit_usd"]
    out["updated_by"] = str(raw.get("updated_by") or "")[:160]
    out["updated_at"] = str(raw.get("updated_at") or "") or None
    return out


def load(tenant: str | None = None) -> dict[str, Any]:
    with _LOCK:
        data = _load_all()
    return normalize((data.get("settings") or {}).get(_tenant_key(tenant)))


def save(changes: dict[str, Any], *, actor: str, tenant: str | None = None) -> dict[str, Any]:
    """Частичная правка: неназванные поля остаются как были. ``actor`` — человек из запроса."""
    key = _tenant_key(tenant)
    with _LOCK:
        data = _load_all()
        by_tenant = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        cur = normalize(by_tenant.get(key))
        cur.update({k: v for k, v in changes.items() if k in DEFAULTS})
        cur["updated_by"] = str(actor or "")[:160]
        cur["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        fresh = normalize(cur)
        by_tenant[key] = fresh
        data["settings"] = by_tenant
        _save_all(data)
    logger.info(
        "orchestrator_autonomy: {} → {}",
        key,
        {k: fresh[k] for k in DEFAULTS},
    )
    return fresh


def public_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ответ API: значения, режимы словами и пределы для формы."""
    return {
        **{k: cfg[k] for k in DEFAULTS},
        "modes": {k: ("автономно" if cfg[k] else "с подтверждением") for k in SWITCHES},
        "updated_by": cfg.get("updated_by") or None,
        "updated_at": cfg.get("updated_at"),
        "defaults": dict(DEFAULTS),
        "limits": {
            "max_auto_retries_per_node": [0, MAX_RETRIES_CAP],
            "auto_spend_limit_usd": [0, SPEND_LIMIT_CAP_USD],
        },
    }


# ---------- счётчик автоповторов ноды ----------


def _retry_key(project_id: int, node_key: str) -> str:
    return f"{int(project_id)}:{node_key}"


# Счётчик «подряд»: успех ноды без нас (кнопкой человека) мы не видим, поэтому старая
# серия через полсуток не держит запрет на автоповтор.
RETRIES_TTL_S = 12 * 3600


def retries_done(project_id: int, node_key: str, tenant: str | None = None) -> int:
    with _LOCK:
        data = _load_all()
    got = ((data.get("retries") or {}).get(_tenant_key(tenant)) or {}).get(_retry_key(project_id, node_key))
    if not isinstance(got, dict) or not isinstance(got.get("n"), int):
        return 0
    if time.time() - float(got.get("at") or 0) > RETRIES_TTL_S:
        return 0
    return max(0, int(got["n"]))


def set_retries(project_id: int, node_key: str, value: int, tenant: str | None = None) -> None:
    """Сколько автоповторов уже было подряд; 0 после подтверждённого успеха ноды."""
    key = _tenant_key(tenant)
    with _LOCK:
        data = _load_all()
        all_r = data.get("retries") if isinstance(data.get("retries"), dict) else {}
        mine = dict(all_r.get(key) or {})
        if value > 0:
            mine[_retry_key(project_id, node_key)] = {"n": int(value), "at": time.time()}
        else:
            mine.pop(_retry_key(project_id, node_key), None)
        all_r[key] = mine
        data["retries"] = all_r
        _save_all(data)


# ---------- текст ошибки для модели при автоповторе ----------

FEEDBACK_FILE = "orchestrator_retry_feedback.txt"
# Файл, оставшийся от упавшего процесса, не должен попасть в запуск человека через час-другой.
FEEDBACK_TTL_S = 2 * 3600
FEEDBACK_CHARS = 3000


def feedback_path(project_dir: Path | str, node_key: str) -> Path:
    from app.services.excel_gpt_node import safe_upload_node_key

    return Path(project_dir) / "excel_gpt_uploads" / safe_upload_node_key(node_key) / FEEDBACK_FILE


def write_feedback(project_dir: Path | str, node_key: str, text: str) -> Path | None:
    path = feedback_path(project_dir, node_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or "")[:FEEDBACK_CHARS], encoding="utf-8")
    except OSError as exc:
        logger.warning("orchestrator_autonomy: feedback not written: {}", exc)
        return None
    return path


def read_feedback(project_dir: Path | str, node_key: str) -> str:
    """Текст ошибки прошлого запуска для промта ноды; пусто — автоповтора нет."""
    path = feedback_path(project_dir, node_key)
    try:
        if not path.is_file():
            return ""
        if time.time() - path.stat().st_mtime > FEEDBACK_TTL_S:
            path.unlink(missing_ok=True)
            return ""
        return path.read_text(encoding="utf-8").strip()[:FEEDBACK_CHARS]
    except OSError:
        return ""


def clear_feedback(project_dir: Path | str, node_key: str) -> None:
    try:
        feedback_path(project_dir, node_key).unlink(missing_ok=True)
    except OSError:
        pass


# ---------- правило: что можно без карточки ----------


@dataclass(frozen=True)
class FixStep:
    action: str
    params: dict
    title_ru: str
    auto: bool
    why_ru: str = ""


@dataclass(frozen=True)
class FixDecision:
    steps: list[FixStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def auto_steps(self) -> list[FixStep]:
        return [s for s in self.steps if s.auto]

    @property
    def card_steps(self) -> list[FixStep]:
        return [s for s in self.steps if not s.auto]


def price_not_higher(current: Any, new: Any) -> bool | None:
    """Новая цена не выше текущей: True/False; None — хоть одна цена неизвестна.

    Цена — число или кортеж (вход, выход за 1 млн токенов): не выше по каждой точке,
    как ``node_edit.compare_cost``.
    """
    if current is None or new is None:
        return None
    a = tuple(current) if isinstance(current, (list, tuple)) else (current,)
    b = tuple(new) if isinstance(new, (list, tuple)) else (new,)
    if not a or len(a) != len(b):
        return None
    try:
        return all(float(y) <= float(x) for x, y in zip(a, b, strict=True))
    except (TypeError, ValueError):
        return None


_READ_ONLY_FIXES = frozenset(
    {"node.output", "logs.tail", "diag.node_failure", "diag.incidents", "diag.content_checks"}
)

def _model_label(model_id: str) -> str:
    """Название модели из каталога для карточки (а не её код)."""
    try:
        from app.services.node_edit import find_catalog_model

        found = find_catalog_model(model_id)
    except Exception:  # noqa: BLE001
        found = None
    label = str(found.get("label") or "").strip() if isinstance(found, dict) else ""
    return label or model_id


def decide_fix(
    *,
    cause: dict | str,
    fix_options: list[dict],
    cfg: dict[str, Any],
    retries_done: int,
    current_price: Any = None,
    new_price: Any = None,
    new_model_id: str | None = None,
    retry_cost_usd: float | None = None,
    blocked_reason: str = "",
) -> FixDecision:
    """Чистая функция: шаги исправления и для каждого — можно ли без карточки.

    ``fix_options`` — из ``diag.node_failure``. Смена модели идёт раньше повтора: повтор
    должен пойти уже на новой модели. ``blocked_reason`` (кончились деньги у провайдера,
    бюджет прогона) снимает автономность целиком: повтор тут не поможет, а деньги сожжёт.
    """
    cfg = normalize(cfg)
    code = cause.get("code") if isinstance(cause, dict) else str(cause or "")
    check_node = bool(cause.get("check_node")) if isinstance(cause, dict) else False
    fix_on = cfg["auto_fix_on_failure"] and not blocked_reason
    notes: list[str] = []
    if blocked_reason:
        notes.append(f"Сам не чиню: {blocked_reason}.")
    elif not cfg["auto_fix_on_failure"]:
        notes.append("Автоисправление выключено: жду подтверждения.")
    if fix_on and not cfg.get("updated_by"):
        fix_on = False
        notes.append("Неизвестно, кто включил автономный режим: жду подтверждения.")
    limit = float(cfg["auto_spend_limit_usd"])
    models: list[FixStep] = []
    runs: list[FixStep] = []
    upstream: list[FixStep] = []
    others: list[FixStep] = []
    for opt in fix_options or []:
        action = opt.get("action")
        params = dict(opt.get("params") or {})
        title = str(opt.get("title_ru") or action or "")
        if not action:
            notes.append(title)
            continue
        if action == "node.set_model":
            if not new_model_id:
                models.append(
                    FixStep(
                        "node.list_models",
                        params,
                        "Показать модели не дороже текущей",
                        False,
                        "подходящей модели не дороже текущей не нашлось",
                    )
                )
                continue
            params["model_id"] = new_model_id
            cheaper = price_not_higher(current_price, new_price)
            why = ""
            if cheaper is None:
                why = "цена текущей или новой модели неизвестна"
            elif not cheaper:
                why = "новая модель дороже текущей"
            elif not cfg["auto_model_switch"]:
                why = "автосмена модели выключена"
            elif not fix_on:
                why = "автоисправление выключено"
            name = "node.set_model" if cheaper else "node.set_model_costly"
            models.append(FixStep(name, params, f"Поставить модель «{_model_label(new_model_id)}»", not why, why))
            continue
        if action == "step.run_step" and opt.get("upstream"):
            upstream.append(FixStep(action, params, title, False, "это другой платный шаг, не повтор: только с подтверждением"))
            continue
        if action == "step.run_step":
            why = ""
            if not cfg["auto_retry"]:
                why = "автоповтор выключен"
            elif code in NO_AUTO_RETRY_CAUSES or (code == "empty_ops" and check_node):
                why = "повтор с той же причиной даст тот же результат"
            elif retries_done >= cfg["max_auto_retries_per_node"]:
                why = f"автоповторов уже {retries_done} из {cfg['max_auto_retries_per_node']}"
            elif retry_cost_usd is None:
                why = "стоимость повтора неизвестна"
            elif float(retry_cost_usd) > limit:
                why = f"повтор до ${float(retry_cost_usd):.2f}, выше потолка ${limit:.2f}"
            elif not fix_on:
                why = "автоисправление выключено"
            runs.append(FixStep(action, params, title, not why, why))
            continue
        if action in DATA_FIX_ACTIONS and opt.get("deterministic"):
            why = "" if fix_on else (blocked_reason or "автоисправление выключено")
            others.append(FixStep(action, params, title, not why, why))
            continue
        # Правка промта, вариант промта, мастер-промты, удаление — всегда через человека.
        # Чтение (node.output, logs.tail) ничего не меняет: так и пишем, а не «меняет промт».
        why = (
            "это проверка, по кнопке"
            if action in _READ_ONLY_FIXES
            else "меняет промт или данные: только с подтверждением"
        )
        others.append(FixStep(action, params, title, False, why))
    steps = [*models[:1], *upstream, *runs[:1], *others]
    # Страховка от будущих правок правила: вне белого списка без карточки не идёт ничего.
    steps = [
        s if s.action in AUTO_ACTIONS or not s.auto else FixStep(s.action, s.params, s.title_ru, False)
        for s in steps
    ]
    return FixDecision(steps=steps, notes=notes)
