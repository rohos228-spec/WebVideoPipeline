"""Граф ролика как единица управления: чтение, диф, операции, применение.

До этого модуля граф проекта (`project.meta.canvas_graph`) правился только
целиком через `PATCH /projects/{id}` с анти-wipe эвристикой, а стадии и
чат-агент про него не знали вовсе. Здесь собрано всё, что нужно, чтобы граф
можно было **предложить, показать разницу и применить** — из интерфейса или
из разговора с агентом, одним и тем же путём.

**Почему diff, а не «сохранить».** Перестройка графа на живом проекте — это
не правка схемы, а инвалидация сделанной работы: узел, вставленный перед
картинками, делает картинки устаревшими. Человек обязан видеть это ДО
применения — «+2 узла, −1 связь, сгорят шаги img_pr…assemble», — и решать
сам. Поэтому применение принимает уже посчитанный план сброса, а не
догадывается о нём после записи.

**Почему операции, а не только целый граф.** Модели проще сказать «вставь
проверку после картинок», чем переписать тридцать узлов без ошибки в id.
Операции детерминированно переводятся в новый граф, а дальше путь общий:
валидация → диф → предложение → применение.

Порядок исполнения по-прежнему считает детерминированный планировщик
(`app/orchestrator/graph/planner.py`); здесь только форма графа.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Project, ProjectStatus, Workflow, WorkflowRun
from app.orchestrator.node_registry import (
    CONFIG_NODE_TYPES,
    HITL_NODE_TYPES,
    NODE_TYPE_TO_STEP_CODE,
    WORK_NODES,
    is_work_node_type,
)

#: Какой стадии принадлежит тип узла. Стадии — свёртка графа для простого
#: экрана; здесь та самая карта свёртки, и она одна на сервер.
STAGE_OF_NODE_TYPE: dict[str, str] = {
    "plan": "plan",
    "script": "script",
    "split": "frames",
    "scene_design": "cast",
    "sd_agent": "cast",
    "sd_assemble": "cast",
    "hero": "cast",
    "items": "cast",
    "excel_gpt": "cast",
    "enrich_1": "cast",
    "enrich_2": "cast",
    "enrich_3": "cast",
    "enrich_4": "cast",
    "enrich_5": "cast",
    "image_prompts": "images",
    "images": "images",
    "animation_prompts": "videos",
    "videos": "videos",
    "audio": "final",
    "music": "final",
    "sfx_plan": "final",
    "sfx_gen": "final",
    "assemble": "final",
    "publish": "final",
}

#: Виды связей, которые понимает планировщик.
EDGE_KINDS: frozenset[str] = frozenset({"after", "gate", "pass", "fail"})

#: Шаг раскладки по горизонтали — тот же, что у штатного графа.
LAYOUT_STEP_X = 290
LAYOUT_STEP_Y = 130
LAYOUT_BASE_X = 80
LAYOUT_BASE_Y = 200

#: Поля `data`, изменение которых НЕ делает результат узла устаревшим.
#: Подпись и описание — оформление; позиция вообще не в data.
_COSMETIC_DATA_KEYS: frozenset[str] = frozenset({"label", "description", "title"})


class GraphError(ValueError):
    """Граф не принят: ошибки валидации или операция не имеет смысла."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = list(errors or [message])


def all_node_types() -> set[str]:
    return set(WORK_NODES) | set(HITL_NODE_TYPES) | set(CONFIG_NODE_TYPES) | {"excel_gpt", "excel_feed"}


# ── Чтение ───────────────────────────────────────────────────────────────


