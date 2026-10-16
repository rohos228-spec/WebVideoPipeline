"""Жёсткий лимит LLM-запросов на запуск + оценка стоимости до запуска.

Каждый вызов модели проходит через ``LlmBudget.reserve()``; превышение —
``BudgetExceeded`` (запуск останавливается, частичные результаты
сохраняются). ``plan_calls`` раскладывает лимит по специалистам заранее,
чтобы UI показал план и цену ещё до нажатия ▶.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_LLM_CALLS = 60
MIN_LLM_CALLS = 1
MAX_LLM_CALLS_HARD = 600

# Специалисты, которые работают по каждому ролику (vision = пачки листов).
PER_VIDEO_AGENTS: tuple[str, ...] = ("visual", "motion", "voice", "narrative")
VISION_AGENTS: tuple[str, ...] = ("visual", "motion")
SYNTH_AGENT = "synth"

# Лимит vision-входа gpt_api (см. style_analyzer.BATCH_SIZE): ≤ 8 картинок/запрос.
IMAGES_PER_CALL = 8

# Приближённые токены на вызов — для оценки цены (не биллинг).
_TOKENS_IN_TEXT = 3500
_TOKENS_OUT_TEXT = 900
_TOKENS_IN_PER_IMAGE = 1100  # лист 3×2 ≈ 1 картинка ≈ 1k токенов
_TOKENS_OUT_VISION = 700
_TOKENS_IN_SYNTH = 6000
_TOKENS_OUT_SYNTH = 1800

# Fallback-цены $/1M (docs/NODE_MODELS.md), если каталог не вернул pricing.
FALLBACK_PRICES_USD_PER_M: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (1.28, 7.68),
    "gpt-5.6-luna": (0.08, 0.47),
    "gemini-3-flash": (0.30, 1.77),
    "claude-sonnet-5": (0.59, 2.95),
}
_DEFAULT_PRICE = (1.28, 7.68)


class BudgetExceeded(RuntimeError):
    """Лимит LLM-запросов исчерпан — дальше модель не вызываем."""


def clamp_max_calls(value: Any, default: int = DEFAULT_MAX_LLM_CALLS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_LLM_CALLS, min(MAX_LLM_CALLS_HARD, n))


@dataclass
class LlmBudget:
    max_calls: int
    used: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def can_reserve(self, n: int = 1) -> bool:
        return self.used + n <= self.max_calls

    def reserve(self, label: str = "") -> int:
        with self._lock:
            if self.used >= self.max_calls:
                raise BudgetExceeded(
                    f"лимит LLM-запросов исчерпан ({self.used}/{self.max_calls}) — {label or 'call'}"
                )
            self.used += 1
            self.log.append({"n": self.used, "label": label})
            return self.used

    def snapshot(self) -> dict[str, Any]:
        return {"used": self.used, "limit": self.max_calls, "remaining": self.remaining}


def model_prices(model: dict[str, Any] | None, model_id: str | None = None) -> tuple[float, float]:
    """($/1M вход, $/1M выход) из каталога или fallback-таблицы."""
    pricing = (model or {}).get("pricing") if isinstance(model, dict) else None
    if isinstance(pricing, dict):
        try:
            pin = pricing.get("input_usd_per_m")
            pout = pricing.get("output_usd_per_m")
            if pin is not None and pout is not None:
                return float(pin), float(pout)
        except (TypeError, ValueError):
            pass
    mid = str(model_id or (model or {}).get("id") or "").strip()
    return FALLBACK_PRICES_USD_PER_M.get(mid, _DEFAULT_PRICE)


def _vision_sheet_batches(n_sheets: int) -> int:
    if n_sheets <= 0:
        return 0
    return (n_sheets + IMAGES_PER_CALL - 1) // IMAGES_PER_CALL


def plan_calls(
    *,
    n_videos: int,
    sheets_per_video: int,
    max_calls: int,
    agents: tuple[str, ...] | list[str] = PER_VIDEO_AGENTS,
    with_synth: bool = True,
    sheets_visual: int | None = None,
    sheets_motion: int | None = None,
) -> dict[str, Any]:
    """Разложить лимит по специалистам.

    Минимум: по 1 вызову на каждого включённого специалиста на каждый ролик
    (vision — минимум 1 пачка ≤ 8 листов) + 1 synth. Если лимита не хватает —
    ``fits=False`` и подсказка; запуск при этом всё равно возможен, но часть
    роликов/агентов будет пропущена (``skip_plan``).
    """
    n_videos = max(0, int(n_videos))
    sheets_per_video = max(0, int(sheets_per_video))
    sheets_by_agent = {
        "visual": max(0, int(sheets_visual if sheets_visual is not None else sheets_per_video)),
        "motion": max(0, int(sheets_motion if sheets_motion is not None else sheets_per_video)),
    }
    max_calls = clamp_max_calls(max_calls)
    enabled = [a for a in PER_VIDEO_AGENTS if a in set(agents)]

    per_video: dict[str, int] = {}
    for agent in enabled:
        if agent in VISION_AGENTS:
            per_video[agent] = max(1, _vision_sheet_batches(sheets_by_agent[agent])) if n_videos else 0
        else:
            per_video[agent] = 1 if n_videos else 0

    minimal_per_video = {a: (1 if n_videos else 0) for a in enabled}
    minimal_total = sum(minimal_per_video.values()) * n_videos + (1 if with_synth and n_videos else 0)
    ideal_total = sum(per_video.values()) * n_videos + (1 if with_synth and n_videos else 0)

    budget_for_videos = max_calls - (1 if with_synth and n_videos else 0)
    fits_minimal = minimal_total <= max_calls

    # Распределяем: сначала минимум всем, потом добавляем vision-пачки, пока есть лимит.
    alloc_per_video = dict(minimal_per_video)
    if n_videos and fits_minimal:
        spare = budget_for_videos - sum(alloc_per_video.values()) * n_videos
        for agent in VISION_AGENTS:
            if agent not in alloc_per_video:
                continue
            want = per_video[agent] - alloc_per_video[agent]
            while want > 0 and spare >= n_videos:
                alloc_per_video[agent] += 1
                spare -= n_videos
                want -= 1

    videos_covered = n_videos
    skip_plan: list[str] = []
    if n_videos and not fits_minimal:
        per_video_min = sum(minimal_per_video.values()) or 1
        videos_covered = max(0, budget_for_videos // per_video_min)
        if videos_covered < n_videos:
            skip_plan.append(
                f"лимита {max_calls} хватит на {videos_covered} из {n_videos} роликов "
                f"(нужно ≥ {minimal_total}); остальные будут разобраны только детерминированно"
            )

    planned_total = sum(alloc_per_video.values()) * videos_covered + (1 if with_synth and videos_covered else 0)
    hint = ""
    if n_videos == 0:
        hint = "нет роликов — загрузите mp4 в ноду или подключите «Файлы»"
    elif not fits_minimal:
        hint = f"поднимите лимит до {minimal_total}, чтобы все специалисты посмотрели каждый ролик"
    elif planned_total < ideal_total:
        hint = (
            f"при лимите {ideal_total} vision-специалисты увидят все листы "
            f"(≈{sheets_by_agent['visual']}+{sheets_by_agent['motion']} на ролик); сейчас часть листов пропускается"
        )

    return {
        "n_videos": n_videos,
        "sheets_per_video": sheets_per_video,
        "sheets_by_agent": sheets_by_agent,
        "images_per_call": IMAGES_PER_CALL,
        "max_calls": max_calls,
        "agents": enabled,
        "with_synth": bool(with_synth and n_videos),
        "per_video": alloc_per_video,
        "videos_covered": videos_covered,
        "minimal_total": minimal_total,
        "ideal_total": ideal_total,
        "planned_total": planned_total,
        "fits": fits_minimal,
        "hint": hint,
        "skip_plan": skip_plan,
    }


def image_tokens(width: int, height: int) -> int:
    """Токены картинки по правилу OpenAI vision (detail=high): вписать в 2048, короткая сторона ≤768, плитки 512 → 85+170·N."""
    import math as _m

    w, h = float(max(1, width)), float(max(1, height))
    k = min(1.0, 2048 / max(w, h))
    w, h = w * k, h * k
    k = min(1.0, 768 / min(w, h))
    w, h = w * k, h * k
    return 85 + 170 * _m.ceil(w / 512) * _m.ceil(h / 512)


def sheet_pixels(grid: str, aspect_w: int = 16, aspect_h: int = 9, cell_h: int = 360, gutter: int = 4) -> tuple[int, int]:
    """Реальный размер листа: ячейка высотой cell_h с шириной по формату ролика."""
    try:
        cols, rows = (int(x) for x in str(grid).lower().split("x"))
    except ValueError:
        cols, rows = 3, 2
    cell_w = max(1, round(cell_h * aspect_w / max(1, aspect_h)))
    return cols * cell_w + (cols - 1) * gutter, rows * cell_h + (rows - 1) * gutter


_PROMPT_CACHE: dict[str, int] = {}


def count_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore

        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:  # noqa: BLE001
        return int(len(text) / 2.8) + 1  # кириллица ≈ 2.8 симв/токен


def prompt_tokens(role: str, prompts_dir: Any = None) -> int:
    """Токены системного промпта специалиста (prompts/style_agent/<role>.md)."""
    from pathlib import Path

    base = Path(prompts_dir) if prompts_dir else Path(__file__).resolve().parents[3] / "prompts" / "style_agent"
    key = str(base / f"{role}.md")
    if key not in _PROMPT_CACHE:
        try:
            _PROMPT_CACHE[key] = count_tokens((base / f"{role}.md").read_text(encoding="utf-8"))
        except OSError:
            _PROMPT_CACHE[key] = 1500
    return _PROMPT_CACHE[key]


METRICS_TOKENS = 1200  # метрики ролика в JSON (сжатые)
OUTPUT_TOKENS = {"visual": 900, "motion": 900, "voice": 800, "narrative": 900, "synth": 2500}
REASONING_FACTOR = 1.0  # reasoning-токены биллятся как выход (≈ размер ответа)
RETRY_FACTOR = 1.15  # повторы при невалидном JSON / обрывах


def estimate_cost(plan: dict[str, Any], *, model: dict[str, Any] | None, model_id: str | None) -> dict[str, Any]:
    """Оценка $ по реальным размерам листов, токенам промптов и цене модели."""
    pin, pout = model_prices(model, model_id)
    markup = float(((model or {}).get("pricing") or {}).get("markup") or 1.0) if isinstance(model, dict) else 1.0
    if markup > 1:  # каталог отдаёт цену с наценкой UI — считаем сырую цену провайдера
        pin, pout = pin / markup, pout / markup
    per_video = plan.get("per_video") or {}
    n = int(plan.get("videos_covered") or 0)
    sheets_default = int(plan.get("sheets_per_video") or 0)
    sheets_by_agent = plan.get("sheets_by_agent") or {}
    img_tok = int(plan.get("image_tokens_per_sheet") or image_tokens(*sheet_pixels("3x2")))
    tok_in = 0
    tok_out = 0
    for agent, calls in per_video.items():
        calls_n = int(calls or 0)
        if not calls_n or not n:
            continue
        base_in = prompt_tokens(agent) + METRICS_TOKENS
        if agent in VISION_AGENTS:
            sheets = min(int(sheets_by_agent.get(agent, sheets_default) or 0), calls_n * IMAGES_PER_CALL)
            tok_in += n * (calls_n * base_in + sheets * img_tok)
        else:
            tok_in += n * calls_n * base_in
        tok_out += n * calls_n * int(OUTPUT_TOKENS.get(agent, 900) * (1 + REASONING_FACTOR))
    if plan.get("with_synth"):
        tok_in += prompt_tokens("synth") + METRICS_TOKENS * max(1, n) + sum(OUTPUT_TOKENS.get(a, 900) for a in per_video) * max(1, n)
        tok_out += int(OUTPUT_TOKENS["synth"] * (1 + REASONING_FACTOR))
    usd = (tok_in / 1_000_000 * pin + tok_out / 1_000_000 * pout)
    return {
        "model_id": model_id or (model or {}).get("id") or "",
        "usd_per_m_in": round(pin, 4),
        "usd_per_m_out": round(pout, 4),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "image_tokens_per_sheet": img_tok,
        "usd": round(usd, 4),
        "usd_hi": round(usd * RETRY_FACTOR, 4),
        "calls": int(plan.get("planned_total") or 0),
    }
