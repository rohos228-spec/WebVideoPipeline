from __future__ import annotations

import math
import re
from typing import Any

from app.orchestrator.contracts import PlanStep

PLAN_CARD_REASONS = frozenset(
    {
        "plan_sanity",
        "critic_reject",
        "critic_escalation",
        "unknown_cost",
        "steer_not_approved",
        "risk",
        "paid",
        "approval_required",
    }
)
_RISK_ORDER = ("none", "low", "medium", "high")
_UNKNOWN_TITLE = "Действие вне каталога студии"
_ACTION_ID = re.compile(r"(?<![\w.])[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?![\w.])")
UNKNOWN_COST_TEXT = "неизвестна (зависит от числа кадров и тарифа провайдера)"
_SOFT_TITLES = {
    "montage.delete_frame": "Мягко скрыть кадр",
    "montage.delete_child_frame": "Мягко скрыть дочерний кадр",
}


def _spec(action: str) -> Any:
    try:
        from app.services.action_layer.spec import ensure_builtin_actions, registry

        ensure_builtin_actions()
        return registry.find(str(action))
    except Exception:
        return None


_NODE_TITLES = {
    "node.set_model": "Сменить модель ноды «{node}» на {model}",
    "node.set_model_costly": "Сменить модель ноды «{node}» на {model} (дороже или цена неизвестна)",
    "node.set_prompt_variant": "Поставить ноде «{node}» промт {variant}",
    "node.describe": "Показать настройки ноды «{node}»",
    "node.list_models": "Показать, какие модели можно поставить на ноду «{node}»",
    "node.revert_model": "Вернуть прежнюю модель ноды «{node}»",
    "node.set_params": "Изменить настройки ноды «{node}» (без роста расходов)",
    "node.set_params_costly": "Изменить настройки ноды «{node}» с подтверждением",
    "node.revert_params": "Вернуть прежние настройки ноды «{node}»",
    "prompt.edit_text": "Предложить правку промта ноды «{node}» (этот проект)",
}
_ID_KEYS = ("node_key", "model_id", "variant")


