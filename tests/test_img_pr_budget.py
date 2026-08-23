"""Промт пишется под потолок того генератора, куда поедет кадр.

Мастер требовал тело ≤4877 знаков и дословный стилевой замок (≈1065), а
`image_generation` у MiniMax берёт 1500 и режет хвост сам. На сюжет
оставалось ~435 знаков вместе с расстановкой; разницу добирал компрессор
`outsee_retry` — лишний вызов модели на каждый кадр и вторая точка, где
режиссёрское задание теряется. Дешевле сказать агенту правду на входе.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import img_pr_batches as ipb
from app.services.img_pr_budget import (
    MINIMAX_IMAGE_PROMPT_MAX,
    OUTSEE_IMAGE_PROMPT_MAX,
    STYLE_COMPACT,
    STYLE_FULL,
    budget_for_provider,
    style_mode_for_budget,
)

_MASTER = Path("prompts/05_image_prompts/default.md")


def test_budget_follows_the_provider() -> None:
    assert budget_for_provider("minimax") == MINIMAX_IMAGE_PROMPT_MAX
    assert budget_for_provider("MiniMax") == MINIMAX_IMAGE_PROMPT_MAX
    for other in ("grsai", "outsee", "", "unknown"):
        assert budget_for_provider(other) == OUTSEE_IMAGE_PROMPT_MAX


def test_budget_leaves_room_for_the_gen_id_prefix() -> None:
    """Пайплайн ставит `[ID: …]` первой строкой — он тоже считается провайдером."""
    assert MINIMAX_IMAGE_PROMPT_MAX < 1500
    assert OUTSEE_IMAGE_PROMPT_MAX < 4900


def test_tight_budget_switches_the_style_lock() -> None:
    assert style_mode_for_budget(MINIMAX_IMAGE_PROMPT_MAX) == STYLE_COMPACT
    assert style_mode_for_budget(OUTSEE_IMAGE_PROMPT_MAX) == STYLE_FULL


def test_compact_lock_actually_fits_the_minimax_budget() -> None:
    """Иначе короткий замок — такая же фикция, как полный: сцене места нет."""
    blocks = re.findall(r"```\n(.*?)\n```", _MASTER.read_text(encoding="utf-8"), flags=re.S)
    style_blocks = [b for b in blocks if b.startswith(("STYLE:", "Final style lock:", "Negative:"))]
    assert len(style_blocks) == 4, "в мастере должны быть обе пары блоков — полная и короткая"
    full = sum(len(b) for b in style_blocks[:2])
    compact = sum(len(b) for b in style_blocks[2:])
    assert compact < full
    # На сцену с расстановкой должно остаться больше половины бюджета.
    assert MINIMAX_IMAGE_PROMPT_MAX - compact > MINIMAX_IMAGE_PROMPT_MAX // 2
    assert full > MINIMAX_IMAGE_PROMPT_MAX - 500, "полный замок и правда не помещается"


def test_batch_task_names_the_limit_and_which_lock_to_copy() -> None:
    tight = ipb.batch_footer(batch_i=1, batch_n=2, n=8, limit=1460, style_mode=STYLE_COMPACT)
    assert "1460" in tight
    assert "КОРОТКАЯ" in tight
    assert "4877" not in tight

    wide = ipb.batch_footer(batch_i=1, batch_n=2, n=8, limit=4860, style_mode=STYLE_FULL)
    assert "4860" in wide
    assert "ПОЛНАЯ" in wide


def test_followup_carries_the_same_limit() -> None:
    msg = ipb.followup_message(batch_i=2, batch_n=3, n=8, limit=1460, style_mode=STYLE_COMPACT)
    assert "1460" in msg
    assert "КОРОТКАЯ" in msg
