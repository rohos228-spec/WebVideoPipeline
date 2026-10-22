from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

_MAX_NODES = 60
_ENDINGS = tuple(
    sorted(
        (
            "иями",
            "ями",
            "ами",
            "ого",
            "его",
            "ому",
            "ему",
            "ыми",
            "ими",
            "иях",
            "ях",
            "ах",
            "ов",
            "ев",
            "ей",
            "ой",
            "ий",
            "ый",
            "ая",
            "яя",
            "ое",
            "ее",
            "ые",
            "ие",
            "ия",
            "ья",
            "ью",
            "ом",
            "ем",
            "ам",
            "ям",
            "ок",
            "ек",
            "а",
            "я",
            "о",
            "е",
            "ы",
            "и",
            "у",
            "ю",
            "ь",
            "й",
        ),
        key=len,
        reverse=True,
    )
)
_STOP = frozenset(
    {
        "нод",
        "шаг",
        "модел",
        "на",
        "в",
        "во",
        "с",
        "со",
        "и",
        "по",
        "для",
        "как",
        "какая",
        "какой",
        "стоит",
        "постав",
        "туд",
        "там",
        "проект",
        "эт",
        "эту",
        "этой",
        "мне",
        "что",
        "сейчас",
        "подешевл",
        "дешевл",
        "дорож",
        "смен",
        "покаж",
        "работ",
    }
)
_WORD = re.compile(r"[a-zа-я0-9_]+")
NO_FRAMES_RULE = (
    "Кадров в проекте: 0. Картинки, видео и промты к ним делать не из чего: такой запуск не "
    "планируй, сначала нужна разбивка на кадры (или импорт Excel)."
)
NODE_RULES = (
    "node_key бери только из списка нод проекта ниже, других ключей нет. «Нода сценария», "
    "«нода картинок» и т. п. — ищи по типу и подписи ноды в этом списке.",
)
MODEL_RULE = (
    "Чтобы сменить модель ноды, бери model_id только из списка моделей этой ноды ниже: модель "
    "дешевле или так же — действие node.set_model, дороже или цена неизвестна — "
    "node.set_model_costly. Не используй ссылки на результаты других шагов (вида $steps[...]) "
    "в params: только конкретные значения из списка."
)
_MAX_MODEL_NODES = 2
_MAX_OPTIONS = 20
_COST_RU = {"cheaper": "дешевле", "same": "так же", "costlier": "дороже", "unknown": "цена неизвестна"}
_SET_ACTION = {"cheaper": "node.set_model", "same": "node.set_model"}

MetaLoader = Callable[[int], Awaitable[Any]]
FramesLoader = Callable[[int], Awaitable[Any]]
ProposalsLoader = Callable[[int], Awaitable[Any]]
_APPLY_WORDS = ("примен", "предлож", "правк", "внес", "накат")
_MAX_PROPOSALS = 5
PROPOSALS_HEAD = (
    "Ожидающие предложения изменений проекта, самое свежее первым (для change.apply бери "
    "proposal_id и expected_digest только отсюда):"
)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _stem(word: str) -> str:
    for end in _ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[: -len(end)]
    return word


def _stems(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(_norm(text)):
        stem = _stem(word)
        if len(stem) >= 3 and stem not in _STOP and word not in _STOP:
            out.append(stem)
    return out


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))


def roster(meta: Any) -> list[dict[str, Any]]:
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services import node_edit as ne
    from app.services.action_layer import builtin_insight as bi

    if not isinstance(meta, dict):
        return []
    rows: list[dict[str, Any]] = []
    for node in bi._canvas_nodes(meta)[:_MAX_NODES]:
        key = str(node["id"])
        raw, eff = bi._node_types(node)
        typ = eff or raw
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        label = " ".join(str(data.get("label") or "").split())[:80]
        try:
            model = bi._model_for(meta, key, node, raw, eff)
        except Exception:
            model = ""
        rows.append(
            {
                "key": key,
                "type": typ,
                "type_ru": ne.TYPE_RU.get(typ) or bi._type_ru(typ),
                "label": label,
                "model": model,
                "step_code": NODE_TYPE_TO_STEP_CODE.get(typ, ""),
            }
        )
    return rows


def _score(row: dict[str, Any], words: set[str], stems: list[str]) -> int:
    codes = {str(row.get(name) or "").lower() for name in ("key", "type", "step_code")} - {""}
    if codes & words:
        return 100
    best = 0
    for name in (row.get("type_ru"), row.get("label")):
        need = _stems(str(name or ""))
        if need and all(any(_same(want, got) for got in stems) for want in need):
            best = max(best, len(need))
    return best