def _node_data(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    return dict(data) if isinstance(data, dict) else {}


#: Типы, у которых маркер веера вообще что-то значит (`effective_node_type`).
_MARKED_NODE_TYPES: frozenset[str] = frozenset({"excel_gpt", "sd_agent", "sd_assemble"})


def _scene_agent_markers() -> set[str]:
    """Допустимые значения `data.sd_agent`.

    Снятые с волн агенты (style) остаются допустимыми: они лежат в графах
    старых роликов, и запрет означал бы, что такой ролик больше не сохранить.
    """
    from app.services.scene_design.agents import ALL_AGENTS, ASSEMBLER, DEPRECATED_AGENTS

    return {*ALL_AGENTS, ASSEMBLER, *DEPRECATED_AGENTS}


def edge_kind(edge: dict[str, Any]) -> str:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    kind = str((data or {}).get("kind") or edge.get("kind") or "after").strip().lower()
    if kind in ("ok", "если ok", "если_ok"):
        return "pass"
    if kind in ("не ok", "не ок", "not_ok"):
        return "fail"
    if kind in ("feed", "review", ""):
        return "after"
    return kind if kind in EDGE_KINDS else "after"


def node_disabled(node: dict[str, Any]) -> bool:
    return _node_data(node).get("disabled") is True


def node_step_code(node: dict[str, Any]) -> str | None:
    """Код шага узла. Для «Работы с GPT» — слот доработки, если он назначен."""
    from app.services.excel_gpt_node import EXCEL_GPT_NODE_TYPE, effective_node_type, slot_index_from_node

    typ = effective_node_type(node)
    if str(node.get("type") or "") == EXCEL_GPT_NODE_TYPE and typ == EXCEL_GPT_NODE_TYPE:
        slot = slot_index_from_node(node)
        if 1 <= slot <= 5:
            return f"enrich_{slot}"
        return "excel_gpt"
    return NODE_TYPE_TO_STEP_CODE.get(typ)


def node_label(node: dict[str, Any]) -> str:
    from app.web.routers.workflows import NODE_LABELS

    data = _node_data(node)
    return str(data.get("label") or NODE_LABELS.get(str(node.get("type") or ""), node.get("type") or ""))


@dataclass
class ProjectGraph:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    #: canvas — граф проекта; run — снимок прогона; workflow — штатная схема;
    #: default — встроенный граф. Всё, кроме canvas, означает «у проекта
    #: своего графа ещё нет».
    source: str
    workflow_id: int | None = None

    @property
    def owned(self) -> bool:
        return self.source == "canvas"


async def load_project_graph(session: AsyncSession, project: Project) -> ProjectGraph:
    """Граф, по которому проект идёт сейчас. Тот же порядок, что у планировщика."""
    from app.orchestrator.default_graph import default_graph
    from app.services.canvas_graph import canvas_graph_from_meta

    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta)
    if cg:
        wf_raw = cg.get("workflow_id")
        return ProjectGraph(
            nodes=[dict(n) for n in cg["nodes"]],
            edges=[dict(e) for e in cg["edges"]],
            source="canvas",
            workflow_id=int(wf_raw) if isinstance(wf_raw, int) else None,
        )
    run = (
        await session.execute(select(WorkflowRun).where(WorkflowRun.project_id == project.id))
    ).scalar_one_or_none()
    if run is not None and isinstance(run.nodes_snapshot, list) and run.nodes_snapshot:
        return ProjectGraph(
            nodes=[dict(n) for n in run.nodes_snapshot],
            edges=[dict(e) for e in (run.edges_snapshot or [])],
            source="run",
            workflow_id=run.workflow_id,
        )
    wf = (await session.execute(select(Workflow).where(Workflow.is_default.is_(True)))).scalars().first()
    if wf is not None and wf.nodes:
        return ProjectGraph(
            nodes=[dict(n) for n in wf.nodes],
            edges=[dict(e) for e in (wf.edges or [])],
            source="workflow",
            workflow_id=wf.id,
        )
    nodes, edges = default_graph()
    return ProjectGraph(nodes=nodes, edges=edges, source="default")


# ── Раскладка ────────────────────────────────────────────────────────────


def _layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    """Слой узла — длина самого длинного пути от входа (без петель «не ок»)."""
    ids = [str(n.get("id")) for n in nodes if n.get("id")]
    known = set(ids)
    preds: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
        if src in known and tgt in known and edge_kind(e) != "fail" and src != tgt:
            preds[tgt].append(src)
    layer: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(nid: str) -> int:
        if nid in layer:
            return layer[nid]
        if nid in visiting:
            return 0  # цикл — валидация его отдельно отвергнет
        visiting.add(nid)
        d = 0 if not preds[nid] else 1 + max(depth(p) for p in preds[nid])
        visiting.discard(nid)
        layer[nid] = d
        return d

    for nid in ids:
        depth(nid)
    return layer


def layout_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    only_missing: bool = True,
) -> list[dict[str, Any]]:
    """Расставить узлы: слои слева направо, ветки одного слоя — вниз.

    По умолчанию трогает только узлы без позиции: граф из чата приходит без
    координат, а граф с холста — с теми, что человек расставил руками, и
    перекладывать их за него нельзя.
    """
    layers = _layers(nodes, edges)
    out = [dict(n) for n in nodes]
    occupied: dict[int, int] = {}
    for n in out:
        pos = n.get("position")
        has_pos = (
            isinstance(pos, dict)
            and isinstance(pos.get("x"), int | float)
            and isinstance(pos.get("y"), int | float)
        )
        if has_pos and only_missing:
            continue
        lay = layers.get(str(n.get("id")), 0)
        row = occupied.get(lay, 0)
        occupied[lay] = row + 1
        n["position"] = {"x": LAYOUT_BASE_X + lay * LAYOUT_STEP_X, "y": LAYOUT_BASE_Y + row * LAYOUT_STEP_Y}
    return out


