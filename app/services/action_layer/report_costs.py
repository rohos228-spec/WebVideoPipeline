"""Траты проекта по нодам: только по связи, которую можно показать.

Связи (по убыванию надёжности):
1. request_id записи трат совпадает с request_id события ноды (observation_events);
2. run_id «native:<проект>:verdict-<шаг>» — проверка результата шага; нода этого шага одна на канвасе;
3. запись платного вызова попала во время прогона ровно одной ноды (node_runs started_at..finished_at).
Запись без связи остаётся «не разнесено»: у ноды без связанных записей стоимость «нет данных», не 0.
Траты чата (планировщик, критик, резервы действий) нодам не приписываются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

HOW_REQUEST = "по запросу ноды"
HOW_VERDICT = "проверка шага"
HOW_WINDOW = "по времени прогона"
NO_DATA = "нет данных"
NOT_RUN = "—"
# Статусы ноды, при которых она ничего не тратила: стоимость не «нет данных», а прочерк.
IDLE_STATUSES = frozenset({"не запускалась", "ждёт", "отключена"})

# Источники трат, которые идут не от нод: чат-оркестратор и резервы действий чата.
CHAT_SOURCES = frozenset({"orch_llm", "action_layer"})
SOURCE_RU = {
    "native_call": "вызовы моделей нодами",
    "orch_llm": "чат (планировщик и критик)",
    "action_layer": "резервы действий чата",
    "media_native": "картинки и видео",
    "reconcile_gap": "сверка с провайдером",
    "paid_gate_block": "остановленные платные вызовы",
}


@dataclass
class NodeCosts:
    by_node: dict[str, float] = field(default_factory=dict)
    how: dict[str, list[str]] = field(default_factory=dict)
    total: float | None = None
    by_source: dict[str, float] = field(default_factory=dict)

    @property
    def attributed(self) -> float:
        return sum(self.by_node.values())

    def add(self, key: str, usd: float, how: str) -> None:
        self.by_node[key] = self.by_node.get(key, 0.0) + usd
        seen = self.how.setdefault(key, [])
        if how not in seen:
            seen.append(how)


def _when(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    # В базе время без пояса и в UTC (datetime.utcnow); время с поясом приводим к нему же.
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _windows(node_runs: list[dict]) -> list[tuple[str, datetime, datetime]]:
    out = []
    for nr in node_runs or []:
        start, end = _when(nr.get("started_at")), _when(nr.get("finished_at"))
        key = str(nr.get("node_key") or "")
        if key and start is not None and end is not None and end >= start:
            out.append((key, start, end))
    return out


def _verdict_step(run_id: str, pid: int) -> str:
    prefix = f"native:{int(pid)}:verdict-"
    return run_id[len(prefix) :] if run_id.startswith(prefix) else ""


def split_costs(
    rows: list[dict],
    request_nodes: dict[str, set[str]],
    node_runs: list[dict],
    step_nodes: dict[str, list[str]],
    pid: int,
) -> NodeCosts:
    """Разносит записи трат по нодам. Чистая функция: на входе строки spend_ledger проекта."""
    out = NodeCosts(total=0.0)
    windows = _windows(node_runs)
    for row in rows:
        usd = float(row.get("usd") or 0.0)
        source = str(row.get("source") or "")
        out.total = (out.total or 0.0) + usd
        out.by_source[source] = out.by_source.get(source, 0.0) + usd
        if source in CHAT_SOURCES or not usd:
            continue
        owners = request_nodes.get(str(row.get("request_id") or "")) or set()
        if len(owners) == 1:
            out.add(next(iter(owners)), usd, HOW_REQUEST)
            continue
        step = _verdict_step(str(row.get("run_id") or ""), pid)
        if step:
            keys = step_nodes.get(step) or []
            if len(keys) == 1:
                out.add(keys[0], usd, HOW_VERDICT)
            continue
        moment = _when(row.get("created_at"))
        if moment is None:
            continue
        hits = {key for key, start, end in windows if start <= moment <= end}
        if len(hits) == 1:
            out.add(next(iter(hits)), usd, HOW_WINDOW)
    return out


async def node_costs(
    pid: int, *, node_runs: list[dict] | None = None, step_nodes: dict[str, list[str]] | None = None
) -> NodeCosts:
    """Записи трат проекта, разнесённые по нодам. База недоступна — total=None.

    Участнику — пусто (total=None): касса провайдеров общая, это себестоимость,
    а не его списания (`plan.member_view`).
    """
    from sqlalchemy import text

    from app.services.action_layer import builtin_insight as bi
    from app.services.action_layer.plan import member_view

    if member_view():
        return NodeCosts()
    try:
        async with bi._sessions()() as s:
            ledger = (
                await s.execute(
                    text(
                        "SELECT created_at, source, run_id, request_id, usd FROM spend_ledger "
                        "WHERE project_id = :p ORDER BY id"
                    ),
                    {"p": pid},
                )
            ).all()
            events = (
                await s.execute(
                    text(
                        "SELECT DISTINCT request_id, node_key FROM observation_events WHERE project_id = :p "
                        "AND node_key IS NOT NULL AND request_id IS NOT NULL AND request_id != ''"
                    ),
                    {"p": pid},
                )
            ).all()
    except SQLAlchemyError:
        return NodeCosts()
    request_nodes: dict[str, set[str]] = {}
    for rid, key in events:
        request_nodes.setdefault(str(rid), set()).add(str(key))
    rows = [
        {"created_at": r[0], "source": r[1], "run_id": r[2], "request_id": r[3], "usd": r[4]} for r in ledger
    ]
    return split_costs(rows, request_nodes, list(node_runs or []), dict(step_nodes or {}), pid)


def step_nodes_of(canvas: list[dict]) -> dict[str, list[str]]:
    """Шаг → ноды канваса этого шага (для связи «проверка шага»)."""
    from app.services.action_layer import builtin_insight as bi

    out: dict[str, list[str]] = {}
    for node in canvas:
        raw, eff = bi._node_types(node)
        out.setdefault(bi._step_code(raw, eff), []).append(str(node["id"]))
    return out


def empty_cost(status_ru: Any) -> str:
    return NOT_RUN if str(status_ru or "") in IDLE_STATUSES else NO_DATA


def cost_text(usd: float | None, status_ru: Any = "") -> str:
    return empty_cost(status_ru) if usd is None else f"{round(float(usd), 4)}"