def match_nodes(rows: list[dict[str, Any]], text: str) -> list[str]:
    norm = _norm(text)
    words = set(_WORD.findall(norm))
    stems = _stems(norm)
    scored = [(_score(row, words, stems), str(row["key"])) for row in rows]
    top = max((score for score, _key in scored), default=0)
    if top <= 0:
        return []
    return [key for score, key in scored if score == top]


def roster_text(rows: list[dict[str, Any]]) -> str:
    lines = ["Ноды проекта (ключ: тип, подпись, модель):"]
    for row in rows:
        parts = [f"{row['key']}: {row['type_ru']}"]
        if row.get("label") and row["label"] != row["type_ru"]:
            parts.append(f"подпись «{row['label']}»")
        if row.get("model"):
            parts.append(f"модель {row['model']}")
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


_PROMPT_WORDS = ("промт", "промпт", "prompt")
_VARIANT_OK = re.compile(r"^[A-Za-z0-9_.\-]+$")
_MAX_VARIANTS = 30
_MAX_PROMPT_NODES = 4
PROMPT_RULE = (
    "Чтобы поставить ноде другой промт, используй node.set_prompt_variant: variant бери "
    "только из списка вариантов этой ноды ниже (до двоеточия), человек называет промт по названию. "
    "slot_id не указывай: нода читает промт из слота main."
)


def prompt_variants_text(row: dict[str, Any]) -> str:
    from app.services import prompt_library as pl

    step = str(row.get("step_code") or row.get("type") or "")
    try:
        names = [n for n in pl.list_prompts(step) if _VARIANT_OK.match(n)]
        meta = pl.load_file_meta(step)
    except Exception:
        return ""
    if not names:
        return ""
    lines = [f"Варианты промта для ноды {row['key']} («{row.get('label') or row.get('type_ru')}»):"]
    for name in names[:_MAX_VARIANTS]:
        info = meta.get(name) if isinstance(meta.get(name), dict) else {}
        title = " ".join(str(info.get("title") or "").split())
        lines.append(f"- {name}: {title}" if title else f"- {name}")
    return "\n".join(lines)


def _price(option_id: str) -> tuple[float, ...] | None:
    from app.services import node_edit as ne

    return ne.price_points(ne.find_catalog_model(option_id))


def _cheapest(options: list[dict[str, Any]]) -> set[str]:
    priced = [(str(o["id"]), _price(str(o["id"]))) for o in options]
    priced = [(key, pts) for key, pts in priced if pts]
    return {
        key
        for key, pts in priced
        if all(
            len(other) == len(pts) and all(a <= b for a, b in zip(pts, other, strict=True))
            for _other_key, other in priced
        )
    }


def _order(option: dict[str, Any]) -> tuple[int, float]:
    pts = _price(str(option["id"]))
    return (0, sum(pts)) if pts else (1, 0.0)


def model_options_text(meta: Any, key: str) -> str:
    from app.services import node_edit as ne

    try:
        data = ne.model_options(meta, key)
    except Exception:
        return ""
    current = data.get("current") or {}
    options = [o for o in data.get("options") or [] if isinstance(o, dict) and o.get("id")]
    if not current.get("kind") or not options:
        return ""
    head = (
        f"Модели для ноды {key} («{current.get('label') or key}»), сейчас "
        f"{current.get('model_label') or current.get('model_id')} (id {current.get('model_id')})"
    )
    if current.get("forced_vibecode") or not current.get("applies", True):
        return head + ": смена модели на этой ноде ни на что не повлияет, такой шаг не планируй."
    cheapest = _cheapest(options)
    lines = [head + ":"]
    for option in sorted(options, key=_order)[:_MAX_OPTIONS]:
        cost = str(option.get("cost_vs_current") or "unknown")
        if option["id"] == current.get("model_id"):
            lines.append(f"- {option['id']} ({option['label']}): текущая, {option['price_ru']}")
            continue
        mark = _COST_RU.get(cost, cost) + (", самая дешёвая" if option["id"] in cheapest else "")
        action = _SET_ACTION.get(cost, "node.set_model_costly")
        lines.append(f"- {option['id']} ({option['label']}): {mark}, {option['price_ru']}; {action}")
    return "\n".join(lines)