def _text_value(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return " ".join(value.split()) if isinstance(value, str) else ""


def model_title(model_id: str) -> str:
    if not model_id:
        return ""
    try:
        from app.services.node_edit import find_catalog_model

        found = find_catalog_model(model_id)
    except Exception:
        found = None
    label = str(found.get("label") or "").strip() if isinstance(found, dict) else ""
    return label or model_id


def node_title(action: str, params: dict[str, Any], labels: dict[str, str] | None) -> str:
    pattern = _NODE_TITLES.get(action)
    key = _text_value(params, "node_key")
    label = str((labels or {}).get(key) or "").strip()
    if not pattern or not label:
        return ""
    model = model_title(_text_value(params, "model_id"))
    if "{model}" in pattern and not model:
        return ""
    variant = variant_title(_text_value(params, "variant"))
    if "{variant}" in pattern and not variant:
        return ""
    return pattern.format(node=label, model=model, variant=variant)


def variant_title(name: str) -> str:
    """«Название» промта из описания библиотеки; без описания — имя файла."""
    if not name:
        return ""
    try:
        from app.services import prompt_library as pl

        info = pl.load_file_meta(pl.EXCEL_GPT_UNIFIED_STEP).get(name)
    except Exception:
        info = None
    title = str(info.get("title") or "").strip() if isinstance(info, dict) else ""
    return f"«{title or name}»"


def _params_text(params: dict[str, Any], labels: dict[str, str] | None = None, named: bool = False) -> str:
    shown = dict(params)
    if named:
        for key in _ID_KEYS:
            shown.pop(key, None)
    else:
        key = _text_value(shown, "node_key")
        label = str((labels or {}).get(key) or "").strip()
        if key and label:
            shown["node_key"] = f"«{label}»"
        model_id = _text_value(shown, "model_id")
        if model_id:
            shown["model_id"] = model_title(model_id)
    try:
        from app.services.action_layer.builtin_insight import _STEP_RU
        from app.services.action_layer.planner_rules import param_parts_ru

        code = shown.get("step_code")
        if isinstance(code, str) and code in _STEP_RU:
            shown["step_code"] = _STEP_RU[code]
        parts = param_parts_ru(shown)
    except Exception:
        parts = []
    pid = params.get("project_id")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        parts.append(f"проект #{pid}")
    return "; ".join(parts)


def humanize_reason(text: str) -> str:
    from app.services.plain_ru import plain_ru

    def _title(match: Any) -> str:
        spec = _spec(match.group(0))
        title = str(getattr(spec, "title_ru", "") or "").strip()
        return f"«{title}»" if title else match.group(0)

    out = _ACTION_ID.sub(_title, str(text or ""))
    return " ".join(plain_ru(out).split())


def _risk(actions: Any, step: PlanStep) -> str:
    from app.orchestrator.agents.executor import effective_risk

    try:
        return effective_risk(actions, step).value
    except Exception:
        return str(step.risk or "high")


def _paid(actions: Any, step: PlanStep, spec: Any) -> bool:
    from app.orchestrator.agents.executor import step_is_paid

    if step.paid or bool(getattr(spec, "spends_money", False)):
        return True
    try:
        return step_is_paid(actions, step)
    except Exception:
        return False


def step_view(step: PlanStep, actions: Any, labels: dict[str, str] | None = None) -> dict[str, Any]:
    spec = _spec(step.action)
    named = node_title(step.action, dict(step.params), labels) if spec is not None else ""
    title = (
        named
        or _SOFT_TITLES.get(step.action)
        or str(getattr(spec, "title_ru", "") or "").strip()
        or _UNKNOWN_TITLE
    )
    params_text = _params_text(dict(step.params), labels, bool(named))
    paid = _paid(actions, step, spec)
    risk = _risk(actions, step)
    if paid and risk in {"none", "low"}:
        risk = "medium"
    return {
        "title": title,
        "action": step.action,
        "params_text": params_text,
        "paid": paid,
        "risk": risk,
        "text": f"{title} — {params_text}" if params_text else title,
    }


def plan_steps(
    plan: list[PlanStep], actions: Any, labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    return [step_view(step, actions, labels) for step in plan]


def plan_node_keys(plan: list[PlanStep]) -> list[str]:
    keys = [_text_value(dict(step.params), "node_key") for step in plan]
    return list(dict.fromkeys(key for key in keys if key))


def plan_project_id(plan: list[PlanStep]) -> int | None:
    for step in plan:
        pid = step.params.get("project_id")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return pid
    return None


def node_labels(meta: Any, keys: list[str]) -> dict[str, str]:
    if not keys or not isinstance(meta, dict):
        return {}
    from app.orchestrator.agents.project_nodes import roster

    try:
        rows = roster(meta)
    except Exception:
        return {}
    names = [str(row.get("label") or row.get("type_ru") or "").strip() for row in rows]
    out: dict[str, str] = {}
    for row, label in zip(rows, names, strict=True):
        key = str(row.get("key") or "")
        if key in keys and label:
            number = "".join(ch for ch in key if ch.isdigit())
            # Две ноды «Работа с GPT» в карточке различаются номером из ключа.
            out[key] = f"{label} {number}" if number and names.count(label) > 1 else label
    return out


def _step_usd(step: PlanStep, spec: Any) -> float | None:
    if spec is not None:
        try:
            estimate = spec.estimate(dict(step.params)).to_json()
        except Exception:
            return None
        high = estimate.get("high")
        if estimate.get("known") and isinstance(high, (int, float)) and not isinstance(high, bool):
            from app.services.orch_bridge import to_usd

            usd = to_usd(high)
            if usd is not None and math.isfinite(usd) and usd >= 0:
                return float(usd)
        return None
    if step.estimated_cost and step.estimated_cost > 0 and not step.params.get("_cost_unknown"):
        return float(step.estimated_cost)
    return None


def plan_cost(plan: list[PlanStep], views: list[dict[str, Any]]) -> tuple[float | None, str]:
    total = 0.0
    paid_any = False
    for step, view in zip(plan, views, strict=False):
        if not view.get("paid"):
            continue
        paid_any = True
        usd = _step_usd(step, _spec(step.action))
        if usd is None:
            return None, UNKNOWN_COST_TEXT
        total += usd
    if not paid_any:
        return 0.0, "бесплатно: платных шагов нет"
    return round(total, 4), f"около ${round(total, 2)}"


def plan_risk(views: list[dict[str, Any]]) -> str:
    best = "none"
    for view in views:
        risk = str(view.get("risk") or "none")
        if risk not in _RISK_ORDER:
            return "high"
        if _RISK_ORDER.index(risk) > _RISK_ORDER.index(best):
            best = risk
    return best


def enrich(
    payload: dict[str, Any],
    plan: list[PlanStep],
    actions: Any,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not plan or isinstance(payload.get("steps"), list):
        return payload
    views = plan_steps(plan, actions, labels)
    body = dict(payload)
    body["steps"] = views
    cost, cost_text = plan_cost(plan, views)
    card_cost = body.get("cost")
    if isinstance(card_cost, dict) and card_cost.get("known") is False and any(v.get("paid") for v in views):
        cost, cost_text = None, UNKNOWN_COST_TEXT
    if body.get("cost_estimate") is None and cost is not None and cost > 0:
        body["cost_estimate"] = cost
    body.setdefault("cost_text", cost_text)
    if not body.get("risk"):
        body["risk"] = plan_risk(views)
    return body