# ── Диф ──────────────────────────────────────────────────────────────────


@dataclass
class GraphDiff:
    added_nodes: list[dict[str, Any]] = field(default_factory=list)
    removed_nodes: list[dict[str, Any]] = field(default_factory=list)
    changed_nodes: list[dict[str, Any]] = field(default_factory=list)
    added_edges: list[dict[str, Any]] = field(default_factory=list)
    removed_edges: list[dict[str, Any]] = field(default_factory=list)
    changed_edges: list[dict[str, Any]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.added_nodes,
                self.removed_nodes,
                self.changed_nodes,
                self.added_edges,
                self.removed_edges,
                self.changed_edges,
            )
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.added_nodes:
            parts.append(f"+{len(self.added_nodes)} узл.")
        if self.removed_nodes:
            parts.append(f"−{len(self.removed_nodes)} узл.")
        if self.changed_nodes:
            parts.append(f"~{len(self.changed_nodes)} узл.")
        if self.added_edges:
            parts.append(f"+{len(self.added_edges)} связ.")
        if self.removed_edges:
            parts.append(f"−{len(self.removed_edges)} связ.")
        if self.changed_edges:
            parts.append(f"~{len(self.changed_edges)} связ.")
        return ", ".join(parts) if parts else "без изменений"

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_nodes": self.added_nodes,
            "removed_nodes": self.removed_nodes,
            "changed_nodes": self.changed_nodes,
            "added_edges": self.added_edges,
            "removed_edges": self.removed_edges,
            "changed_edges": self.changed_edges,
            "summary": self.summary(),
            "empty": self.empty,
        }


def _brief(node: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(node.get("id")), "type": str(node.get("type") or ""), "label": node_label(node)}


def _edge_brief(edge: dict[str, Any]) -> dict[str, Any]:
    return {"source": str(edge.get("source")), "target": str(edge.get("target")), "kind": edge_kind(edge)}


def graph_diff(
    old_nodes: list[dict[str, Any]],
    old_edges: list[dict[str, Any]],
    new_nodes: list[dict[str, Any]],
    new_edges: list[dict[str, Any]],
) -> GraphDiff:
    """Что изменилось. Позиции не считаются: перетащить узел — не правка."""
    diff = GraphDiff()
    old_by = {str(n.get("id")): n for n in old_nodes if n.get("id")}
    new_by = {str(n.get("id")): n for n in new_nodes if n.get("id")}

    for nid, n in new_by.items():
        if nid not in old_by:
            diff.added_nodes.append(_brief(n))
            continue
        o = old_by[nid]
        changes: list[str] = []
        if str(o.get("type") or "") != str(n.get("type") or ""):
            changes.append("type")
        od, nd = _node_data(o), _node_data(n)
        for key in sorted(set(od) | set(nd)):
            if od.get(key) != nd.get(key):
                changes.append(key)
        if changes:
            diff.changed_nodes.append({**_brief(n), "changes": changes})
    for nid, o in old_by.items():
        if nid not in new_by:
            diff.removed_nodes.append(_brief(o))

    old_e = {(str(e.get("source")), str(e.get("target"))): e for e in old_edges}
    new_e = {(str(e.get("source")), str(e.get("target"))): e for e in new_edges}
    for pair, e in new_e.items():
        if pair not in old_e:
            diff.added_edges.append(_edge_brief(e))
        elif edge_kind(old_e[pair]) != edge_kind(e):
            diff.changed_edges.append({**_edge_brief(e), "was": edge_kind(old_e[pair])})
    for pair, e in old_e.items():
        if pair not in new_e:
            diff.removed_edges.append(_edge_brief(e))
    return diff


# ── План сброса ──────────────────────────────────────────────────────────


def project_rank(project: Project) -> int:
    """Позиция проекта в цепочке; для paused/failed — последняя линейная."""
    from app.services.pipeline_stages import OFF_LINE_STATUSES, status_rank

    if project.status in OFF_LINE_STATUSES:
        meta = project.meta if isinstance(project.meta, dict) else {}
        last = meta.get("last_linear_status")
        try:
            return status_rank(ProjectStatus(last)) if last else -1
        except ValueError:
            return -1
    return status_rank(project.status)


def step_is_done(project: Project, step_code: str | None) -> bool:
    """Проект уже прошёл шаг — его результат существует и может устареть."""
    from app.orchestrator.node_registry import STEP_CODE_TO_NODE_TYPE
    from app.services.pipeline_stages import status_rank

    if not step_code:
        return False
    typ = STEP_CODE_TO_NODE_TYPE.get(step_code)
    spec = WORK_NODES.get(typ or "")
    if spec is None:
        return False
    return project_rank(project) >= status_rank(spec.ready_status) >= 0


