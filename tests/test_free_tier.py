"""Бесплатный уровень: где проходит граница подарка и что её держит.

Решение владельца — бесплатно всё до первой генерации видео на аккаунте
(`docs/SAAS-PIVOT.md` §5.7). Дырок у такого подарка ровно три, и каждая
закрывается отдельно: видео, потолок расхода, число проектов. Здесь на каждую
по тесту, потому что цена ошибки — не баг, а счёт от провайдера.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CreditEntry, CreditHold, Frame, FrameStatus, Project, ProjectStatus
from app.services.free_tier import (
    FreeTierExhausted,
    check_step_allowed,
    free_tier_state,
    record_promo,
)
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "free_tier_enabled", True)
    monkeypatch.setattr(settings, "free_tier_spend_cap_usd", 3.0)
    monkeypatch.setattr(settings, "free_tier_max_projects", 1)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'free.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    yield factory
    await engine.dispose()


async def test_storyboard_is_free_and_video_is_not(db) -> None:
    """Граница подарка проходит по рендеру.

    Видео — 82% себестоимости, и именно оно отделяет «посмотреть» от
    «получить». Всё до него отдаётся даром, чтобы качество было видно до
    оплаты; сам рендер бесплатным не бывает никогда.
    """
    tenant = str(uuid.uuid4())
    async with db() as s:
        for step in ("plan", "script", "split", "img_pr", "img", "anim_pr"):
            assert await check_step_allowed(s, tenant, step_code=step, project_id=1, cost_usd=0.05), (
                f"{step} обязан быть бесплатным"
            )
        for step in ("video", "audio", "music", "sfx_gen"):
            assert not await check_step_allowed(s, tenant, step_code=step, project_id=1, cost_usd=0.05), (
                f"{step} бесплатным быть не может"
            )


async def test_spend_cap_stops_the_open_tap(db) -> None:
    """Потолок расхода: раскадровку можно перезапускать бесконечно.

    Каждый перезапуск стоит платформе денег. Без потолка бесплатный аккаунт —
    открытый кран, и заметно это стало бы по счёту провайдера, а не по
    метрике.
    """
    tenant = str(uuid.uuid4())
    async with db() as s:
        await record_promo(s, tenant, project_id=1, step_code="script", cost_usd=2.90)
        await s.commit()

    async with db() as s:
        # Дешёвый шаг ещё влезает.
        assert await check_step_allowed(s, tenant, step_code="plan", project_id=1, cost_usd=0.05)
        # Дорогой — уже нет, и это не отказ шага, а требование пополнить.
        with pytest.raises(FreeTierExhausted, match="исчерпан"):
            await check_step_allowed(s, tenant, step_code="script", project_id=1, cost_usd=0.50)


async def test_cap_counts_the_worst_case_not_the_average(db) -> None:
    """Потолок проверяется по p90, а не по медиане.

    Платформа платит за худший исход, а не за средний: считать по медиане
    значит регулярно проскакивать за потолок ровно на разницу.
    """
    tenant = str(uuid.uuid4())
    async with db() as s:
        await record_promo(s, tenant, project_id=1, step_code="scene_d", cost_usd=2.80)
        await s.commit()
    async with db() as s:
        # Медиана 0.10 прошла бы, p90 0.40 — нет.
        with pytest.raises(FreeTierExhausted):
            await check_step_allowed(s, tenant, step_code="scene_d", project_id=1, cost_usd=0.40)


async def test_second_free_project_is_refused(db) -> None:
    """Двадцать проектов по $0.97 дешевле, чем один за $3.

    Потолок расхода закрывает кран с одной стороны, число проектов — с
    другой. Проект, уже получавший подарок, остаётся бесплатным: ограничение
    на новые, а не на продолжение работы.
    """
    tenant = str(uuid.uuid4())
    async with db() as s:
        await record_promo(s, tenant, project_id=7, step_code="plan", cost_usd=0.01)
        await s.commit()

    async with db() as s:
        assert await check_step_allowed(s, tenant, step_code="script", project_id=7, cost_usd=0.05)
        with pytest.raises(FreeTierExhausted, match="проект"):
            await check_step_allowed(s, tenant, step_code="plan", project_id=8, cost_usd=0.01)


async def test_paid_video_ends_the_gift_for_the_whole_account(db) -> None:
    """Граница по аккаунту, а не по проекту.

    Формулировка «до первого видео» относится к аккаунту: иначе подарок
    повторялся бы с каждым новым проектом и стоил бы $0.97 за штуку без
    всякого предела.
    """
    from app.services import credit_ledger as cl

    tenant = str(uuid.uuid4())
    async with db() as s:
        assert (await free_tier_state(s, tenant)).active

        await cl.topup(s, tenant, 50 * 10**6)
        hold = await cl.open_hold(s, tenant, project_id=1, step_code="video", amount_micro=13_680_000)
        await cl.settle_hold(s, hold.id, cost_usd=4.56)
        await s.commit()

    async with db() as s:
        state = await free_tier_state(s, tenant)
        assert not state.active
        assert state.reason == "видео уже оплачивалось"
        # И новый проект тоже платный — подарок кончился везде.
        assert not await check_step_allowed(s, tenant, step_code="plan", project_id=99, cost_usd=0.01)


async def test_promo_entry_is_zero_delta_with_real_cost(db) -> None:
    """Подарок — проводка, а не отсутствие проводки.

    Нулевая дельта не двигает баланс: платит платформа. Но строка обязана
    существовать, иначе нельзя ни сосчитать стоимость привлечения, ни
    удержать сами предохранители — §5.7 велит вести счётчики леджером, а не
    отдельной подсистемой, которая с ним разойдётся.
    """
    from app.services.credit_ledger import balance_micro

    tenant = str(uuid.uuid4())
    async with db() as s:
        await record_promo(s, tenant, project_id=3, step_code="img", cost_usd=0.084, ref_table="media_calls")
        await s.commit()

    async with db() as s:
        rows = (await s.execute(select(CreditEntry))).scalars().all()
        assert len(rows) == 1
        entry = rows[0]
        assert entry.kind == "promo"
        assert entry.delta_micro == 0, "подарок сдвинул баланс"
        assert float(entry.cost_usd) == pytest.approx(0.084)
        assert entry.project_id == 3 and entry.step_code == "img"
        assert await balance_micro(s, tenant) == 0


async def test_free_step_runs_without_a_hold(db, monkeypatch) -> None:
    """Сквозняк: бесплатный шаг проходит кассу, не оставив резерва.

    Резервировать нечего — платит платформа. Но след остаётся: промо-проводка
    с фактической себестоимостью, из которой потом считают, во сколько
    обошёлся клиент.
    """
    from app.services.advance_runner import advance_project_job
    from app.services.tenant import tenant_scope
    from app.telegram.noop_bot import get_worker_bot

    tenant = str(uuid.uuid4())
    async with db() as s:
        p = Project(slug="free-run", topic="подарок", status=ProjectStatus.planning)
        s.add(p)
        await s.flush()
        s.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="а" * 40,
                status=FrameStatus.image_prompt_ready,
            )
        )
        await s.commit()
        project_id = int(p.id)

    async def _noop(session, project, bot):
        return None

    monkeypatch.setattr("app.services.advance_runner.advance_project", _noop)
    with tenant_scope(tenant):
        await advance_project_job(project_id, get_worker_bot(None))

    async with db() as s:
        assert (await s.execute(select(CreditHold))).scalars().all() == [], "резерв под подарок"
        promo = (await s.execute(select(CreditEntry))).scalars().all()
        assert [e.kind for e in promo] == ["promo"]
        assert promo[0].step_code == "plan"
        assert promo[0].project_id == project_id


async def test_disabling_the_tier_makes_everything_paid(db, monkeypatch) -> None:
    """Выключенный бесплатный уровень не оставляет лазеек."""
    monkeypatch.setattr(settings, "free_tier_enabled", False)
    tenant = str(uuid.uuid4())
    async with db() as s:
        assert not (await free_tier_state(s, tenant)).active
        assert not await check_step_allowed(s, tenant, step_code="plan", project_id=1, cost_usd=0.01)
