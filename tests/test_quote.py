"""Котировка шага: что обещаем пользователю до нажатия кнопки.

Проверяется поведение, от которого зависят деньги: точная часть сметы
совпадает с прайсом до цента, стохастическая не занижает резерв, а радиус
перегенерации берётся из того же графа, по которому идёт инвалидация.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import quote as q
from app.services.credits import format_credits


def _project(**over) -> SimpleNamespace:
    base = dict(
        meta=None,
        image_resolution=None,
        aspect_ratio=None,
        image_quality=None,
        video_resolution=None,
        video_duration=6,
        image_generator_id=None,
        video_generator_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def no_history(monkeypatch) -> None:
    """Оценка без обращения к БД: история пуста."""

    async def _empty(step_code, *, session=None, limit=50):  # noqa: ARG001
        return []

    monkeypatch.setattr(q, "step_history_usd", _empty)


@pytest.fixture
def minimax(monkeypatch) -> None:
    """Провайдер с известным тарифом.

    По умолчанию в тестовом окружении стоит генератор, которого нет в
    прайсе, — и это отдельный проверяемый случай (см. ниже). Здесь нужен
    именно тарифицированный путь.
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "video_provider", "minimax", raising=False)
    monkeypatch.setattr(settings, "image_provider", "minimax", raising=False)


# ── детерминированная часть ────────────────────────────────────────────────


async def test_video_price_is_exact_to_the_cent(no_history, minimax) -> None:
    """Цена шага известна заранее копейка в копейку: без «примерно»."""
    est = await q.quote_step(_project(), "video", frames=24)
    assert est.basis == "media"
    assert est.exact
    # Дефолт 720p (1080p убрано из опций 2026-09-17 — живой шлюз отдает 720p).
    assert est.median_usd == est.p90_usd == pytest.approx(0.19 * 24, abs=1e-9)


async def test_unpriced_provider_is_not_free(no_history) -> None:
    """Генератор без строки в прайсе не превращается в бесплатный шаг.

    Тариф outsee/minimax/kie зависит от плана владельца и в код не вписан. Пока
    его там нет, смета обязана взять справочную величину и честно сказать,
    что она справочная, — а не показать ноль.
    """
    from app.settings import settings as live

    est = await q.quote_step(_project(), "video", frames=24)
    if live.video_provider == "minimax":  # окружение владельца — случай не тот
        pytest.skip("в этом окружении видео тарифицировано")
    assert est.median_usd > 0
    assert est.basis == "prior"
    assert not est.exact


async def test_images_add_text_check_to_media(no_history, minimax) -> None:
    """У шага «Картинки» две строки расхода, и обе входят в холд.

    Считать только PNG значило бы занизить резерв на проверку кадра
    зрением — а холда, которого не хватило, довольно, чтобы шаг встал.
    """
    est = await q.quote_step(_project(), "img", frames=24)
    media_only = 0.0035 * 24
    assert est.median_usd > media_only
    assert est.median_usd == pytest.approx(media_only + q.PRIOR_TEXT_USD["img"], abs=1e-6)
    assert not est.exact  # текстовая часть остаётся оценкой


async def test_no_binary_dust_in_price(no_history, minimax) -> None:
    """24 клипа по $0.19 — это 13.68 кредита, а не 13.69.

    В float `0.19 * 24` даёт 4.5600000000000005, и округление цены вверх
    превращало бы пыль в лишний микрокредит.
    """
    est = await q.quote_step(_project(video_resolution="720p"), "video", frames=24)
    assert est.median_usd == pytest.approx(0.19 * 24, abs=1e-9)
    assert format_credits(est.price_micro, rounding="up") == "13.68"


async def test_audio_priced_per_character(no_history) -> None:
    est = await q.quote_step(_project(), "audio", voice_chars=950)
    assert est.basis == "media"
    assert est.median_usd == pytest.approx(0.095, abs=1e-9)


async def test_unknown_frame_count_falls_back_to_prior(no_history) -> None:
    """Без числа кадров смета всё равно есть — но перестаёт быть точной."""
    est = await q.quote_step(_project(), "video", frames=None)
    assert est.basis == "prior"
    assert not est.exact
    assert est.median_usd == pytest.approx(q.PRIOR_MEDIA_USD["video"], abs=1e-9)


async def test_local_step_is_free(no_history) -> None:
    for code in ("assemble", "publish", "scene_asm"):
        est = await q.quote_step(_project(), code)
        assert est.median_usd == 0.0
        assert est.hold_micro == 0


# ── стохастическая часть ───────────────────────────────────────────────────


async def test_thin_history_shows_reference_but_holds_the_worst(monkeypatch) -> None:
    """Единственный прогон может оказаться отладочным.

    В проекте #2 шаг раскадровки перезапускался десяток раз, и его
    «медиана» вчетверо выше чистого прогона. Показываем справку, резервируем
    по худшему наблюдению: ошибка вверх возвращается, ошибка вниз
    останавливает шаг.
    """

    async def _one_dirty_run(step_code, *, session=None, limit=50):  # noqa: ARG001
        return [1.90]

    monkeypatch.setattr(q, "step_history_usd", _one_dirty_run)
    est = await q.quote_step(_project(), "scene_d")
    assert est.basis == "prior"
    assert est.median_usd == pytest.approx(q.PRIOR_TEXT_USD["scene_d"], abs=1e-9)
    assert est.p90_usd == pytest.approx(1.90, abs=1e-9)
    assert est.hold_micro > est.price_micro


async def test_enough_history_wins_over_reference(monkeypatch) -> None:
    async def _many(step_code, *, session=None, limit=50):  # noqa: ARG001
        return [0.10, 0.12, 0.14, 0.40]

    monkeypatch.setattr(q, "step_history_usd", _many)
    est = await q.quote_step(_project(), "script")
    assert est.basis == "history"
    assert est.samples == 4
    assert est.median_usd == pytest.approx(0.13, abs=1e-9)
    assert est.p90_usd == pytest.approx(0.40, abs=1e-9)


async def test_hold_never_below_price(no_history) -> None:
    """Резерв не может быть меньше показанной цены ни на одном шаге."""
    for code in q.PRIOR_TEXT_USD:
        est = await q.quote_step(_project(), code, frames=24, voice_chars=950)
        assert est.hold_micro >= est.price_micro, code


# ── радиус перегенерации ───────────────────────────────────────────────────


async def test_cascade_follows_the_invalidation_graph(no_history) -> None:
    """Смета и сброс смотрят на один граф.

    Иначе пользователь платит не за то, что пересчитается.
    """
    casc = await q.quote_cascade(_project(), "img_pr", frames=24)
    codes = [s.step_code for s in casc.steps]
    assert codes[0] == "img_pr"
    assert {"img", "anim_pr", "video"} <= set(codes)
    assert "script" not in codes  # предки в радиус не входят


async def test_cascade_is_dominated_by_video(no_history, minimax) -> None:
    """82% себестоимости — видео. На этом стоит весь интерфейс (§6.3)."""
    casc = await q.quote_cascade(_project(), "img_pr", frames=24)
    dominant = casc.dominant()
    assert dominant is not None
    assert dominant.step_code == "video"
    assert dominant.median_usd > casc.median_usd * 0.5


async def test_frame_edit_is_orders_cheaper_than_cascade(no_history, minimax) -> None:
    """Правка кадра и переделка раскадровки не должны стоить похоже —
    иначе кнопки нельзя показывать рядом (§7.3)."""
    one_clip, _ = q.video_unit_usd(_project())
    casc = await q.quote_cascade(_project(), "img_pr", frames=24)
    assert casc.median_usd > one_clip * 10
