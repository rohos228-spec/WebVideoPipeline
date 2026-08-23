"""Мост между журналами вызовов и графом шагов: `node_key` → `step_code`.

Журналы (`llm_calls`, `media_calls`) помечают строку ключом ноды: `images`,
`n_excel_gpt_sd_cd_camera`, `videos`. Граф зависимостей
(`app/orchestrator/step_dependencies.py`) оперирует кодами шагов: `img`,
`scene_d`, `video`. Пока деньги считались суммой по проекту, разница ничего
не стоила. Как только по шагу выставляется цена, она становится критичной:
нераспознанный `node_key` — это расход, который не попал ни в одну котировку.

**Неизвестный ключ не превращается молча в ноль.** ``step_of_node_key``
возвращает ``None``, а ``attribute`` складывает такие строки в отдельную
корзину ``unattributed``. Котировка обязана показать её владельцу: «$0.42
расходов вне шагов» честнее, чем занижённая на те же $0.42 цена.

Это тот же приём, что и `scene_design/acceptance.py`: требование объявлено
один раз в таблице, а проверка того, что реальность ей соответствует, —
в тесте (`tests/test_cost_attribution.py` сверяет словарь с живой БД).
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.step_dependencies import known_step_codes

#: Точное соответствие ключа ноды коду шага DAG.
NODE_KEY_STEP: dict[str, str] = {
    "plan": "plan",
    "script": "script",
    "split": "split",
    "hero": "hero",
    "items": "items",
    "excel_gpt": "enrich_1",
    "scene_d": "scene_d",
    "sd_agent": "scene_d",
    "scene_asm": "scene_asm",
    "image_prompts": "img_pr",
    "img_pr": "img_pr",
    # `images` в llm_calls — проверка кадра зрением, в media_calls — сама
    # генерация PNG. Оба расхода принадлежат шагу `img`.
    "images": "img",
    "img": "img",
    "anim_pr": "anim_pr",
    "animation_prompts": "anim_pr",
    "videos": "video",
    "video": "video",
    "audio": "audio",
    "music": "music",
    "sfx_plan": "sfx_plan",
    "sfx_gen": "sfx_gen",
    "assemble": "assemble",
    "publish": "publish",
}

#: Ключи с номером слота: `n_excel_gpt_3` → `enrich_3`.
_ENRICH_SLOT = re.compile(r"^n_excel_gpt_(\d+)$")
#: Веер сцен: `n_excel_gpt_sd_cd_camera`, `n_excel_gpt_sd_skel` → `scene_d`.
_SCENE_AGENT = re.compile(r"^n_excel_gpt_sd_")
#: Ключи, которые расходом шага не являются и в котировку не идут.
NON_STEP_KEYS: frozenset[str] = frozenset({"adhoc", ""})


def step_of_node_key(node_key: str) -> str | None:
    """Код шага DAG для ключа ноды. ``None`` — ключ шагу не принадлежит.

    ``None`` возвращается и для служебных ключей (`adhoc` — ручной вызов
    вне конвейера), и для незнакомых. Различить их важно только в отчёте,
    поэтому см. ``is_known_non_step``.
    """
    key = (node_key or "").strip()
    if key in NON_STEP_KEYS:
        return None
    if key in NODE_KEY_STEP:
        return NODE_KEY_STEP[key]
    if _SCENE_AGENT.match(key):
        return "scene_d"
    slot = _ENRICH_SLOT.match(key)
    if slot:
        n = int(slot.group(1))
        code = f"enrich_{n}"
        return code if code in known_step_codes() else None
    # `n_<что-то>` — нода пользовательского графа: шага DAG за ней нет.
    return None


def is_known_non_step(node_key: str) -> bool:
    """Ключ заведомо не принадлежит шагу — не повод для тревоги."""
    return (node_key or "").strip() in NON_STEP_KEYS


def attribute(rows: list[Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Разложить строки журнала по шагам.

    Принимает что угодно с атрибутами ``node_key`` и ``cost_usd`` (строки
    ORM или простые объекты). Возвращает ``(по шагам, вне шагов)``, где
    вторая корзина сгруппирована по исходному ключу — чтобы в отчёте было
    видно не только «сколько», но и «откуда».
    """
    by_step: dict[str, float] = {}
    unattributed: dict[str, float] = {}
    for row in rows or []:
        key = str(getattr(row, "node_key", "") or "")
        cost = float(getattr(row, "cost_usd", 0.0) or 0.0)
        step = step_of_node_key(key)
        if step is None:
            unattributed[key or "(пусто)"] = unattributed.get(key or "(пусто)", 0.0) + cost
        else:
            by_step[step] = by_step.get(step, 0.0) + cost
    return by_step, unattributed


def unmapped_keys(node_keys: list[str]) -> list[str]:
    """Ключи, которые не легли ни на шаг, ни в список служебных.

    Используется тестом и диагностикой: появление нового ключа в проде
    должно быть замечено, а не растворено в «прочем».
    """
    out: list[str] = []
    for key in node_keys or []:
        if is_known_non_step(key) or step_of_node_key(key) is not None:
            continue
        if key not in out:
            out.append(key)
    return out