@dataclass
class ResetPlan:
    #: С какого шага сбрасывать (конус вниз возьмёт остальное).
    first_step: str | None = None
    #: Все шаги, которые сгорят, в порядке цепочки.
    steps: list[str] = field(default_factory=list)
    #: Почему: узел → причина. Для объяснения человеку.
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"first_step": self.first_step, "steps": self.steps, "reasons": self.reasons}


def reset_plan(
    project: Project,
    old_nodes: list[dict[str, Any]],
    new_nodes: list[dict[str, Any]],
    diff: GraphDiff,
) -> ResetPlan:
    """Какие сделанные шаги устаревают от такой правки.

    Правило одно: результат шага устарел, если поменялось то, из чего он
    делался, — сам узел (тип, модель, параметры), его удалили, или в уже
    пройденный участок вставили новый рабочий узел / перевели связь. Подпись
    и позиция результата не меняют. Дальше берётся самый ранний из таких
    шагов, и конус зависимостей (`step_dependencies`) добирает остальное.
    """
    from app.orchestrator.step_dependencies import TOPO_ORDER, dependents_cone

    old_by = {str(n.get("id")): n for n in old_nodes if n.get("id")}
    new_by = {str(n.get("id")): n for n in new_nodes if n.get("id")}
    stale: dict[str, str] = {}

    def mark(step: str | None, reason: str) -> None:
        if step and step_is_done(project, step) and step not in stale:
            stale[step] = reason

    for item in diff.removed_nodes:
        node = old_by.get(item["id"])
        if node is not None and is_work_node_type(str(node.get("type") or "")):
            mark(node_step_code(node), f"узел «{item['label']}» удалён")
    for item in diff.changed_nodes:
        node = new_by[item["id"]]  # изменённый узел есть в новом графе по построению
        meaningful = [c for c in item.get("changes", []) if c not in _COSMETIC_DATA_KEYS and c != "disabled"]
        if meaningful and is_work_node_type(str(node.get("type") or "")):
            mark(node_step_code(node), f"узел «{item['label']}» изменён ({', '.join(meaningful)})")
    for item in diff.added_nodes:
        node = new_by.get(item["id"])
        if node is not None and is_work_node_type(str(node.get("type") or "")) and not node_disabled(node):
            mark(node_step_code(node), f"узел «{item['label']}» добавлен в пройденный участок")
    for item in diff.added_edges + diff.removed_edges + diff.changed_edges:
        node = new_by.get(item["target"]) or old_by.get(item["target"])
        if node is not None and is_work_node_type(str(node.get("type") or "")):
            mark(node_step_code(node), f"связи узла «{node_label(node)}» переведены")

    if not stale:
        return ResetPlan()
    order = {code: i for i, code in enumerate(TOPO_ORDER)}
    first = min(stale, key=lambda c: order.get(c, len(order)))
    cone = list(dependents_cone(first)) or [first]
    return ResetPlan(first_step=first, steps=cone, reasons=stale)


# ── Операции ─────────────────────────────────────────────────────────────


def _unique_id(existing: set[str], base: str) -> str:
    candidate = base
    while candidate in existing:
        candidate = f"{base}_{uuid.uuid4().hex[:4]}"
    existing.add(candidate)
    return candidate


