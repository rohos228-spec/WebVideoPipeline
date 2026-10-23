from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from loguru import logger

BASE_READ_IDS: tuple[str, ...] = (
    "project.status",
    "diag.incidents",
    "logs.tail",
    "node.output",
    "spend.summary",
    "projects.overview",
    "report.nodes",
    "node.describe",
)
# «Запусти / прогони / дальше» — запуск шага в каталоге всегда: иначе отбор по словам
# отдаёт место нодам и группам из той же просьбы, и планировщик отказывает «нет запуска шага».
LAUNCH_IDS: tuple[str, ...] = ("step.run_step",)
_LAUNCH_RE = re.compile(r"(?<![a-zа-я0-9_])(?:(?:пере)?запуст|прогон|прогна|стартуй|пусти|дальше)")
_DESC_LIMIT = 140


class _AllowedView:
    def __init__(self, specs: list[Any]) -> None:
        self._specs = {spec.id: spec for spec in specs}

    def list(self) -> list[Any]:
        return [self._specs[key] for key in sorted(self._specs)]

    def find(self, action_id: str) -> Any:
        return self._specs.get(action_id)


def _default_max() -> int:
    try:
        from app.settings import settings

        return int(settings.action_layer_planner_max_catalog)
    except Exception:
        return 24


def _task_project(task_id: Any) -> int | None:
    if task_id is None:
        return None
    try:
        from app.orchestrator.agents.task_store import TaskStore

        raw = (TaskStore().get(int(task_id)) or {}).get("project_id")
        value = int(raw) if raw is not None else 0
    except Exception:
        return None
    return value if value > 0 else None


def _allowed_specs() -> list[Any]:
    from app.orchestrator.actions import whitelist
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    allowed = set(whitelist())
    return [spec for spec in registry.list() if spec.id in allowed]


def _contract_flags(spec: Any) -> tuple[str, bool]:
    try:
        from app.orchestrator.actions import metadata_of

        meta = metadata_of(spec.id)
        return str(meta.risk), bool(meta.paid)
    except Exception:
        floor = {"read": "none", "local_safe": "low", "paid": "medium", "destructive": "high"}
        return floor.get(str(spec.risk), "high"), str(spec.risk) == "paid"


def _enum_values(spec: Any) -> dict[str, list[str]]:
    props = (getattr(spec, "params_schema", None) or {}).get("properties") or {}
    out: dict[str, list[str]] = {}
    for name in sorted(props):
        rule = props[name] if isinstance(props[name], dict) else {}
        values = rule.get("enum")
        if not isinstance(values, list) and isinstance(rule.get("items"), dict):
            values = rule["items"].get("enum")
        if isinstance(values, list) and values:
            out[name] = [str(value) for value in values]
    return out


def step_titles() -> dict[str, str]:
    try:
        from app.services import launch_risks
        from app.services.action_layer.builtin_launch import launch_step_codes

        return {code: str(launch_risks.step_title(code)) for code in launch_step_codes()}
    except Exception:
        logger.exception("orch planner step titles failed")
        return {}


def catalog_entry(spec: Any) -> dict[str, Any]:
    from app.services.action_layer.planner_catalog import compact_entry

    compact = compact_entry(spec)
    risk, paid = _contract_flags(spec)
    desc = " ".join(str(spec.description_ru or "").split())
    if len(desc) > _DESC_LIMIT:
        desc = desc[: _DESC_LIMIT - 1].rstrip() + "…"
    row = {
        "id": spec.id,
        "t": spec.title_ru,
        "d": desc,
        "risk": risk,
        "paid": paid,
        "p": compact["p"],
    }
    values = _enum_values(spec)
    if values:
        row["v"] = values
    return row


def _hints(rows: list[dict[str, Any]]) -> list[str]:
    if not any("step_code" in (row.get("v") or {}) for row in rows):
        return []
    titles = step_titles()
    if not titles:
        return []
    pairs = "; ".join(f"{code} = {title}" for code, title in titles.items())
    return [
        "step_code — шаг конвейера, только код из списка: " + pairs + ".",
        "Пример: «картинки» — step_code=img, «промты картинок» — img_pr, «видео» — video, "
        "«озвучка» — audio, «монтаж» — assemble, «сценарий» и «шаг сценария» (нода «Сценарий») — plan, "
        "«закадровый текст» — script, «разбивка» — split.",
    ]


def select_ids(text: str, specs: list[Any], max_catalog: int) -> list[str]:
    from app.services.action_layer.planner_catalog import select_catalog

    view = _AllowedView(specs)
    picked = select_catalog(text or "", None, view, max_catalog).ids
    base = [key for key in BASE_READ_IDS if view.find(key) is not None]
    if wants_launch(text):
        base += [key for key in LAUNCH_IDS if view.find(key) is not None]
    return sorted({*picked, *base})


def wants_launch(text: str) -> bool:
    return bool(_LAUNCH_RE.search((text or "").lower().replace("ё", "е")))


class ActionCatalog:
    def __init__(
        self,
        *,
        max_catalog: int | None = None,
        specs: Callable[[], list[Any]] | None = None,
        project_of: Callable[[Any], int | None] | None = None,
    ) -> None:
        self.max_catalog = max_catalog
        self._specs = specs or _allowed_specs
        self._project_of = project_of or _task_project

    def entries(self, text: str) -> list[dict[str, Any]]:
        specs = list(self._specs())
        if not specs:
            return []
        limit = self.max_catalog if self.max_catalog is not None else _default_max()
        ids = set(select_ids(text, specs, limit))
        return [catalog_entry(spec) for spec in sorted(specs, key=lambda s: s.id) if spec.id in ids]

    def block(self, text: str, task_id: Any = None) -> str:
        try:
            rows = self.entries(text)
        except Exception:
            logger.exception("orch planner catalog failed")
            return ""
        if not rows:
            return ""
        lines = [
            "Каталог действий. В поле action пиши только id из этого списка, другие имена отклоняются.",
            "t — название, d — что делает, risk — риск (none бесплатно и только чтение), "
            "paid — платное действие, p — параметры (* обязательный), "
            "v — допустимые значения параметра (другие значения отклоняются).",
        ]
        project_id = self._project_of(task_id)
        if project_id is not None:
            lines.append(f"Проект задачи: project_id={project_id}. Подставляй его в params.project_id.")
        lines.extend(_hints(rows))
        lines.extend(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        return "\n".join(lines)

    def __call__(self, text: str, task_id: Any = None) -> str:
        return self.block(text, task_id)
