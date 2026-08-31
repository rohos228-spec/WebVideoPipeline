"""Adaptive split fat scene_design GPT-вызовов при 524/timeout.

Один action/camera на весь ролик часто ловит Cloudflare 524. При capacity-failure:
кадры пополам → ещё раз пополам (depth≤2); дальше — ошибка.
"""

from __future__ import annotations

import copy
from typing import Any

from loguru import logger

from app.services.scene_design import agents as ag
from app.services.scene_design.assemble_chunks import (
    _frame_span,
    _overlaps,
    _scene_span_tuple,
)
from app.services.scene_design.chronology import _norm, frame_offsets

SPLITTABLE_AGENTS = frozenset({"action", "camera"})
# depth 0 = целый запрос; 1 = /2; 2 = /4; дальше не дробим.
MAX_SPLIT_DEPTH = 2


class CapacitySplitExhausted(ag.SceneDesignAgentError):
    """Уже дробили /2 и /4 — дальше только hard-fail, без повторного split."""


class ShortChunkAnswer(ag.SceneDesignAgentError):
    """Кусок вернул заметно меньше объектов, чем в него отдали кадров.

    У чанков `validate=False` (валидируется только склейка), поэтому обрыв
    ответа доезжал до `merge_agent_slices` как «валидный» результат: сцены
    молча терялись, а падало это через пять минут и совсем в другом месте —
    «биты скелета без фазы». Живой прогон 2026-08-31: gpt-5.6-sol на один и
    тот же кусок отдавал то 4500 токенов, то 700, и 28 сцен превращались в 19.

    Наследуется от капасити-семейства (`is_capacity_failure` ниже пускает
    его в split): не влезло в ответ — дроби кусок, а не повторяй целиком.
    """


class CreditsExhausted(ag.SceneDesignAgentError):
    """402 / нет кредитов — стоп без retry и без split (не жечь баланс)."""


def _gpt_codes(exc: BaseException) -> tuple[str, int]:
    try:
        from app.services.gpt_api import GptApiError
    except Exception:  # noqa: BLE001
        return "", 0
    if not isinstance(exc, GptApiError):
        return "", 0
    kind = str(exc.context.get("error_kind") or "")
    code = int(exc.context.get("provider_code") or exc.context.get("status_code") or 0)
    return kind, code


def is_credits_failure(exc: BaseException) -> bool:
    """402 / credits insufficient — не дробить и не soft-retry пачками."""
    if isinstance(exc, CreditsExhausted):
        return True
    kind, code = _gpt_codes(exc)
    if code == 402:
        return True
    msg = str(exc).lower()
    return "credits insufficient" in msg or "code=402" in msg or "http 402" in msg


def is_transient_server_failure(exc: BaseException) -> bool:
    """500/502/503 — один повтор того же чанка, без capacity-split."""
    if is_credits_failure(exc):
        return False
    kind, code = _gpt_codes(exc)
    if code in (500, 502, 503):
        return True
    msg = str(exc).lower()
    return any(x in msg for x in ("http 500", "http 502", "http 503", "server exception"))


def is_capacity_failure(exc: BaseException) -> bool:
    """Timeout / 524 / Cloudflare — имеет смысл дробить payload.

    HTTP 500 сюда больше не входит: split на 500 только умножал пустые
    платежи. 402 — отдельно (``is_credits_failure``).
    """
    if isinstance(exc, (CapacitySplitExhausted, CreditsExhausted)):
        return False
    if isinstance(exc, ShortChunkAnswer):
        return True
    if is_credits_failure(exc):
        return False
    kind, code = _gpt_codes(exc)
    if kind in ("timeout", "network") or code == 524:
        return True
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "http 524",
            "timeout",
            "cloudflare",
            "504 gateway",
            "http 504",
        )
    )