def apply_graph_ops(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ops: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Применить список операций к графу. Возвращает новый граф и отчёт.

    Операции: add_node, remove_node, connect, disconnect, set_edge_kind,
    set_node. Каждая либо применяется целиком, либо роняет весь список
    `GraphError`: половина правок хуже, чем ни одной.
    """
    nodes = [dict(n) for n in nodes]
    edges = [dict(e) for e in edges]
    applied: list[str] = []
    valid_types = all_node_types()

    def by_id() -> dict[str, dict[str, Any]]:
        return {str(n.get("id")): n for n in nodes if n.get("id")}

    def find(nid: Any, what: str) -> dict[str, Any]:
        node = by_id().get(str(nid or ""))
        if node is None:
            raise GraphError(f"{what}: узла {nid!r} нет; есть: {', '.join(sorted(by_id()))[:400]}")
        return node

    def add_edge(src: str, tgt: str, kind: str) -> None:
        if src == tgt:
            raise GraphError(f"связь узла с самим собой: {src}")
        if kind not in EDGE_KINDS:
            raise GraphError(f"неизвестный вид связи {kind!r}; есть: {', '.join(sorted(EDGE_KINDS))}")
        for e in edges:
            if str(e.get("source")) == src and str(e.get("target")) == tgt:
                e["data"] = {**(e.get("data") or {}), "kind": kind}
                return
        edges.append({"id": f"e_{src}__{tgt}", "source": src, "target": tgt, "data": {"kind": kind}})

    for raw in ops:
        if not isinstance(raw, dict):
            raise GraphError("операция должна быть объектом")
        op = _op_name(raw)

        if op == "add_node":
            typ = str(raw.get("type") or "").strip()
            if typ not in valid_types:
                raise GraphError(f"add_node: неизвестный тип {typ!r}; есть: {', '.join(sorted(valid_types))}")
            ids = set(by_id())
            nid = _unique_id(ids, str(raw.get("id") or f"n_{typ}"))
            data = dict(raw.get("data") or {}) if isinstance(raw.get("data"), dict) else {}
            if raw.get("label"):
                data["label"] = str(raw["label"])
            if raw.get("model_id"):
                data["modelId"] = str(raw["model_id"])
            node: dict[str, Any] = {"id": nid, "type": typ, "data": data}
            after = str(raw.get("after") or "").strip()
            before = str(raw.get("before") or "").strip()
            anchor = None
            if after:
                anchor = find(after, "add_node.after")
                successors = [
                    e
                    for e in edges
                    if str(e.get("source")) == after and edge_kind(e) == "after" and not _is_sink(by_id(), e)
                ]
                for e in successors:
                    e["source"] = nid
                add_edge(after, nid, "after")
            elif before:
                anchor = find(before, "add_node.before")
                predecessors = [
                    e for e in edges if str(e.get("target")) == before and edge_kind(e) == "after"
                ]
                for e in predecessors:
                    e["target"] = nid
                add_edge(nid, before, "after")
            if anchor is not None and isinstance(anchor.get("position"), dict):
                pos = anchor["position"]
                dx = LAYOUT_STEP_X // 2 if after else -(LAYOUT_STEP_X // 2)
                node["position"] = {
                    "x": float(pos.get("x", 0)) + dx,
                    "y": float(pos.get("y", 0)) + LAYOUT_STEP_Y,
                }
            nodes.append(node)
            applied.append(f"add_node {nid} ({typ})")

        elif op == "remove_node":
            node = find(raw.get("id"), "remove_node")
            nid = str(node["id"])
            preds = [
                str(e["source"]) for e in edges if str(e.get("target")) == nid and edge_kind(e) == "after"
            ]
            succs = [
                str(e["target"]) for e in edges if str(e.get("source")) == nid and edge_kind(e) == "after"
            ]
            edges = [e for e in edges if str(e.get("source")) != nid and str(e.get("target")) != nid]
            nodes = [n for n in nodes if str(n.get("id")) != nid]
            if raw.get("bridge", True):
                sinks = {str(n["id"]) for n in nodes if str(n.get("type")) == "storage"}
                for p in preds:
                    for s in succs:
                        if p != s and s not in sinks:
                            add_edge(p, s, "after")
            applied.append(f"remove_node {nid}")

        elif op == "connect":
            src, tgt = str(raw.get("source") or ""), str(raw.get("target") or "")
            find(src, "connect.source")
            find(tgt, "connect.target")
            add_edge(src, tgt, str(raw.get("kind") or "after").strip().lower())
            applied.append(f"connect {src} → {tgt}")

        elif op == "disconnect":
            src, tgt = str(raw.get("source") or ""), str(raw.get("target") or "")
            before_n = len(edges)
            edges = [e for e in edges if not (str(e.get("source")) == src and str(e.get("target")) == tgt)]
            if len(edges) == before_n:
                raise GraphError(f"disconnect: связи {src} → {tgt} нет")
            applied.append(f"disconnect {src} → {tgt}")

        elif op == "set_edge_kind":
            src, tgt = str(raw.get("source") or ""), str(raw.get("target") or "")
            kind = str(raw.get("kind") or "").strip().lower()
            hit = [e for e in edges if str(e.get("source")) == src and str(e.get("target")) == tgt]
            if not hit:
                raise GraphError(f"set_edge_kind: связи {src} → {tgt} нет")
            if kind not in EDGE_KINDS:
                raise GraphError(f"неизвестный вид связи {kind!r}; есть: {', '.join(sorted(EDGE_KINDS))}")
            for e in hit:
                e["data"] = {**(e.get("data") or {}), "kind": kind}
            applied.append(f"set_edge_kind {src} → {tgt} = {kind}")

        elif op == "set_node":
            node = find(raw.get("id"), "set_node")
            data = _node_data(node)
            if "label" in raw:
                data["label"] = str(raw["label"] or "")
            if "disabled" in raw:
                data["disabled"] = bool(raw["disabled"])
            if "model_id" in raw:
                if raw["model_id"]:
                    data["modelId"] = str(raw["model_id"])
                else:
                    data.pop("modelId", None)
            if isinstance(raw.get("data"), dict):
                data.update(raw["data"])
            node["data"] = data
            applied.append(f"set_node {node['id']}")

        elif not op:
            raise GraphError(f"у операции нет ключа op; есть: {', '.join(GRAPH_OPS)}")
        else:
            raise GraphError(f"неизвестная операция {op!r}; есть: {', '.join(GRAPH_OPS)}")

    return nodes, edges, applied


GRAPH_OPS = ("add_node", "remove_node", "connect", "disconnect", "set_edge_kind", "set_node")
_SET_NODE_FIELDS = frozenset({"label", "disabled", "model_id"})


def _op_name(raw: dict[str, Any]) -> str:
    """Имя операции из объекта модели.

    Канон — ключ `op`. Живой прогон (MiniMax-M3, 2026-08-26) показал, что
    модель пишет ключ как `type`/`action`, а для set_node вовсе опускает его:
    `{"id": "n_music", "disabled": true}` — ровно так операцию описывает
    системный промт («set_node disabled=true»). Пять отказов подряд на одном
    и том же — это не защита контракта, а глухота к очевидному. `type`
    берём как имя операции только при значении из списка: в add_node тот
    же ключ означает тип узла.
    """
    op = str(raw.get("op") or "").strip()
    if op:
        return op
    for key in ("action", "type"):
        alias = str(raw.get(key) or "").strip()
        if alias in GRAPH_OPS:
            return alias
    if raw.get("id") and (set(raw) - {"id"}) and (set(raw) - {"id"}) <= _SET_NODE_FIELDS:
        return "set_node"
    return ""


def _is_sink(by_id: dict[str, dict[str, Any]], edge: dict[str, Any]) -> bool:
    tgt = by_id.get(str(edge.get("target")))
    return bool(tgt and str(tgt.get("type")) == "storage")


# ── Нормализация и проверка ──────────────────────────────────────────────


def normalize_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Привести граф к тому виду, что хранится: слоты, позиции, проверка."""
    from app.orchestrator.graph.validate import validate_workflow_graph
    from app.services.excel_gpt_node import assign_slot_indices, migrate_enrich_nodes

    valid_types = all_node_types()
    valid_markers = _scene_agent_markers()
    clean_nodes: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            raise GraphError("узел без id")
        typ = str(n.get("type") or "").strip()
        if typ not in valid_types:
            raise GraphError(
                f"неизвестный тип узла {typ!r} у {n.get('id')}; есть: {', '.join(sorted(valid_types))}"
            )
        data = _node_data(n)
        if typ in _MARKED_NODE_TYPES:
            marker = str(data.get("sd_agent") or data.get("agent") or "").strip()
            if marker and marker not in valid_markers:
                # Опечатка в маркере не видна ничем: планировщик посчитает
                # ноду обычной «Работой с GPT», а сцен-дизайн просто не найдёт
                # агента — веер молча соберётся без него.
                raise GraphError(
                    f"неизвестная роль в веере сцен {marker!r} у {n['id']}; "
                    f"есть: {', '.join(sorted(valid_markers))}"
                )
        clean = {"id": str(n["id"]), "type": typ, "data": data}
        if isinstance(n.get("position"), dict):
            clean["position"] = dict(n["position"])
        clean_nodes.append(clean)
    clean_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))
        edata: dict[str, Any] = dict(e["data"]) if isinstance(e.get("data"), dict) else {}
        edata["kind"] = edge_kind(e)
        clean_edges.append(
            {
                "id": str(e.get("id") or f"e_{src}__{tgt}"),
                "source": src,
                "target": tgt,
                "data": edata,
            }
        )
    clean_nodes = assign_slot_indices(migrate_enrich_nodes(clean_nodes))
    clean_nodes = layout_graph(clean_nodes, clean_edges)
    check = validate_workflow_graph(clean_nodes, clean_edges)
    if check.get("edges"):
        clean_edges = list(check["edges"])
    return clean_nodes, clean_edges, check


