"""Сколько символов промта картинки реально доедет до генератора.

Мастер-промт требовал тело ≤4877 знаков и дословные блоки STYLE/Negative
(вместе ≈1065). У MiniMax `image_generation` берёт 1500 и режет хвост сам —
то есть на сюжет оставалось ~435 знаков вместе с расстановкой. Дальше
включался компрессор (`outsee_retry`) и переписывал промт ещё раз, уже своей
моделью: лишний вызов на каждый кадр и вторая точка потери задания.

Дешевле сказать агенту правду на входе. Бюджет считается по тому провайдеру,
куда поедет картинка ЭТОГО проекта, и уезжает в задание батча вместе с
указанием, какой стилевой замок копировать — полный или короткий.
"""

from __future__ import annotations

from typing import Any

# Жёсткие лимиты провайдеров (дублируются в outsee_retry — там точка отправки).
_MINIMAX_CAP = 1500
_OUTSEE_CAP = 4900

# Перед отправкой пайплайн ставит на первую строку `[ID: P2-F12-a1b2c3d4]`
# плюс запас под уникализацию ретрая (см. ``outsee_retry._prefix_reserve``:
# 21 + 2 + 8, у второго шота на 3 больше). Агент должен целиться НИЖЕ потолка
# провайдера ровно на этот префикс — иначе промт «влез» по его счёту и не
# влез по счёту генератора, и включится компрессор.
_GEN_ID_RESERVE = 40

MINIMAX_IMAGE_PROMPT_MAX = _MINIMAX_CAP - _GEN_ID_RESERVE
OUTSEE_IMAGE_PROMPT_MAX = _OUTSEE_CAP - _GEN_ID_RESERVE

# Ниже этого бюджета полный замок (≈1065 знаков) съедает сцену целиком.
COMPACT_STYLE_BELOW = 2600

STYLE_FULL = "full"
STYLE_COMPACT = "compact"


def budget_for_provider(provider: str) -> int:
    return (
        MINIMAX_IMAGE_PROMPT_MAX if (provider or "").strip().lower() == "minimax" else OUTSEE_IMAGE_PROMPT_MAX
    )


def style_mode_for_budget(budget: int) -> str:
    return STYLE_COMPACT if int(budget) < COMPACT_STYLE_BELOW else STYLE_FULL


def image_prompt_budget(project: Any) -> tuple[int, str, str]:
    """``(бюджет, режим замка, провайдер)`` для проекта.

    Модель картинки берётся так же, как её берёт шаг «Картинки»
    (``resolve_node_media_settings`` → ``image_provider_for``): иначе промты
    писались бы под один лимит, а отправлялись в другой.
    """
    provider = "outsee"
    try:
        from app.generation_options import IMAGE_GENERATORS_BY_ID
        from app.services.media_route import image_provider_for
        from app.services.vibecode_catalog import resolve_node_media_settings

        media = resolve_node_media_settings(project, node_type="images")
        gen = IMAGE_GENERATORS_BY_ID.get(media["image_generator_id"])
        provider = image_provider_for(gen.outsee_slug if gen else None)
    except Exception:  # noqa: BLE001
        from loguru import logger

        logger.warning("img_pr budget: провайдер картинок не определился — беру дефолт", exc_info=True)
    budget = budget_for_provider(provider)
    return budget, style_mode_for_budget(budget), provider