async def _meta_from_db(project_id: int) -> Any:
    from app.models import Project
    from app.services.action_layer.builtin_insight import _sessions
    from app.services.canvas_graph import effective_project_meta

    async with _sessions()() as session:
        row = await session.get(Project, project_id)
        if row is None:
            return None
        meta, _source = await effective_project_meta(session, row)
        return meta


async def load_meta(project_id: int) -> Any:
    from app.orchestrator import app_ref
    from app.services.action_layer.builtin_group import _effective_project
    from app.services.action_layer.context import ActionContext

    app = app_ref.current()
    if app is not None:
        detail = await _effective_project(
            ActionContext(app=app, operator="system", read_only=True), int(project_id)
        )
        meta = (detail or {}).get("meta") if isinstance(detail, dict) else None
        if isinstance(meta, dict):
            return meta
    try:
        meta = await _meta_from_db(int(project_id))
    except Exception:
        logger.exception("orch project nodes: project {} meta not read", project_id)
        return None
    return meta if isinstance(meta, dict) else None


async def load_proposals(project_id: int) -> Any:
    from app.services.change_control import service

    data = await service.list_proposals(project_id=int(project_id), status="proposed", limit=_MAX_PROPOSALS)
    return [item for item in data.get("items") or [] if isinstance(item, dict) and item.get("can_apply")]


def proposals_text(items: list[dict[str, Any]]) -> str:
    lines = [PROPOSALS_HEAD]
    for item in items[:_MAX_PROPOSALS]:
        what = "промт ноды" if item.get("kind") == "prompt_text" else "настройка ноды"
        node = item.get("node_key") or ""
        when = str(item.get("created_at") or "")[:16].replace("T", " ")
        lines.append(
            f"- proposal_id={item.get('id')}, expected_digest={item.get('digest')}: {what} {node}"
            + (f", создано {when}" if when else "")
        )
    return "\n".join(lines) if len(lines) > 1 else ""


_BLOCK_WORDS = ("блокир", "неизвестн", "исход")
BLOCKED_HEAD = (
    "Блокировки неизвестного исхода в проекте (для media.release_unknown бери key и provider "
    "только отсюда, reason — слова человека):"
)


async def load_blocked(project_id: int) -> Any:
    from app.services.media_unknown import list_unknown

    return (await list_unknown(int(project_id))).get("blocked") or []


def blocked_text(items: list[dict[str, Any]]) -> str:
    lines = [BLOCKED_HEAD]
    lines.extend(f"- key={item.get('key')}, provider={item.get('provider')}" for item in items[:20])
    return "\n".join(lines) if len(lines) > 1 else ""


_UNDO_WORDS = ("откат", "откати", "верни прежн", "верни как было", "верни старый", "отмени правк")
_MAX_SNAPSHOTS = 10
SNAPSHOTS_HEAD = (
    "Снимки правок проекта, самый свежий первым (для history.rollback бери snapshot_id только "
    "отсюда; промт ноды — вид prompt_slot, модель ноды возвращает node.revert_model):"
)


async def load_snapshots(project_id: int) -> Any:
    from app.services.action_layer import store

    rows = await store.list_snapshots(int(project_id), limit=_MAX_SNAPSHOTS * 2)
    return [
        {"snapshot_id": r.id, "kind": r.kind, "target": r.target, "before": r.before, "after": r.after}
        for r in rows
        if r.restored_at is None and r.restorable
    ]


