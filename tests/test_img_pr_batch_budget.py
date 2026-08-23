"""Размер батча img_pr зависит от провайдера, а не от одной константы.

228k знаков вывода на запрос рассчитаны на быстрый релей. MiniMax на таком
объёме не укладывается в 600 с и уходит в пять холостых ретраев по 10 минут —
шаг стоит час и не двигается.
"""

from __future__ import annotations

import pytest

from app.services.output_batch_plan import batch_count_img_pr, pack_frames_img_pr


def _set_provider(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "text_llm_provider", name)


def test_minimax_splits_two_dozen_frames(monkeypatch) -> None:
    _set_provider(monkeypatch, "minimax")
    assert batch_count_img_pr(24) == 3
    sizes = [len(b) for b in pack_frames_img_pr(list(range(24)))]
    assert sizes == [8, 8, 8]


def test_fast_relay_keeps_one_batch(monkeypatch) -> None:
    """Провайдеру с большим бюджетом дробить незачем — лишние вызовы дороже."""
    _set_provider(monkeypatch, "kie")
    assert batch_count_img_pr(24) == 1
    assert [len(b) for b in pack_frames_img_pr(list(range(24)))] == [24]


def test_explicit_n_batches_still_wins(monkeypatch) -> None:
    _set_provider(monkeypatch, "minimax")
    assert [len(b) for b in pack_frames_img_pr(list(range(24)), n_batches=4)] == [6, 6, 6, 6]


def test_empty_input_is_one_batch(monkeypatch) -> None:
    _set_provider(monkeypatch, "minimax")
    assert batch_count_img_pr(0) == 1
