"""Сайдкар останавливается, когда перестал продвигаться.

Живой прогон 2026-08-31: очередь шла 33 → 27 → 24 → 8 и встала — проходы
падали, ничего не сохраняя, а цикл повторялся каждые ~70 секунд, и каждый
проход это оплаченный вызов модели. Считать надо ЗАСТОЙ, а не отказы: простой
счётчик падений остановил бы сайдкар и там, где он ещё продвигался.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import anim_pr_sidecar as sc


async def _no_sleep(*_args, **_kwargs) -> None:
    """Заглушка вместо пауз цикла: подменять `asyncio.sleep` им же — рекурсия."""
    return None


class _FakeSession:
    def __init__(self, project, frames):
        self._project = project
        self._frames = frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _model, _pid):
        return self._project

    async def execute(self, _stmt):
        frames = self._frames

        class _Res:
            def scalars(self):
                class _S:
                    def all(self_inner):
                        return frames

                return _S()

        return _Res()


@pytest.mark.asyncio
async def test_pending_counts_items_from_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.SessionLocal", lambda: _FakeSession(object(), ["f1", "f2"]))
    monkeypatch.setattr(
        "app.services.animation_prompt_gpt.collect_batch_items",
        lambda _p, frames: list(frames),
    )
    assert await sc._pending_now(4) == 2


@pytest.mark.asyncio
async def test_pending_is_none_without_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.SessionLocal", lambda: _FakeSession(None, []))
    assert await sc._pending_now(4) is None


@pytest.mark.asyncio
async def test_pending_is_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Диагностика не должна ронять цикл — молча отдаём None."""

    def boom():
        raise RuntimeError("база недоступна")

    monkeypatch.setattr("app.db.SessionLocal", boom)
    assert await sc._pending_now(4) is None


@pytest.mark.asyncio
async def test_loop_stops_after_stuck_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очередь не движется — сайдкар сдаётся, а не жжёт вызовы бесконечно."""
    calls = {"n": 0}

    async def always_fails(_pid, **_kw):
        calls["n"] += 1
        raise RuntimeError("битый JSON apply-ops")

    async def stuck(_pid):
        return 8

    monkeypatch.setattr(sc, "drain_anim_pr_from_image_prompts", always_fails)
    monkeypatch.setattr(sc, "_pending_now", stuck)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(sc._sidecar_loop(4), timeout=5)
    # Первому проходу не с чем сравнивать остаток очереди, поэтому он
    # засчитывается как «мог продвинуться»: стоп наступает на N+1.
    assert calls["n"] == sc._MAX_STUCK_ROUNDS + 1


@pytest.mark.asyncio
async def test_progress_resets_the_stuck_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Падения вперемешку с прогрессом — работаем дальше, пока очередь идёт."""
    calls = {"n": 0}
    queue = [30, 24, 18, 12, 12, 12, 12]

    async def always_fails(_pid, **_kw):
        calls["n"] += 1
        raise RuntimeError("обрыв")

    async def moving(_pid):
        return queue[min(calls["n"] - 1, len(queue) - 1)]

    monkeypatch.setattr(sc, "drain_anim_pr_from_image_prompts", always_fails)
    monkeypatch.setattr(sc, "_pending_now", moving)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(sc._sidecar_loop(4), timeout=5)
    # три первых прохода двигали очередь, стоп только на трёх подряд без движения
    assert calls["n"] > sc._MAX_STUCK_ROUNDS