def snapshots_text(items: list[dict[str, Any]]) -> str:
    lines = [SNAPSHOTS_HEAD]
    for item in items[:_MAX_SNAPSHOTS]:
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        where = target.get("node_key") or target.get("step_code") or ""
        before = "по умолчанию" if item.get("before") is None else str(item.get("before"))[:60]
        lines.append(
            f"- snapshot_id={item.get('snapshot_id')}: {item.get('kind')} {where}, "
            f"было «{before}», стало «{str(item.get('after'))[:60]}»"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


async def load_frames(project_id: int) -> Any:
    from app.services.launch_inputs import project_frames

    return await project_frames(int(project_id))


_MAX_PROJECTS = 30
PROJECTS_HEAD = (
    "Проекты фабрики (project_id: название, статус). project_id бери только отсюда; "
    "цифры в названии проекта — это не его номер:"
)
ProjectsLoader = Callable[[], Awaitable[Any]]


# Заказчик зовёт script_frames_qc «Кадры» (так называется её отчёт), в каталоге другое название.
_GROUP_ALIASES = {"script_frames_qc": "«Кадры»"}


def groups_text(text: str) -> str:
    """Каталог групп нод для group.insert, когда просьба про группу."""
    if "групп" not in _norm(text):
        return ""
    try:
        from app.services.node_groups import list_node_groups

        items = list_node_groups()
    except Exception:
        logger.exception("orch node groups not read")
        return ""
    lines = ["Группы нод (group_id: название):"]
    for g in items:
        alias = _GROUP_ALIASES.get(str(g.get("id")))
        name = g.get("title") or g.get("name") or ""
        lines.append(f"- {g.get('id')}: {name}" + (f", её же зовут {alias}" if alias else ""))
    return "\n".join(lines) if len(lines) > 1 else ""


async def load_projects() -> Any:
    from sqlalchemy import select

    from app.models import Project
    from app.services.action_layer.builtin_insight import _sessions

    async with _sessions()() as session:
        rows = (
            await session.execute(
                select(Project.id, Project.title, Project.topic, Project.status)
                .order_by(Project.id.desc())
                .limit(_MAX_PROJECTS)
            )
        ).all()
    return [
        {
            "id": int(pid),
            "title": str(title or topic or "").strip(),
            "status": getattr(status, "value", str(status)),
        }
        for pid, title, topic, status in rows
    ]


def named_projects(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    norm = _norm(text)
    found = [
        item for item in items if len(_norm(item.get("title") or "")) >= 4 and _norm(item["title"]) in norm
    ]
    longest = max((len(item["title"]) for item in found), default=0)
    return [item for item in found if len(item["title"]) == longest]


_NUMBERED_RE = re.compile(r"проект\w*\s+((?:№?#?\d{1,5}(?:\s*(?:,|и)\s*)?)+)")


def numbered_projects(items: list[dict[str, Any]], text: str) -> list[int]:
    """«в проектах 10, 11 и 12» → [10, 11, 12]; только числа сразу после слова «проект»."""
    known = {int(item["id"]) for item in items}
    out: list[int] = []
    for group in _NUMBERED_RE.findall(_norm(text)):
        for raw in re.findall(r"\d{1,5}", group):
            if int(raw) in known and int(raw) not in out:
                out.append(int(raw))
    return out


def projects_text(items: list[dict[str, Any]], text: str) -> str:
    if not items:
        return ""
    lines = [PROJECTS_HEAD]
    lines.extend(f"- {item['id']}: «{item['title']}», {item['status']}" for item in items[:_MAX_PROJECTS])
    named = named_projects(items, text)
    if len(named) == 1:
        lines.append(f"В запросе назван проект «{named[0]['title']}»: это project_id={named[0]['id']}.")
    return "\n".join(lines)


def _task_project(task_id: Any) -> int | None:
    from app.orchestrator.agents.action_catalog import _task_project as lookup

    return lookup(task_id)


class ProjectNodes:
    def __init__(
        self,
        *,
        meta_of: MetaLoader | None = None,
        frames_of: FramesLoader | None = None,
        project_of: Callable[[Any], int | None] | None = None,
        proposals_of: ProposalsLoader | None = None,
        projects_of: ProjectsLoader | None = None,
        blocked_of: ProposalsLoader | None = None,
        snapshots_of: ProposalsLoader | None = None,
    ) -> None:
        self._projects_of = projects_of or load_projects
        self._blocked_of = blocked_of or load_blocked
        self._snapshots_of = snapshots_of or load_snapshots
        self._meta_of = meta_of or load_meta
        self._frames_of = frames_of or load_frames
        self._proposals_of = proposals_of or load_proposals
        self._project_of = project_of or _task_project

    async def _meta(self, project_id: int) -> Any:
        try:
            return await self._meta_of(int(project_id))
        except Exception:
            logger.exception("orch project nodes failed for project {}", project_id)
            return None

    async def rows(self, project_id: int) -> list[dict[str, Any]]:
        try:
            return roster(await self._meta(project_id))
        except Exception:
            logger.exception("orch project nodes failed for project {}", project_id)
            return []

    async def frames(self, project_id: int) -> int | None:
        try:
            found = await self._frames_of(int(project_id))
        except Exception:
            logger.exception("orch project frames failed for project {}", project_id)
            return None
        return found if isinstance(found, int) and not isinstance(found, bool) else None

    async def block(self, text: str, task_id: Any = None) -> str:
        project_id = self._project_of(task_id)
        if project_id is None:
            items = await self.project_items()
            head = projects_text(items, text)
            named = named_projects(items, text)
            ids = [int(named[0]["id"])] if len(named) == 1 else numbered_projects(items, text)
            if not ids:
                return head
            nodes = await self.project_block(ids[0], text)
            note = (
                f"Ноды и варианты промтов ниже — проекта {ids[0]}; у проектов {', '.join(map(str, ids))} "
                "граф одинаковый, ключи нод и variant те же."
                if len(ids) > 1
                else ""
            )
            status_of = {int(item["id"]): str(item.get("status") or "") for item in items}
            nexts = [await self.next_step(pid, status_of.get(pid, "")) for pid in ids]
            return "\n".join(part for part in (head, note, nodes, *nexts) if part)
        return await self.project_block(project_id, text)

    async def next_step(self, project_id: int, status: str) -> str:
        """Следующий шаг по графу проекта: step.run_step берёт этот step_code, а не линейное меню."""
        try:
            from app.services.action_layer.builtin_insight import _current_next, _pipeline

            meta = await self._meta(project_id)
            if not isinstance(meta, dict) or not status:
                return ""
            _cur, nxt = _current_next(_pipeline(status, meta), status)
        except Exception:
            logger.exception("orch next step failed for project {}", project_id)
            return ""
        if not nxt:
            return ""
        return (
            f"Проект {project_id} ({status}): следующий шаг по графу — «{nxt['title']}», "
            f"step_code={nxt['step']}."
        )

    async def project_block(self, project_id: int, text: str) -> str:
        lines: list[str] = []
        meta = await self._meta(project_id)
        try:
            rows = roster(meta)
        except Exception:
            logger.exception("orch project nodes failed for project {}", project_id)
            rows = []
        if rows:
            lines.extend(NODE_RULES)
            lines.append(roster_text(rows))
            hits = set(match_nodes(rows, text))
            if hits:
                found = ", ".join(f"{row['key']} ({row['type_ru']})" for row in rows if row["key"] in hits)
                lines.append(f"По словам запроса подходит: {found}.")
                keys = [row["key"] for row in rows if row["key"] in hits][:_MAX_MODEL_NODES]
                models = [item for item in (model_options_text(meta, key) for key in keys) if item]
                if models:
                    lines.append(MODEL_RULE)
                    lines.extend(models)
                if any(word in _norm(text) for word in _PROMPT_WORDS):
                    words, stems = set(_WORD.findall(_norm(text))), _stems(text)
                    named = [row for row in rows if _score(row, words, stems) > 0][:_MAX_PROMPT_NODES]
                    variants = [item for item in (prompt_variants_text(row) for row in named) if item]
                    if variants:
                        lines.append(PROMPT_RULE)
                        lines.extend(variants)
        if await self.frames(project_id) == 0:
            lines.append(NO_FRAMES_RULE)
        pending = await self.proposals(project_id, text)
        if pending:
            lines.append(pending)
        blocked = await self.blocked(project_id, text)
        if blocked:
            lines.append(blocked)
        undo = await self.snapshots(project_id, text)
        if undo:
            lines.append(undo)
        return "\n".join(lines)

    async def snapshots(self, project_id: int, text: str) -> str:
        if not any(stem in _norm(text) for stem in _UNDO_WORDS):
            return ""
        try:
            items = await self._snapshots_of(int(project_id))
        except Exception:
            logger.exception("orch project snapshots failed for project {}", project_id)
            return ""
        return snapshots_text([item for item in items or [] if isinstance(item, dict)])

    async def blocked(self, project_id: int, text: str) -> str:
        if not any(stem in _norm(text) for stem in _BLOCK_WORDS):
            return ""
        try:
            items = await self._blocked_of(int(project_id))
        except Exception:
            logger.exception("orch project blocked outcomes failed for project {}", project_id)
            return ""
        return blocked_text([item for item in items or [] if isinstance(item, dict)])

    async def project_items(self) -> list[dict[str, Any]]:
        try:
            items = await self._projects_of()
        except Exception:
            logger.exception("orch factory projects not read")
            return []
        return [item for item in items or [] if isinstance(item, dict)]

    async def proposals(self, project_id: int, text: str) -> str:
        if not any(stem in _norm(text) for stem in _APPLY_WORDS):
            return ""
        try:
            items = await self._proposals_of(int(project_id))
        except Exception:
            logger.exception("orch project proposals failed for project {}", project_id)
            return ""
        return proposals_text([item for item in items or [] if isinstance(item, dict)])

    async def __call__(self, text: str, task_id: Any = None) -> str:
        block = await self.block(text, task_id)
        groups = groups_text(text)
        return "\n".join(part for part in (block, groups) if part)