# ── Предложение (proposal) ───────────────────────────────────────────────


def proposal_from_meta(project: Project) -> dict[str, Any] | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("graph_proposal")
    return raw if isinstance(raw, dict) and raw.get("id") else None


async def propose_project_graph(
    session: AsyncSession,
    project: Project,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    reason: str = "",
    author: str = "agent",
) -> dict[str, Any]:
    """Проверить граф, посчитать диф и план сброса, положить как предложение.

    Ничего не применяет. Предложение живёт в `meta.graph_proposal`, пока его
    не применят или не отклонят; следующее предложение замещает предыдущее —
    два конкурирующих плана на один граф человеку не показать.
    """
    clean_nodes, clean_edges, check = normalize_graph(nodes, edges)
    if not check["valid"]:
        raise GraphError("граф не проходит проверку: " + "; ".join(check["errors"]), check["errors"])
    current = await load_project_graph(session, project)
    diff = graph_diff(current.nodes, current.edges, clean_nodes, clean_edges)
    plan = reset_plan(project, current.nodes, clean_nodes, diff)
    proposal = {
        "id": uuid.uuid4().hex[:8],
        "author": author,
        "reason": str(reason or ""),
        "created_at": datetime.now(UTC).isoformat(),
        "nodes": clean_nodes,
        "edges": clean_edges,
        "diff": diff.to_dict(),
        "reset": plan.to_dict(),
        "warnings": list(check.get("warnings") or []),
    }
    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    meta["graph_proposal"] = proposal
    project.meta = meta
    flag_modified(project, "meta")
    await session.flush()
    return proposal


