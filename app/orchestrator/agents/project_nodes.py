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
MANY_RULE = (
    "Одна модель или один промт для ноды во всех этих проектах — один шаг node.set_model_many или "
    "node.set_prompt_variant_many с project_ids всех этих проектов, а не шаг на каждый проект; "
    "model_id и variant бери из списков ниже. Вернуть прежнюю модель ноды во всех этих проектах — "
    "один шаг node.revert_model_many."
)
FINISH_RULE = (
    "Путь до готового ролика - вторая половина графа, каждый шаг отдельным step.run_step: озвучка "
    "(step_code=audio), музыка (music), план звуков (sfx_plan), звуки (sfx_gen), сборка (assemble); "
    "готовый mp4 - статус assembled. Запускай по одному шагу, следующий бери из строки «следующий "
    "шаг по графу», а не весь путь сразу. Цену шага сначала узнай step.get_run_cost_basis: озвучка и "
    "звуки - sound_credits оттуда, музыка - нужен потолок ceiling_credits не ниже цены трека. Своя "
    "озвучка или музыка с компьютера - montage.upload_voice / montage.upload_music, тогда шаг их не "
    "генерирует. Пересобрать собранный ролик без новых генераций - montage.run_montage, настройки "
    "сборки (громкость музыки, субтитры) - montage.set_assemble_params. Публикация - только "
    "publish.send и только после статуса assembled."
)
_FINISH_WORDS = (
    "озвуч",
    "музык",
    "звук",
    "собер",
    "сборк",
    "смонтир",
    "монтаж",
    "финальн",
    "mp4",
    "до конца",
    "до готов",
    "довед",
    "опублик",
    "публикац",
)
# «Запусти следующий шаг» во вкладке проекта: без строки «следующий шаг по графу» план
# сводился к чтению статуса, и карточки запуска не было (во «Всей фабрике» строка есть всегда).
# Без «дальше»/«продолжи»: в «запусти ноду X и дальше до Y» строка спорила с правилом цепочки.
_NEXT_WORDS = ("следующ", "очередн")
_MAX_MODEL_NODES = 2
_MAX_CHAIN_NODES = 2
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
    "proposal_id и expected_digest только отсюда; отменить неприменённое — change.reject с тем же "
    "proposal_id):"
)
ALL_PROPOSALS_HEAD = (
    "Ожидающие предложения изменений этого чата (или всей студии, если своих нет), самое свежее "
    "первым. Для change.apply бери proposal_id и expected_digest только отсюда; одно предложение — "
    "его и применяй, несколько и человек не назвал проект или ноду — пустой steps и в reason "
    "вопрос, какое (нода и проект каждого); отменить неприменённое — change.reject:"
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
    from app.services.action_layer.builtin_group import prompt_step_for

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
                "prompt_step": prompt_step_for(raw) or "",
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
_MAX_VARIANTS = 30
_MAX_PROMPT_NODES = 4
PROMPT_RULE = (
    "Чтобы поставить ноде другой промт, используй node.set_prompt_variant: variant бери "
    "только из списка вариантов этой ноды ниже (до двоеточия) и пиши имя точно как там, с "
    "пробелами, кириллицей и .txt, если они есть; человек называет промт по названию. "
    "slot_id не указывай: нода читает промт из слота main. Стиль персонажа ноды героя — "
    "отдельный список, для него slot_id=style."
)


def chains_text(meta: Any, rows: list[dict[str, Any]], keys: list[str], text: str) -> list[str]:
    """Цепочка нод GPT, которую Студия пройдёт сама после запущенной: из неё stop_after_node_key."""
    from app.orchestrator.agents.action_catalog import wants_launch
    from app.services.action_layer.builtin_launch import gpt_chain

    if not wants_launch(text):
        return []
    labels = {str(row["key"]): str(row.get("label") or row.get("type_ru") or "") for row in rows}
    gpt = [key for key in keys if any(row["key"] == key and row["type"] == "excel_gpt" for row in rows)]
    out: list[str] = []
    for key in gpt[:_MAX_CHAIN_NODES]:
        chain = gpt_chain(meta, key)
        path = " → ".join(f"{node} «{labels.get(node, node)}»" for node in chain)
        out.append(
            f"После запуска {key} Студия сама пройдёт по стрелкам: {path}; дальше цепочка нод GPT "
            "встаёт. Остановиться раньше — stop_after_node_key."
        )
    return out


def _variants_lines(meta: Any, key: str, step: str, head: str, sd_first: bool = False) -> list[str]:
    from app.services import prompt_library as pl
    from app.services.action_layer.builtin_group import library_names

    try:
        names = [
            n
            for n in library_names(meta if isinstance(meta, dict) else {}, key, step)
            if pl.is_valid_prompt_name(n)
        ]
        titles = pl.load_file_meta(step)
    except Exception:
        logger.exception("orch prompt variants failed for step {}", step)
        return []
    if sd_first:
        # Агент дизайна сцен — нода «Работа с GPT» с меткой: свои промты sd_* показываем первыми.
        names.sort(key=lambda n: not n.startswith("sd_"))
    if not names:
        return []
    lines = [head]
    for name in names[:_MAX_VARIANTS]:
        info = titles.get(name) if isinstance(titles.get(name), dict) else {}
        title = " ".join(str(info.get("title") or "").split())
        lines.append(f"- {name}: {title}" if title else f"- {name}")
    if len(names) > _MAX_VARIANTS:
        lines.append(f"(и ещё {len(names) - _MAX_VARIANTS})")
    return lines


def prompt_variants_text(row: dict[str, Any], meta: Any = None) -> str:
    from app.services.action_layer.builtin_group import HERO_STYLE_SLOT, prompt_step_for

    step = str(row.get("prompt_step") or "")
    if not step:
        return ""
    name = row.get("label") or row.get("type_ru")
    key = row["key"]
    lines = _variants_lines(
        meta,
        key,
        step,
        f"Варианты промта для ноды {key} («{name}»):",
        sd_first=row.get("type") in _SCENE_AGENT_TYPES,
    )
    style = prompt_step_for(str(row.get("type") or ""), HERO_STYLE_SLOT)
    if style and style != step:
        lines += _variants_lines(
            meta, key, style, f"Стили персонажа для ноды {key} («{name}», slot_id={HERO_STYLE_SLOT}):"
        )
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


def _previous_model(meta: Any, key: str) -> str | None:
    from app.services import node_edit as ne

    try:
        target = ne.plan_model_revert(meta, key).get("target_model_id")
    except Exception:
        return None
    return ne.canonical_model_id(target) if target else None


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
    previous = _previous_model(meta, key)
    lines = [head + ":"]
    for option in sorted(options, key=_order)[:_MAX_OPTIONS]:
        cost = str(option.get("cost_vs_current") or "unknown")
        if option["id"] == current.get("model_id"):
            lines.append(f"- {option['id']} ({option['label']}): текущая, {option['price_ru']}")
            continue
        mark = _COST_RU.get(cost, cost) + (", самая дешёвая" if option["id"] in cheapest else "")
        action = _SET_ACTION.get(cost, "node.set_model_costly")
        if previous is not None and ne.canonical_model_id(option["id"]) == previous:
            # Вернуть то, что стояло до правки, — откат, но только если цена не растёт:
            # node.revert_model идёт без карточки, а «дороже» человек подтверждает сам.
            if action != "node.set_model_costly":
                action = f"вернуть прежнюю по просьбе человека — node.revert_model, иначе {action}"
            mark = "прежняя модель этой ноды, " + mark
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


async def load_proposals(project_id: int | None, operators: list[str] | None = None) -> Any:
    """Ожидающие применения предложения проекта; без проекта — этой беседы, иначе все."""
    from app.services.change_control import service

    if project_id is None:
        from app.services.action_layer.chat_proposals import pending

        return (await pending(operators))[:_MAX_PROPOSALS]
    data = await service.list_proposals(project_id=int(project_id), status="proposed", limit=_MAX_PROPOSALS)
    return [item for item in data.get("items") or [] if isinstance(item, dict) and item.get("can_apply")]


def proposals_text(items: list[dict[str, Any]], head: str = PROPOSALS_HEAD) -> str:
    lines = [head]
    for item in items[:_MAX_PROPOSALS]:
        what = "промт ноды" if item.get("kind") == "prompt_text" else "настройка ноды"
        node = item.get("node_key") or ""
        where = f", проект {item['project_id']}" if item.get("project_id") else ""
        when = str(item.get("created_at") or "")[:16].replace("T", " ")
        lines.append(
            f"- proposal_id={item.get('id')}, expected_digest={item.get('digest')}: {what} {node}{where}"
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
    "отсюда; промт ноды — вид prompt_slot, модель ноды — node_model, настройки ноды — "
    "node_params, настройки проекта (в том числе модель и разрешение картинок и видео) — "
    "project_settings, агент проверки ноды — check_agent, его возвращает "
    "node.revert_check_agent):"
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
        if item.get("kind") == "check_agent":
            from app.services.action_layer.builtin_wave5_history import agent_state_ru

            before, after = agent_state_ru(item.get("before")), agent_state_ru(item.get("after"))
        else:
            before = "по умолчанию" if item.get("before") is None else str(item.get("before"))[:60]
            after = str(item.get("after"))[:60]
        lines.append(
            f"- snapshot_id={item.get('snapshot_id')}: {item.get('kind')} {where}, "
            f"было «{before}», стало «{after}»"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


_SCENE_AGENT_TYPES = frozenset({"sd_agent", "sd_assemble"})
_AGENT_WORDS = ("агент", "проверк", "проверяющ", "вердикт", "мышлен", "рассужд", "effort", "reasoning")
_MAX_AGENT_NODES = 8
AGENTS_RULE = (
    "Агенты нод и как их менять. Агент проверки ноды «Работа с GPT»: поставить новый — "
    "node.upload_check_agent, нужен прикреплённый файл .txt или .md (файла нет — спроси, какой "
    "прикрепить, текст агента в params не пиши); вернуть встроенного — node.clear_check_agent; "
    "вернуть прежнего после смены — node.revert_check_agent; включить или выключить проверку и "
    "режим «чинить» — node.set_params (check_mode, check_fix). Роль ноды (проверка, сравнение, "
    "шлагбаум) — node.set_config role. Агент дизайна сцен — это вариант промта его ноды: "
    "node.set_prompt_variant, variant из списка вариантов этой ноды. Уровень рассуждений "
    "текстовой модели (low, medium, high): понизить — node.set_params reasoning_effort, повысить — "
    "node.set_params_costly, вернуть — node.revert_params; если ниже сказано «не действует», такой "
    "шаг не планируй. Шаблон GPT-проверки «Вердикт» у шага выбирается только в Студии, из чата он "
    "не меняется — так и ответь."
)


def _agent_line(meta: dict, row: dict[str, Any]) -> str:
    from app.services import node_edit as ne
    from app.services.excel_gpt_node import sd_agent_marker

    key = str(row["key"])
    node = ne.find_node(meta, key)
    if node is None:
        return ""
    name = row.get("label") or row.get("type_ru") or key
    parts: list[str] = []
    typ = str(row.get("type") or "")
    if typ in _SCENE_AGENT_TYPES:
        marker = sd_agent_marker(node) or "agent"
        slots = meta.get("prompt_slot_variants") if isinstance(meta.get("prompt_slot_variants"), dict) else {}
        slot = slots.get(key) if isinstance(slots.get(key), dict) else {}
        variant = str(slot.get("main") or "").strip()
        parts.append(
            f"агент дизайна сцен «{marker}», промт "
            + (f"«{variant}»" if variant else f"по умолчанию (sd_{marker})")
        )
    elif typ == "excel_gpt":
        cfg = ne._operator_cfg(meta, key)
        own = str(cfg.get("checkAgentFileName") or "").strip()
        parts.append(
            "агент проверки "
            + (f"свой «{own}»" if own else "встроенный")
            + (", проверка включена" if cfg.get("checkMode") else ", проверка выключена")
            + f", роль {cfg.get('role') or 'assist'}"
        )
    if ne.model_kind_for_type(typ) == ne.KIND_TEXT:
        ok, why = ne.effort_applies(meta, node)
        effort = ne.node_effort(node)
        parts.append(
            f"уровень рассуждений {ne.EFFORT_RU.get(effort, effort)}"
            + ("" if ok else f" (не действует: {why})")
        )
    if not parts:
        return ""
    return f"- {key} («{name}»): " + "; ".join(parts) + "."


def agents_text(meta: Any, rows: list[dict[str, Any]], hits: set[str]) -> list[str]:
    """Какие агенты стоят на нодах и что из них меняется из чата."""
    if not isinstance(meta, dict):
        return []
    picked = [row for row in rows if row["key"] in hits] or [
        row for row in rows if row.get("type") in (*_SCENE_AGENT_TYPES, "excel_gpt")
    ]
    lines: list[str] = []
    for row in picked[:_MAX_AGENT_NODES]:
        try:
            line = _agent_line(meta, row)
        except Exception:
            logger.exception("orch agent line failed for node {}", row.get("key"))
            continue
        if line:
            lines.append(line)
    if not lines:
        return []
    out = [AGENTS_RULE, "Агенты и уровень рассуждений нод:", *lines]
    scene = [row for row in picked if row.get("type") in _SCENE_AGENT_TYPES][:_MAX_PROMPT_NODES]
    out.extend(item for item in (prompt_variants_text(row, meta) for row in scene) if item)
    return out


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


# Диапазон: «10-14» или «с 10 по 14»; голое «12 до 20» - это не проекты («в проекте 5 до 10 кадров»).
_NUMBERED_RE = re.compile(
    r"проект\w*\s+((?:(?:с\s+№?#?\d{1,5}\s*(?:по|до)\s*№?#?\d{1,5}"
    r"|№?#?\d{1,5}(?:\s*[-–—]\s*№?#?\d{1,5})?)(?:\s*(?:,|и)\s*)?)+)"
)
_RANGE_RE = re.compile(r"(\d{1,5})\s*(?:-|–|—|по|до)\s*№?#?(\d{1,5})|(\d{1,5})")
_MAX_RANGE = 50


def numbered_projects(items: list[dict[str, Any]], text: str) -> list[int]:
    """«в проектах 10, 11 и 12», «10-14», «с 10 по 14» → номера; только сразу после слова «проект»."""
    known = {int(item["id"]) for item in items}
    out: list[int] = []
    for group in _NUMBERED_RE.findall(_norm(text)):
        for first, last, single in _RANGE_RE.findall(group):
            lo, hi = (int(first), int(last)) if first else (int(single), int(single))
            if not lo <= hi <= lo + _MAX_RANGE:
                hi = lo  # «в проектах 12–10» - не диапазон проектов
            out.extend(pid for pid in range(lo, hi + 1) if pid in known and pid not in out)
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


def finish_words(text: str) -> bool:
    low = _norm(text)
    return any(word in low for word in _FINISH_WORDS)


async def load_status(project_id: int) -> str:
    from sqlalchemy import select

    from app.models import Project
    from app.services.action_layer.builtin_insight import _sessions

    async with _sessions()() as session:
        status = await session.scalar(select(Project.status).where(Project.id == int(project_id)))
    return getattr(status, "value", str(status or ""))


async def _task_operators(task_id: Any) -> list[str] | None:
    """Задачи беседы, к которой относится задача: их предложения — «свои»."""
    import asyncio

    from app.services.action_layer.chat_undo import task_operators

    if task_id is None:
        return None
    try:
        return await asyncio.to_thread(task_operators, task_id)
    except Exception:
        logger.exception("orch project proposals: chat operators not read")
        return None


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
        status_of: Callable[[int], Awaitable[str]] | None = None,
    ) -> None:
        self._status_of = status_of or load_status
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
                # «Примени правку промта» без номера проекта: ожидающие правки беседы и студии.
                pending = await self.proposals(None, text, task_id)
                return "\n".join(part for part in (head, pending) if part)
            nodes = await self.project_block(ids[0], text)
            note = (
                f"Ноды и варианты промтов ниже — проекта {ids[0]}; у проектов {', '.join(map(str, ids))} "
                "граф одинаковый, ключи нод и variant те же. " + MANY_RULE
                if len(ids) > 1
                else ""
            )
            status_of = {int(item["id"]): str(item.get("status") or "") for item in items}
            nexts = [await self.next_step(pid, status_of.get(pid, "")) for pid in ids]
            return "\n".join(part for part in (head, note, nodes, *nexts) if part)
        block = await self.project_block(project_id, text)
        if _NUMBERED_RE.search(_norm(text)):
            ids = numbered_projects(await self.project_items(), text)
            if len(ids) > 1:
                many = f"В запросе проекты {', '.join(map(str, ids))}, граф у них тот же. " + MANY_RULE
                block = "\n".join(part for part in (block, many) if part)
        if not finish_words(text) and not any(word in _norm(text) for word in _NEXT_WORDS):
            return block
        try:
            status = await self._status_of(int(project_id))
        except Exception:
            logger.exception("orch project status failed for project {}", project_id)
            status = ""
        nxt = await self.next_step(project_id, str(status or ""))
        return "\n".join(part for part in (block, nxt) if part)

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
                lines.extend(
                    chains_text(meta, rows, [row["key"] for row in rows if row["key"] in hits], text)
                )
                keys = [row["key"] for row in rows if row["key"] in hits][:_MAX_MODEL_NODES]
                models = [item for item in (model_options_text(meta, key) for key in keys) if item]
                if models:
                    lines.append(MODEL_RULE)
                    lines.extend(models)
                if any(word in _norm(text) for word in _PROMPT_WORDS):
                    words, stems = set(_WORD.findall(_norm(text))), _stems(text)
                    named = [row for row in rows if _score(row, words, stems) > 0][:_MAX_PROMPT_NODES]
                    variants = [item for item in (prompt_variants_text(row, meta) for row in named) if item]
                    if variants:
                        lines.append(PROMPT_RULE)
                        lines.extend(variants)
            if any(word in _norm(text) for word in _AGENT_WORDS):
                lines.extend(agents_text(meta, rows, hits))
        if finish_words(text):
            lines.append(FINISH_RULE)
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

    async def proposals(self, project_id: int | None, text: str, task_id: Any = None) -> str:
        if not any(stem in _norm(text) for stem in _APPLY_WORDS):
            return ""
        try:
            if project_id is None:
                own = self._proposals_of is load_proposals
                items = await (
                    self._proposals_of(None, await _task_operators(task_id))
                    if own
                    else self._proposals_of(None)
                )
            else:
                items = await self._proposals_of(int(project_id))
        except Exception:
            logger.exception("orch project proposals failed for project {}", project_id)
            return ""
        head = ALL_PROPOSALS_HEAD if project_id is None else PROPOSALS_HEAD
        return proposals_text([item for item in items or [] if isinstance(item, dict)], head)

    async def __call__(self, text: str, task_id: Any = None) -> str:
        block = await self.block(text, task_id)
        groups = groups_text(text)
        return "\n".join(part for part in (block, groups) if part)
