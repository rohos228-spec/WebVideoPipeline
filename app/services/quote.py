"""Сколько будет стоить шаг — до того, как он запущен.

Основа кредитной механики (`docs/SAAS-PIVOT.md` §5.4, §6.4): без котировки
нечего резервировать холдом, нечего показывать в чате и нечем считать радиус
перегенерации. Всё остальное в биллинге — надстройка над этим модулем.

**Смета делится надвое, и это принципиально.**

* *Детерминированная часть — медиа.* Как только известно число кадров, цена
  считается точно: тариф провайдера умножается на количество генераций.
  MiniMax берёт за КЛИП, поэтому ключ прайса уточняется разрешением и
  длительностью (`minimax:<model>@768P:6`) — ровно тем же способом, каким
  учёт пишет фактический расход. Самый дорогой шаг оказался самым
  предсказуемым.
* *Стохастическая часть — текст.* Сколько вызовов сделает агент, заранее не
  знает никто: контракт может отвергнуть ответ и вызвать перезапрос. Поэтому
  берётся распределение по истории того же шага — медиана для показа, p90
  для холда.

**Оценка честно сообщает, из чего она сделана.** ``basis`` = ``media`` —
цена точная; ``history`` — по прошлым прогонам; ``prior`` — истории мало,
взята справочная величина из §6.1 с двукратной вилкой; ``unknown`` — шаг
бесплатный или расход не тарифицирован. Показывать «≈$0.10» там, где на
самом деле «от $0.05 до $0.20», — способ потерять доверие ровно один раз.

**Радиус, а не шаг.** Пользователь, который правит промт, платит не за
переделку промта, а за всё, что от него зависит (§7.1). ``quote_cascade``
складывает конус потомков из графа зависимостей — того самого, что уже
управляет инвалидацией кэша.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.orchestrator.step_dependencies import TOPO_ORDER, project_cone
from app.services.credits import price_micro

#: Справочная себестоимость ТЕКСТОВОЙ части шага, $ за прогон
#: (docs/SAAS-PIVOT.md §6.1). Используется, пока по шагу нет истории. Это не
#: «оценка на глаз»: медианы живых прогонов на 2026-08-23. Устаревает —
#: обновляется вместе с §6.1.
PRIOR_TEXT_USD: dict[str, float] = {
    "plan": 0.011,
    "script": 0.054,
    "split": 0.017,
    "scene_d": 0.422,  # скелет + персонажи + мир + action + camera
    "scene_asm": 0.0,  # сборка локальная
    "hero": 0.025,
    "items": 0.010,
    "enrich_1": 0.011,
    "enrich_2": 0.011,
    "enrich_3": 0.011,
    "enrich_4": 0.011,
    "enrich_5": 0.011,
    "img_pr": 0.098,
    # Проверка кадра зрением: 8 вызовов по $0.006. Не путать с генерацией
    # PNG — это отдельная, медийная строка расхода того же шага.
    "img": 0.046,
    "anim_pr": 0.030,
    "video": 0.0,  # промт уже написан в anim_pr, клип — чистое медиа
    "audio": 0.0,
    "music": 0.0,
    "sfx_plan": 0.002,
    "sfx_gen": 0.0,
    "assemble": 0.0,  # ffmpeg локально
    "publish": 0.0,
}

#: Справочная МЕДИЙНАЯ часть, $ за прогон. Нужна, только пока неизвестно
#: число генераций: как только оно есть, медиа считается точно по прайсу.
PRIOR_MEDIA_USD: dict[str, float] = {
    "hero": 0.025,   # ~7 портретов
    "items": 0.007,  # ~2 предмета
    "img": 0.084,    # 24 кадра
    "video": 4.560,  # 24 клипа 768P/6s
    "audio": 0.095,  # ~950 символов
}

#: Шаги, у которых расхода нет вовсе: считаются локально.
LOCAL_STEPS: frozenset[str] = frozenset({"scene_asm", "assemble", "publish", "sfx_gen"})

#: Во сколько раз p90 выше медианы, когда истории не хватает на перцентиль.
#: Две попытки вместо одной — обычный исход отвергнутого контракта.
_PRIOR_SPREAD = 2.0

#: Меньше этого числа прогонов — история не считается представительной.
_MIN_SAMPLES = 3


@dataclass(frozen=True)
class StepEstimate:
    """Оценка одного шага. Деньги — в долларах себестоимости."""

    step_code: str
    median_usd: float
    p90_usd: float
    basis: str  # media | history | prior | unknown
    samples: int = 0
    note: str = ""

    @property
    def exact(self) -> bool:
        """Цена точна до цента — можно не показывать вилку."""
        return self.basis in ("media", "unknown")

    @property
    def price_micro(self) -> int:
        """Цена показа: медиана с маржой, микрокредиты."""
        return price_micro(self.median_usd)

    @property
    def hold_micro(self) -> int:
        """Сколько резервировать: p90 с маржой."""
        return price_micro(self.p90_usd)


@dataclass(frozen=True)
class CascadeEstimate:
    """Оценка каскада: шаг плюс всё, что от него зависит."""

    root: str
    steps: tuple[StepEstimate, ...]

    @property
    def median_usd(self) -> float:
        return sum(s.median_usd for s in self.steps)

    @property
    def p90_usd(self) -> float:
        return sum(s.p90_usd for s in self.steps)

    @property
    def price_micro(self) -> int:
        return price_micro(self.median_usd)

    @property
    def hold_micro(self) -> int:
        return price_micro(self.p90_usd)

    @property
    def exact(self) -> bool:
        return all(s.exact for s in self.steps)

    def dominant(self) -> StepEstimate | None:
        """Самый дорогой шаг каскада — то, что показывают человеку первым."""
        priced = [s for s in self.steps if s.median_usd > 0]
        return max(priced, key=lambda s: s.median_usd) if priced else None


# ────────────────────────────────────────────────────────────────────────────
# Детерминированная часть: медиа по прайсу
# ────────────────────────────────────────────────────────────────────────────


def image_unit_usd(project: Any) -> tuple[float, str]:
    """Цена одной картинки этого проекта и ключ прайса.

    Провайдер и модель берутся ровно так же, как их берёт шаг «Картинки»
    (`img_pr_budget` делает то же самое для лимита промта): иначе смета
    считалась бы по одному генератору, а деньги списывались за другой.
    """
    from app.services.media_ledger import price_for

    provider, model = _image_target(project)
    entry = price_for(provider, model)
    per = entry.per_call if entry.per_call is not None else entry.per_unit
    return (float(per or 0.0), f"{provider}:{model}")


def video_unit_usd(project: Any) -> tuple[float, str]:
    """Цена одного клипа этого проекта и ключ прайса с вариантом."""
    from app.services.media_ledger import price_for

    provider, model, variant = _video_target(project)
    entry = price_for(provider, model, variant)
    # У видео тариф за генерацию; per_unit (за секунду) — запасной путь для
    # провайдеров с посекундной оплатой.
    if entry.per_call is not None:
        per = float(entry.per_call)
    elif entry.per_unit is not None:
        per = float(entry.per_unit) * _video_duration(project)
    else:
        per = 0.0
    return (per, f"{provider}:{model}@{variant}" if variant else f"{provider}:{model}")


def tts_usd(chars: int) -> tuple[float, str]:
    """Цена озвучки по числу символов. ElevenLabs тарифицирует посимвольно."""
    from app.services.media_ledger import price_for
    from app.settings import settings

    model = (getattr(settings, "elevenlabs_tts_model", "") or "tts").strip()
    entry = price_for("elevenlabs", model)
    per = entry.per_unit
    if per is None:
        return (0.0, f"elevenlabs:{model}")
    return (float(per) * max(0, int(chars)), f"elevenlabs:{model}")


def _image_target(project: Any) -> tuple[str, str]:
    try:
        from app.bots.minimax import studio_id_to_minimax_image_slug
        from app.generation_options import IMAGE_GENERATORS_BY_ID
        from app.services.media_route import image_provider_for
        from app.services.vibecode_catalog import resolve_node_media_settings

        media = resolve_node_media_settings(project, node_type="images")
        gid = media["image_generator_id"]
        gen = IMAGE_GENERATORS_BY_ID.get(gid)
        slug = gen.outsee_slug if gen else None
        provider = image_provider_for(slug)
        model = studio_id_to_minimax_image_slug(gid) if provider == "minimax" else (slug or "")
        return (provider, model)
    except Exception:  # noqa: BLE001
        logger.warning("quote: генератор картинок не определился — смета без медиа", exc_info=True)
        return ("", "")


def _video_target(project: Any) -> tuple[str, str, str]:
    try:
        from app.bots.minimax import (
            normalize_video_duration,
            normalize_video_resolution,
            studio_id_to_minimax_video_slug,
        )
        from app.generation_options import DEFAULTS, VIDEO_GENERATORS_BY_ID, VIDEO_RESOLUTIONS_BY_ID
        from app.services.media_route import video_provider_for
        from app.services.vibecode_catalog import effective_video_generator_id

        gid = effective_video_generator_id(project, node_type="videos")
        gen = VIDEO_GENERATORS_BY_ID.get(gid)
        slug = gen.outsee_slug if gen else None
        provider = video_provider_for(slug)
        res_choice = VIDEO_RESOLUTIONS_BY_ID.get(
            getattr(project, "video_resolution", None) or DEFAULTS["video_resolution"]
        )
        res = normalize_video_resolution(res_choice.outsee_slug if res_choice else None)
        dur = normalize_video_duration(_video_duration(project))
        model = studio_id_to_minimax_video_slug(gid) if provider == "minimax" else (slug or "")
        return (provider, model, f"{res}:{dur}")
    except Exception:  # noqa: BLE001
        logger.warning("quote: генератор видео не определился — смета без медиа", exc_info=True)
        return ("", "", "")


def _video_duration(project: Any) -> float:
    raw = getattr(project, "video_duration", None) or getattr(project, "clip_duration", None)
    try:
        return float(raw) if raw else 6.0
    except (TypeError, ValueError):
        return 6.0


# ────────────────────────────────────────────────────────────────────────────
# Стохастическая часть: история по шагу
# ────────────────────────────────────────────────────────────────────────────


def _percentile(values: list[float], q: float) -> float:
    """Перцентиль по ближайшему рангу. Малым выборкам линейная интерполяция
    добавляет точности, которой в них нет."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