def discard_proposal(project: Project) -> bool:
    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    had = meta.pop("graph_proposal", None) is not None
    if had:
        project.meta = meta
        flag_modified(project, "meta")
    return had


# ── Применение ───────────────────────────────────────────────────────────


async def apply_project_graph(
    session: AsyncSession,
    project: Project,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    reset: bool = True,
    source: str = "ui",
) -> dict[str, Any]:
    """Записать граф проекту и, если просили, сбросить устаревшие шаги.

    Пишется напрямую в `meta.canvas_graph`, минуя анти-wipe эвристику
    `project_meta`: та защищает от полусохранённого черновика с холста, а
    здесь граф уже проверен и применяется осознанно — в том числе с
    удалением половины узлов, если человек так решил.
    """
    from app.services.canvas_graph import build_canvas_graph_payload, sync_run_snapshot_from_canvas_graph

    clean_nodes, clean_edges, check = normalize_graph(nodes, edges)
    if not check["valid"]:
        raise GraphError("граф не проходит проверку: " + "; ".join(check["errors"]), check["errors"])
    current = await load_project_graph(session, project)
    diff = graph_diff(current.nodes, current.edges, clean_nodes, clean_edges)
    plan = reset_plan(project, current.nodes, clean_nodes, diff)

    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    meta["canvas_graph"] = build_canvas_graph_payload(
        workflow_id=int(current.workflow_id or 0),
        nodes=clean_nodes,
        edges=clean_edges,
    )
    # Выключенные узлы живут в data.disabled — это то, что видно на холсте;
    # планировщик читает meta.disabled_nodes. Один источник, второй — производный.
    meta["disabled_nodes"] = [str(n["id"]) for n in clean_nodes if node_disabled(n)]
    meta.pop("graph_proposal", None)
    project.meta = meta
    flag_modified(project, "meta")
    await session.flush()
    await sync_run_snapshot_from_canvas_graph(session, project, force=True)

    reset_summary: dict[str, Any] | None = None
    if reset and plan.first_step:
        from app.services.reset_step import reset_step

        reset_summary = await reset_step(session, project, plan.first_step)
        if reset_summary.get("error"):
            logger.warning(
                "[#{}] graph apply: reset {} failed: {}", project.id, plan.first_step, reset_summary["error"]
            )
    # Коммит здесь, а не у вызывающего. Соседний `insert_node_group` фиксирует
    # транзакцию сам, и асимметрия стоила отладки: программный вызов (агент,
    # скрипт, миграция) отрабатывал «успешно», печатал верный диф и не менял
    # ничего — правки уезжали при закрытии сессии. Веб-роут вызывает
    # `_after_change` с ещё одним commit; повторный commit безвреден.
    await session.commit()
    logger.info(
        "[#{}] graph applied ({}): {}; reset={}",
        project.id,
        source,
        diff.summary(),
        plan.first_step if reset else "off",
    )
    return {
        "diff": diff.to_dict(),
        "reset": plan.to_dict(),
        "reset_done": bool(reset_summary and not reset_summary.get("error")),
        "reset_summary": reset_summary,
        "warnings": list(check.get("warnings") or []),
        "nodes": clean_nodes,
        "edges": clean_edges,
    }


