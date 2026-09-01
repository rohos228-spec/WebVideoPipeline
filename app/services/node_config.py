"""Конфигурация узла графа — одно место вместо трёх.

**Что было сломано.** Конфиг одного узла лежал в трёх независимых
хранилищах: модель — в ``node.data.modelId`` (внутри графа), привязка
промта — в ``meta.prompt_slot_variants[node_id]`` (рядом с графом),
настройки «Работы с GPT» — в ``meta.excel_gpt_nodes[node_id]`` (там же).
Живой прогон 2026-08-31 (находка 12) показал цену разнесённости: удалил
узел — привязка промта осталась висеть в ``meta`` и досталась следующему
узлу с тем же id; скопировал узел — привязка не поехала, потому что
копируется ``data``, а не ``meta``; узел, вставший в чужой слот, подхватил
чужой промт (это отдельно закрыто в находке 14, но корень — тот же).

**Что здесь.** Контейнер ``node.data.config`` и единственный слой доступа к
нему. Форма::

    node["data"]["config"] = {
        "modelId": "gpt-5.6-sol",              # модель узла
        "promptSlots": {"main": "sd_action_vlog"},  # привязка промта по слотам
        "excelGpt": {...},                     # конфиг «Работы с GPT»
    }

Правила, которые здесь держатся:

1. **Чтение** — ``data.config`` имеет приоритет; ключа нет — читается старое
   место. Приоритет **по ключу**, а не по всему контейнеру: узел с
   ``config.modelId`` и без ``config.promptSlots`` берёт промт из
   ``meta.prompt_slot_variants``. Иначе первая же миграция одного ключа
   молча обнулила бы остальные.
2. **Запись** — идёт в ``data.config`` **и зеркалится в старое место**.
   Зеркало нужно не ради отката: ``meta.excel_gpt_nodes`` читают напрямую
   ещё десяток модулей (``gpt_operator``, ``storage_node``,
   ``node_xlsx_snapshot``, ``project_ops``, ``mass_factory``,
   ``project_child``), а ``data.modelId`` пишет инспектор узла во фронте.
   Пока они не переведены, односторонняя запись означала бы расхождение
   правды, то есть ровно тот дефект, который чинится.
3. **Миграция** (:func:`migrate_graph_configs`) — чистая функция, зовётся
   **явно** при сохранении графа, а не при каждом чтении: молчаливая
   миграция на чтении означала бы, что состояние проекта меняется от
   опроса UI.

**Чего здесь намеренно нет.** ``excelGpt`` миграцией не заполняется:
``meta.excel_gpt_nodes`` держит не только конфиг, но и состояние прогона
(``lastReplyPath``, ``lastReplyAt``, ``uploadedFileNames``), а состояние
прогона в графе — это мусор в ``graph_diff`` и ложные срабатывания
планировщика сброса. Читающая сторона к переезду готова
(:func:`excel_gpt_config_for_node`), но хранилище остаётся старым, пока
конфиг не отделён от состояния. Симптомы находки 12 для «Работы с GPT»
закрывает :func:`prune_configs_for_removed_nodes`.
"""

from __future__ import annotations

from typing import Any

#: Ключ контейнера внутри ``node.data``.
NODE_CONFIG_KEY = "config"

#: Ключи внутри контейнера.
CONFIG_MODEL_ID = "modelId"
CONFIG_PROMPT_SLOTS = "promptSlots"
CONFIG_EXCEL_GPT = "excelGpt"

#: Старые хранилища в ``meta``, привязанные к id узла. Порядок важен только
#: для читаемости логов.
LEGACY_NODE_BUCKETS: tuple[str, ...] = ("prompt_slot_variants", "excel_gpt_nodes")


# ── Доступ к узлу ────────────────────────────────────────────────────────