async def step_history_usd(step_code: str, *, session: Any = None, limit: int = 50) -> list[float]:
    """Стоимость шага по каждому прошлому проекту, где он выполнялся.

    Единица выборки — прогон шага в проекте, а не отдельный вызов: холд
    ставится на шаг целиком, значит и разброс нужен на уровне шага.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import LlmCall, MediaCall
    from app.services.cost_attribution import step_of_node_key

    async def _collect(sess: Any) -> list[float]:
        per_project: dict[int, float] = {}
        for model in (LlmCall, MediaCall):
            rows = (
                await sess.execute(
                    select(model.project_id, model.node_key, model.cost_usd).where(
                        model.project_id.is_not(None)
                    )
                )
            ).all()
            for project_id, node_key, cost in rows:
                if step_of_node_key(str(node_key or "")) != step_code:
                    continue
                per_project[int(project_id)] = per_project.get(int(project_id), 0.0) + float(cost or 0.0)
        return [v for v in per_project.values() if v > 0][-limit:]

    if session is not None:
        return await _collect(session)
    async with session_scope() as sess:
        return await _collect(sess)


# ────────────────────────────────────────────────────────────────────────────
# Сборка оценки
# ────────────────────────────────────────────────────────────────────────────


async def quote_step(
    project: Any,
    step_code: str,
    *,
    frames: int | None = None,
    voice_chars: int | None = None,
    session: Any = None,
) -> StepEstimate:
    """Оценка одного шага для этого проекта.

    Смета шага — **сумма** двух частей, а не выбор между ними. У шага
    «Картинки» есть и точная медийная строка (24 PNG по прайсу), и
    стохастическая текстовая (проверка кадра зрением, число вызовов заранее
    неизвестно). Посчитать только первую значило бы занизить холд на треть,
    а холда, которого не хватило, достаточно, чтобы шаг встал на середине.

    ``frames`` — число кадров, если оно уже известно (после разбивки). Без
    него медийная часть берётся справочной, и оценка перестаёт быть точной.
    """
    if step_code in LOCAL_STEPS:
        return StepEstimate(step_code, 0.0, 0.0, "unknown", note="считается локально")

    exact_media, media_note = _media_estimate(
        project, step_code, frames=frames, voice_chars=voice_chars
    )
    text_median, text_p90, text_basis, samples, text_note = await _text_estimate(step_code, session=session)

    media_exact = exact_media is not None
    media = float(exact_media) if exact_media is not None else PRIOR_MEDIA_USD.get(step_code, 0.0)
    if not media_exact and media:
        media_note = "медиа по справке: счёт генераций неизвестен"

    total_median = _exact(media + text_median)
    total_p90 = _exact(media + text_p90)
    if total_median <= 0 and total_p90 <= 0:
        return StepEstimate(step_code, 0.0, 0.0, "unknown", note="шаг вне прайса")

    if not media_exact and media:
        basis = "prior"
    elif text_median <= 0:
        basis = "media"
    else:
        basis = text_basis
    note = " + ".join(x for x in (media_note, text_note) if x)
    return StepEstimate(step_code, total_median, total_p90, basis, samples=samples, note=note)


async def _text_estimate(step_code: str, *, session: Any = None) -> tuple[float, float, str, int, str]:
    """``(медиана, p90, основание, число прогонов, пояснение)`` текстовой части."""
    history = await step_history_usd(step_code, session=session)
    prior = PRIOR_TEXT_USD.get(step_code)

    if len(history) >= _MIN_SAMPLES:
        return (_median(history), _percentile(history, 0.9), "history", len(history), "текст по истории")
    if prior is None:
        return (0.0, 0.0, "unknown", len(history), "")
    if prior <= 0:
        return (0.0, 0.0, "media", len(history), "")
    if not history:
        return (prior, prior * _PRIOR_SPREAD, "prior", 0, "текста в истории нет, вилка ×2")
    # История есть, но её мало — а один прогон запросто окажется отладочным:
    # в проекте #2 шаг `scene_d` перезапускался десяток раз, и его «медиана»
    # вчетверо выше чистого прогона. Показываем справочную величину (§6.1 —
    # как раз чистый прогон), а холд берём по худшему наблюдению: ошибиться
    # вверх в резерве значит вернуть излишек, вниз — остановить шаг.
    return (
        prior,
        max(prior * _PRIOR_SPREAD, max(history)),
        "prior",
        len(history),
        "текста в истории мало: показ по справке, холд по худшему прогону",
    )


def _media_estimate(
    project: Any,
    step_code: str,
    *,
    frames: int | None,
    voice_chars: int | None,
) -> tuple[float | None, str]:
    """Точная часть сметы. ``None`` — шаг не медийный или счёт неизвестен."""
    if step_code == "img" and frames:
        per, key = image_unit_usd(project)
        if per > 0:
            return (_exact(per * int(frames)), f"{frames} × {key}")
    elif step_code == "video" and frames:
        per, key = video_unit_usd(project)
        if per > 0:
            return (_exact(per * int(frames)), f"{frames} × {key}")
    elif step_code == "audio" and voice_chars:
        total, key = tts_usd(int(voice_chars))
        if total > 0:
            return (_exact(total), f"{voice_chars} симв. × {key}")
    return (None, "")


def _exact(usd: float) -> float:
    """Убрать двоичную пыль умножения.

    ``0.19 * 24`` в float — это 4.5600000000000005, а цена округляется
    ВВЕРХ: лишняя триллионная доллара становится лишним микрокредитом и
    ролик стоит 13.69 кредита вместо 13.68. Дробность та же, в какой учёт
    пишет факт (`media_ledger.compute_cost` округляет до 6 знаков), — смета
    и списание обязаны совпадать до последнего разряда.
    """
    return round(float(usd), 6)


async def quote_cascade(
    project: Any,
    step_code: str,
    *,
    frames: int | None = None,
    voice_chars: int | None = None,
    session: Any = None,
    include_self: bool = True,
) -> CascadeEstimate:
    """Оценка радиуса поражения: шаг и всё, что придётся пересчитать.

    Конус берётся у графа зависимостей с учётом выключенных шагов проекта —
    того же, который решает, что сбрасывать при `reset_step`. Смета и
    инвалидация обязаны смотреть на один граф, иначе пользователь заплатит
    не за то, что пересчитается.
    """
    cone = project_cone(project, step_code, include_self=include_self)
    order = {code: i for i, code in enumerate(TOPO_ORDER)}
    steps = [
        await quote_step(project, code, frames=frames, voice_chars=voice_chars, session=session)
        for code in sorted(cone, key=lambda c: order.get(c, 999))
    ]
    return CascadeEstimate(root=step_code, steps=tuple(steps))