async def apply_proposal(
    session: AsyncSession, project: Project, proposal_id: str, *, reset: bool = True
) -> dict[str, Any]:
    proposal = proposal_from_meta(project)
    if proposal is None:
        raise GraphError("предложения по графу нет — сначала предложи изменения")
    if str(proposal.get("id")) != str(proposal_id):
        raise GraphError(f"предложение {proposal_id} устарело; актуальное — {proposal.get('id')}")
    result = await apply_project_graph(
        session,
        project,
        list(proposal.get("nodes") or []),
        list(proposal.get("edges") or []),
        reset=reset,
        source=f"proposal:{proposal.get('author', '?')}",
    )
    result["proposal_id"] = proposal_id
    return result


async def reset_project_graph_to_default(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Вернуть проекту штатную схему. Тот же путь, что у любой правки."""
    from app.orchestrator.default_graph import default_graph

    wf = (await session.execute(select(Workflow).where(Workflow.is_default.is_(True)))).scalars().first()
    if wf is not None and wf.nodes:
        nodes, edges = [dict(n) for n in wf.nodes], [dict(e) for e in (wf.edges or [])]
    else:
        nodes, edges = default_graph()
    return await apply_project_graph(session, project, nodes, edges, reset=False, source="reset-default")


# ── Состояния и представления ────────────────────────────────────────────


#: Уже названные причины отказа `node_states` — (project_id, тип, текст).
#: Опрос графа идёт с каждым тиком UI; без дедупликации одна и та же поломка
#: залила бы лог, а с прежним `debug` без текста её просто не было видно.
_NODE_STATES_SEEN: set[tuple[int, str, str]] = set()


def node_states(project: Project, graph: ProjectGraph) -> dict[str, str]:
    """Состояние каждого узла в терминах прогона: pending/running/done/skipped…"""
    from app.orchestrator.graph.planner import WorkflowGraph

    wg = WorkflowGraph(graph.nodes, graph.edges)
    try:
        return {k: v.value for k, v in wg.derived_node_states(project).items()}
    except Exception as e:  # noqa: BLE001 — состояние узлов подсказка, не данные
        # Раньше здесь был `logger.debug(..., exc_info=True)` без текста ошибки.
        # Опрос графа идёт с каждым тиком UI, поэтому в логе копилась строка
        # «node_states failed» без единого указания на причину, а на уровне
        # DEBUG её и не видно. Живой прогон 2026-08-31: падало регулярно и
        # осталось нерасследованным именно поэтому.
        #
        # Поведение не меняем — пустой словарь по-прежнему деградация, а не
        # отказ. Но причина теперь называется, и один раз на связку
        # (проект, тип, текст): опрос частый, а сообщение одно и то же.
        signature = (project.id, type(e).__name__, str(e)[:200])
        if signature not in _NODE_STATES_SEEN:
            _NODE_STATES_SEEN.add(signature)
            logger.warning(
                "[#{}] node_states: {} — {} (узлы покажутся без состояний)",
                project.id,
                type(e).__name__,
                str(e)[:200],
            )
            logger.debug("[#{}] node_states traceback", project.id, exc_info=True)
        return {}


def describe_graph(project: Project, graph: ProjectGraph) -> dict[str, Any]:
    """Компактный вид графа для модели и карточек: без позиций и мусора."""
    states = node_states(project, graph)
    nodes = []
    for n in graph.nodes:
        data = _node_data(n)
        item: dict[str, Any] = {
            "id": str(n.get("id")),
            "type": str(n.get("type") or ""),
            "label": node_label(n),
            "state": states.get(str(n.get("id")), "pending"),
        }
        step = node_step_code(n)
        if step:
            item["step_code"] = step
        stage = STAGE_OF_NODE_TYPE.get(str(n.get("type") or ""))
        if stage:
            item["stage"] = stage
        if data.get("disabled") is True:
            item["disabled"] = True
        if data.get("modelId"):
            item["model_id"] = str(data["modelId"])
        nodes.append(item)
    edges = [_edge_brief(e) for e in graph.edges]
    return {"source": graph.source, "nodes": nodes, "edges": edges}


def stage_nodes(graph: ProjectGraph, states: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """Узлы графа, разложенные по стадиям, в порядке слоёв слева направо."""
    layers = _layers(graph.nodes, graph.edges)
    out: dict[str, list[dict[str, Any]]] = {}
    ordered = sorted(graph.nodes, key=lambda n: (layers.get(str(n.get("id")), 0), str(n.get("id"))))
    for n in ordered:
        typ = str(n.get("type") or "")
        stage = STAGE_OF_NODE_TYPE.get(typ)
        if not stage:
            continue
        out.setdefault(stage, []).append(
            {
                "id": str(n.get("id")),
                "type": typ,
                "label": node_label(n),
                "step_code": node_step_code(n),
                "state": states.get(str(n.get("id")), "pending"),
                "disabled": node_disabled(n),
                "model_id": _node_data(n).get("modelId") or None,
            }
        )
    return out