def node_data(node: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def config_of(node: dict[str, Any] | None) -> dict[str, Any]:
    """``node.data.config`` как словарь (пустой, если контейнера нет)."""
    cfg = node_data(node).get(NODE_CONFIG_KEY)
    return cfg if isinstance(cfg, dict) else {}


def graph_nodes(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Узлы ``meta.canvas_graph`` (пусто, если графа нет)."""
    if not isinstance(meta, dict):
        return []
    raw = meta.get("canvas_graph")
    if not isinstance(raw, dict):
        return []
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [n for n in nodes if isinstance(n, dict)]


def find_node(meta: dict[str, Any] | None, node_id: str | None) -> dict[str, Any] | None:
    key = str(node_id or "").strip()
    if not key:
        return None
    for n in graph_nodes(meta):
        if str(n.get("id") or "").strip() == key:
            return n
    return None


# ── Чтение: новое место важнее старого ───────────────────────────────────


def model_id_of(node: dict[str, Any] | None) -> str | None:
    """Модель узла: ``config.modelId``, иначе ``data.modelId``/``model_id``."""
    cfg = config_of(node)
    raw = str(cfg.get(CONFIG_MODEL_ID) or "").strip()
    if raw:
        return raw
    data = node_data(node)
    legacy = str(data.get("modelId") or data.get("model_id") or "").strip()
    return legacy or None


def _clean_slots(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for slot_id, name in raw.items():
        text = str(name or "").strip()
        if text:
            out[str(slot_id)] = text
    return out


def legacy_prompt_slots(meta: dict[str, Any] | None, node_id: str) -> dict[str, str]:
    """``meta.prompt_slot_variants[node_id]`` как чистый словарь слотов."""
    if not isinstance(meta, dict):
        return {}
    bucket = meta.get("prompt_slot_variants")
    if not isinstance(bucket, dict):
        return {}
    return _clean_slots(bucket.get(str(node_id)))


def prompt_slots_of(node: dict[str, Any] | None, meta: dict[str, Any] | None) -> dict[str, str]:
    """Привязки промта узла: ``config.promptSlots``, иначе старое место.

    Ключ ``promptSlots`` считается авторитетным по факту наличия, даже если
    он пустой: иначе снятую привязку было бы нечем выразить — пустой словарь
    молча откатывался бы к старому значению из ``meta``.
    """
    cfg = config_of(node)
    if CONFIG_PROMPT_SLOTS in cfg:
        return _clean_slots(cfg.get(CONFIG_PROMPT_SLOTS))
    return legacy_prompt_slots(meta, str((node or {}).get("id") or ""))


def prompt_slots_for_node(meta: dict[str, Any] | None, node_id: str | None) -> dict[str, str]:
    """То же для случая, когда на руках только meta и id узла."""
    key = str(node_id or "").strip()
    if not key:
        return {}
    node = find_node(meta, key)
    if node is None:
        # Узла в графе нет: метаданные времён Node Studio. Раньше они
        # читались как обычные привязки — так и оставляем.
        return legacy_prompt_slots(meta, key)
    return prompt_slots_of(node, meta)


def all_prompt_slots(meta: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """node_id → слоты, по всем узлам графа плюс осиротевшие записи meta.

    Узлы графа берутся через :func:`prompt_slots_of` (новое место важнее),
    записи ``meta`` без узла в графе — как есть.
    """
    out: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for node in graph_nodes(meta):
        nid = str(node.get("id") or "").strip()
        if not nid:
            continue
        seen.add(nid)
        slots = prompt_slots_of(node, meta)
        if slots:
            out[nid] = slots
    if isinstance(meta, dict):
        bucket = meta.get("prompt_slot_variants")
        if isinstance(bucket, dict):
            for nid, raw in bucket.items():
                key = str(nid)
                if key in seen:
                    continue
                slots = _clean_slots(raw)
                if slots:
                    out[key] = slots
    return out


def legacy_excel_gpt_config(meta: dict[str, Any] | None, node_id: str) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    bucket = meta.get("excel_gpt_nodes")
    if not isinstance(bucket, dict):
        return {}
    cfg = bucket.get(str(node_id))
    return dict(cfg) if isinstance(cfg, dict) else {}


def excel_gpt_config_for_node(meta: dict[str, Any] | None, node_id: str | None) -> dict[str, Any]:
    """Конфиг «Работы с GPT»: ``config.excelGpt``, иначе ``meta.excel_gpt_nodes``."""
    key = str(node_id or "").strip()
    if not key:
        return {}
    node = find_node(meta, key)
    cfg = config_of(node)
    if CONFIG_EXCEL_GPT in cfg:
        raw = cfg.get(CONFIG_EXCEL_GPT)
        return dict(raw) if isinstance(raw, dict) else {}
    return legacy_excel_gpt_config(meta, key)


# ── Миграция: чистая функция, зовётся явно ───────────────────────────────


def build_node_config(node: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    """Собрать ``data.config`` одного узла из графа и старых мест.

    Чистая: ничего не пишет, читает только переданные структуры.

    ``modelId`` берётся с самого узла и **перебивает** уже записанный
    ``config.modelId``. Инспектор узла во фронте по-прежнему пишет
    ``data.modelId``; если бы побеждал контейнер, выбор модели, сделанный
    после миграции, молча не применялся бы. Оба поля живут в ``data`` и
    едут с узлом при копировании, поэтому расхождения между ними здесь и
    сводятся — при каждом сохранении графа.
    """
    out = dict(config_of(node))
    data = node_data(node)
    node_id = str(node.get("id") or "").strip()

    legacy_model = str(data.get("modelId") or data.get("model_id") or "").strip()
    if legacy_model:
        out[CONFIG_MODEL_ID] = legacy_model
    elif not str(out.get(CONFIG_MODEL_ID) or "").strip():
        out.pop(CONFIG_MODEL_ID, None)

    if CONFIG_PROMPT_SLOTS not in out:
        slots = legacy_prompt_slots(meta, node_id)
        if slots:
            out[CONFIG_PROMPT_SLOTS] = slots
    return out


def migrate_graph_configs(
    nodes: list[dict[str, Any]],
    meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Проставить ``data.config`` каждому узлу графа. Чистая функция.

    Возвращает новый список узлов; входной не меняется. Пустой конфиг не
    записывается — узел без модели и без промта остаётся как был, чтобы
    миграция не раздувала граф и не шумела в ``graph_diff``.
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            out.append(node)
            continue
        cfg = build_node_config(node, meta)
        data = dict(node_data(node))
        if cfg:
            data[NODE_CONFIG_KEY] = cfg
        else:
            data.pop(NODE_CONFIG_KEY, None)
        out.append({**node, "data": data})
    return out


# ── Запись: новое место + зеркало в старое ───────────────────────────────


def _write_node_data(meta: dict[str, Any], node_id: str, data: dict[str, Any]) -> bool:
    """Заменить ``data`` узла в ``meta.canvas_graph`` (копией, не на месте)."""
    raw = meta.get("canvas_graph")
    if not isinstance(raw, dict):
        return False
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return False
    key = str(node_id)
    new_nodes: list[Any] = []
    hit = False
    for n in nodes:
        if isinstance(n, dict) and str(n.get("id") or "").strip() == key:
            new_nodes.append({**n, "data": data})
            hit = True
        else:
            new_nodes.append(n)
    if not hit:
        return False
    meta["canvas_graph"] = {**raw, "nodes": new_nodes}
    return True


def set_prompt_slot(
    meta: dict[str, Any],
    node_id: str,
    slot_id: str,
    variant: str | None,
) -> bool:
    """Назначить узлу вариант промта: в ``data.config`` и в старое место.

    ``variant=None`` или пустая строка снимают привязку. Возвращает True,
    если что-то записалось (узел найден в графе либо запись легла в meta).
    """
    key = str(node_id or "").strip()
    slot = str(slot_id or "").strip() or "main"
    if not key:
        return False
    name = str(variant or "").strip()

    node = find_node(meta, key)
    wrote = False
    if node is not None:
        slots = dict(prompt_slots_of(node, meta))
        if name:
            slots[slot] = name
        else:
            slots.pop(slot, None)
        data = dict(node_data(node))
        cfg = dict(config_of(node))
        cfg[CONFIG_PROMPT_SLOTS] = slots
        data[NODE_CONFIG_KEY] = cfg
        wrote = _write_node_data(meta, key, data)

    # Зеркало: ``meta.prompt_slot_variants`` читают ещё и группы узлов,
    # фабрика массовых роликов и наследование ребёнком.
    bucket = meta.get("prompt_slot_variants")
    bucket = dict(bucket) if isinstance(bucket, dict) else {}
    slots_legacy = dict(_clean_slots(bucket.get(key)))
    if name:
        slots_legacy[slot] = name
    else:
        slots_legacy.pop(slot, None)
    if slots_legacy:
        bucket[key] = slots_legacy
    else:
        bucket.pop(key, None)
    meta["prompt_slot_variants"] = bucket
    return wrote or True


def sync_prompt_slots_into_graph(meta: dict[str, Any], node_ids: list[str] | None = None) -> list[str]:
    """Перенести записи ``meta.prompt_slot_variants`` в ``data.config`` узлов.

    Нужна там, где привязка приходит патчем в ``meta`` (инспектор узла шлёт
    ``PATCH /projects/{id}``), а не через :func:`set_prompt_slot`. Без этого
    новое место осталось бы со старым значением и перебивало бы свежий
    выбор человека — сегодняшний дефект наизнанку.

    Вставка группы узлов (`node_groups.insert_node_group`) пишет `meta`
    напрямую, минуя этот функнель: её узлы получают контейнер на ближайшем
    сохранении графа. До того чтение честно падает в старое место — правды
    это не меняет, меняет только момент переезда.

    Возвращает id узлов, у которых контейнер изменился.
    """
    bucket = meta.get("prompt_slot_variants")
    if not isinstance(bucket, dict):
        return []
    wanted = {str(i) for i in node_ids} if node_ids is not None else None
    changed: list[str] = []
    for node in graph_nodes(meta):
        nid = str(node.get("id") or "").strip()
        if not nid or nid not in bucket:
            continue
        if wanted is not None and nid not in wanted:
            continue
        slots = _clean_slots(bucket.get(nid))
        if prompt_slots_of(node, meta) == slots and CONFIG_PROMPT_SLOTS in config_of(node):
            continue
        data = dict(node_data(node))
        cfg = dict(config_of(node))
        cfg[CONFIG_PROMPT_SLOTS] = slots
        data[NODE_CONFIG_KEY] = cfg
        if _write_node_data(meta, nid, data):
            changed.append(nid)
    return changed


# ── Удаление узла уносит его конфиг ──────────────────────────────────────


def prune_configs_for_removed_nodes(meta: dict[str, Any], removed_ids: list[str]) -> list[str]:
    """Убрать из старых хранилищ конфиги узлов, которых больше нет в графе.

    Ради этого переезд и делался: ``node.data.config`` умирает вместе с
    узлом сам, а вот ``meta.prompt_slot_variants`` и ``meta.excel_gpt_nodes``
    переживали удаление и доставались следующему узлу с тем же id (находка
    12 живого прогона). ``PATCH /projects/{id}`` вычистить их не может:
    оба бакета в ``PROTECTED_META_BUCKETS`` и сливаются вглубь, то есть
    удаление ключа через патч не проходит по построению.

    Сносятся **только** переданные id — те, что были в графе и из него
    ушли. Записи, которых в графе не было никогда (метаданные времён Node
    Studio, они же общепроектный fallback в ``_variant_from_studio_meta``),
    не трогаются: для старых роликов это работающая привязка.

    Результаты работы узла (``storage_nodes``, ``gpt_operator_results``,
    ``xlsx_snapshots_by_node``) не трогаются вовсе — это не конфиг.
    """
    keys = [str(i).strip() for i in removed_ids if str(i or "").strip()]
    if not keys:
        return []
    dropped: list[str] = []
    for bucket_name in LEGACY_NODE_BUCKETS:
        bucket = meta.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        hits = [k for k in keys if k in bucket]
        if not hits:
            continue
        new_bucket = {k: v for k, v in bucket.items() if k not in hits}
        meta[bucket_name] = new_bucket
        dropped.extend(f"{bucket_name}:{k}" for k in hits)
    return dropped
