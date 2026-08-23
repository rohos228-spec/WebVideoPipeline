"""Промт-заглушка не считается промтом.

Живой прогон #2, кадр 9: модель вернула `промт_картинки: "..."` — три символа.
Ответ прошёл отбор ops, прошёл контракт, доехал до генератора и стал мусорной
картинкой за деньги, а шаг отчитался успехом. Пола длины не было ни в
``img_pr_batches``, ни в ``contracts/prompt_ops``.
"""

from __future__ import annotations

import pytest

from app.contracts import IMG_PR, LlmContractError
from app.contracts.prompt_ops import MIN_IMAGE_PROMPT_CHARS
from app.services.img_pr_batches import (
    filter_prompt_ops,
    is_degenerate_prompt,
    parse_img_pr_ops,
)

_REAL = "Референс: c01.\nФон: тёмное окно вагона. " + "детали кадра, " * 60


def test_floor_is_far_below_a_real_prompt() -> None:
    """Порог ловит заглушку, а не короткий стиль другого проекта."""
    assert len(_REAL) > MIN_IMAGE_PROMPT_CHARS
    assert not is_degenerate_prompt(_REAL)
    for junk in ("...", "—", "", "   ", "." * (MIN_IMAGE_PROMPT_CHARS + 50)):
        assert is_degenerate_prompt(junk)


def test_stub_op_is_dropped_and_frame_stays_empty() -> None:
    """Кадр без промта переспросят; кадр с «...» считался бы готовым."""
    ops = filter_prompt_ops(
        [
            {"frame_uuid": "a" * 32, "fields": {"промт_картинки": "..."}},
            {"frame_uuid": "b" * 32, "fields": {"промт_картинки": _REAL}},
        ]
    )
    assert [op["frame_uuid"] for op in ops] == ["b" * 32]


def test_stub_dropped_on_the_real_parse_path() -> None:
    reply = (
        '{"ops":[{"frame_uuid":"' + "a" * 32 + '","fields":{"промт_картинки":"..."}},'
        '{"frame_uuid":"' + "b" * 32 + '","fields":{"промт_картинки":"' + _REAL.replace("\n", " ") + '"}}]}'
    )
    assert [op["frame_uuid"] for op in parse_img_pr_ops(reply)] == ["b" * 32]


def test_contract_names_the_frame_and_asks_for_a_full_prompt() -> None:
    reply = '{"ops":[{"frame_uuid":"' + "a" * 32 + '","fields":{"промт_картинки":"..."}}]}'
    with pytest.raises(LlmContractError) as err:
        IMG_PR.parse(reply)
    text = str(err.value)
    assert "a" * 32 in text
    assert str(MIN_IMAGE_PROMPT_CHARS) in text