def split_frames_half(frames: list[Any]) -> tuple[list[Any], list[Any]]:
    """Пополам по порядку number; обе половины непустые."""
    ordered = [f for f in frames if getattr(f, "uuid", None)]
    if len(ordered) < 2:
        raise ValueError("split_frames_half: нужно ≥2 кадра с uuid")
    mid = max(1, min(len(ordered) // 2, len(ordered) - 1))
    return ordered[:mid], ordered[mid:]


def split_frames_batches(frames: list[Any], *, max_frames: int) -> list[list[Any]]:
    """Пачки кадров ≤ max_frames (проактивный чанк до 524)."""
    ordered = [f for f in frames if getattr(f, "uuid", None)]
    n = max(1, int(max_frames))
    if not ordered:
        return []
    return [ordered[i : i + n] for i in range(0, len(ordered), n)]


def chunk_instruction(*, label: str, depth: int) -> str:
    return (
        f"# ЧАНК ЗАКАДРА (часть «{label}», split_depth={depth})\n"
        "Работай ТОЛЬКО с # ПОЛНЫЙ ЗАКАДР и # КАДРЫ этого чанка.\n"
        "start_words / end_words — дословные цитаты из закадра чанка.\n"
        "Не покрывай закадр вне чанка. Не пиши «продолжение следует».\n"
        "Плотность фаз/шотов — как для полного ролика, но только на этот отрезок."
    )


def filter_action_scenes_for_frames(
    scenes: list[Any],
    chunk_frames: list[Any],
    all_frames: list[Any],
    full_vo: str,
) -> list[dict[str, Any]]:
    """Сцены action, пересекающиеся с VO-диапазоном чанка; иначе пропорция списка."""
    dict_scenes = [s for s in scenes if isinstance(s, dict)]
    if not dict_scenes:
        return []
    offsets = frame_offsets(all_frames, full_vo)
    span = _frame_span(chunk_frames, offsets)
    vo_norm = _norm(full_vo)
    lo, hi = span if span else (0, len(vo_norm) or 1)
    out: list[dict[str, Any]] = []
    for sc in dict_scenes:
        if _overlaps(_scene_span_tuple(sc, vo_norm), lo, hi):
            out.append(copy.deepcopy(sc))
    if out:
        return out
    return _proportional_slice(dict_scenes, chunk_frames, all_frames)


def _proportional_slice(
    items: list[dict[str, Any]],
    chunk_frames: list[Any],
    all_frames: list[Any],
) -> list[dict[str, Any]]:
    uuids = [f.uuid for f in all_frames if f.uuid]
    chunk_set = {f.uuid for f in chunk_frames if f.uuid}
    if not uuids or not items:
        return copy.deepcopy(items)
    indices = [i for i, u in enumerate(uuids) if u in chunk_set]
    if not indices:
        return []
    lo_r = min(indices) / len(uuids)
    hi_r = (max(indices) + 1) / len(uuids)
    a = int(lo_r * len(items))
    b = max(int(hi_r * len(items)), a + 1)
    return copy.deepcopy(items[a:b])


def merge_agent_slices(
    agent: str,
    parts: list[dict[str, Any]],
    *,
    action_scenes_for_ids: list[Any] | None = None,
) -> dict[str, Any]:
    """Склеить частичные JSON-срезы action/camera и провалидировать целиком.

    ``action_scenes_for_ids`` — фазы action: по ним чинятся ``id_scene`` и по
    ним же приёмка считает долю двухсоставных шотов. Считать по составу,
    который назвала сама камера, нельзя: знаменатель не должен выбирать
    проверяемый.
    """
    list_key = ag.LIST_KEY[agent]
    merged: list[Any] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        items = part.get(list_key)
        if isinstance(items, list):
            merged.extend(items)
    if not merged:
        raise ag.SceneDesignAgentError(f"scene_design/{agent}: после склейки чанков пустой «{list_key}»")
    if agent == "action":
        merged = ag.repair_chrono_dyn_year_jumps(merged)
        merged = ag.normalize_chrono_dyn_phase_budget(merged)
        for i, sc in enumerate(merged, start=1):
            if isinstance(sc, dict):
                sc["id_scene"] = f"scene_{i:02d}"
        payoff_fixed = ag.repair_missing_payoff(merged)
        if payoff_fixed:
            logger.info(
                "scene_design/action: payoff доставлен последней фазе — {}",
                "; ".join(payoff_fixed[:8]),
            )
        ag.validate_chrono_dyn_action_scenes(merged)
    elif agent == "camera":
        for i, sh in enumerate(merged, start=1):
            if isinstance(sh, dict) and sh.get("id_shot") is not None:
                sh["id_shot"] = f"shot_{i:02d}"
        ag.validate_chrono_dyn_camera_shots(merged)
        # Долю двухсоставных шотов можно считать только на всей раскадровке:
        # чанк из восьми шотов ничего о ней не говорит. В ``parse_agent_slice``
        # эта проверка стоит для нечанкованного пути — сюда её надо звать
        # отдельно, иначе на реальном (чанкованном) прогоне она мертва.
        if action_scenes_for_ids:
            id_fixes = ag.repair_camera_scene_ids(merged, action_scenes_for_ids)
            if id_fixes:
                logger.warning(
                    "scene_design/camera: id сцен приведены к action — {}",
                    "; ".join(id_fixes[:8]),
                )
        # Приёмка среза: инварианты объявлены в ``acceptance`` вместе с текстом
        # требования, который уезжает в промт. Одна точка на собранный артефакт.
        from app.services.scene_design.acceptance import accept_slice

        accept_slice(
            "camera",
            merged,
            upstream={"action": {"scenes": list(action_scenes_for_ids or [])}},
        )
    logger.info(
        "scene_design/{}: merged {} chunks → {} {}",
        agent,
        len(parts),
        len(merged),
        list_key,
    )
    return {list_key: merged}


def short_chunk_problem(agent: str, data: Any, *, frames_in_chunk: int, label: str) -> str | None:
    """Описание недобора, если кусок вернул подозрительно мало объектов.

    ``None`` — всё в порядке. Порог сознательно грубый (меньше половины):
    задача не поймать «на одну сцену меньше», а отличить обрыв ответа от
    результата. Считаем только для дробимых агентов и только когда кадров
    в куске достаточно, чтобы доля что-то значила.
    """
    if agent not in SPLITTABLE_AGENTS or frames_in_chunk < 4:
        return None
    key = ag.LIST_KEY.get(agent)
    if not key:
        return None
    items = data.get(key) if isinstance(data, dict) else None
    got = len(items) if isinstance(items, list) else 0
    if got * 2 >= frames_in_chunk:
        return None
    return (
        f"кусок {label} вернул {got} «{key}» на {frames_in_chunk} кадров — это обрыв ответа, а не результат"
    )
