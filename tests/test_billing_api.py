"""Цена до нажатия: что отдают ручки сметы и баланса.

Правило продукта, которое эти ручки обслуживают, из `docs/SAAS-PIVOT.md`
§7.3: «перерисовать кадр — 0.01 кр» и «переделать раскадровку — 14.67 кр» не
должны выглядеть одинаково. Значит интерфейсу нужны обе цены и нужна причина
разницы — что именно сгорит.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'price.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        p = Project(slug="price-test", topic="смета", status=ProjectStatus.frames_ready)
        s.add(p)
        await s.flush()
        for n in range(1, 25):
            s.add(
                Frame(
                    project_id=p.id,
                    number=n,
                    voiceover_text="а" * 40,
                    status=FrameStatus.image_prompt_ready,
                )
            )
        await s.commit()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.factory = factory  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def test_video_price_is_exact_before_the_step(client, monkeypatch) -> None:
    """Самый дорогой шаг оказался самым предсказуемым.

    Видео тарифицируется за вызов, а не за токены: как только известно число
    кадров, цена считается до цента (§6.4). Тариф здесь задан явно, а не взят
    из настроек проекта, — иначе тест проверял бы конфигурацию машины, а не
    арифметику. Число 13.68 не выдумано: это 24 клипа 768P из §6.2, та самая
    цифра, которую в спеке видит человек перед оплатой.
    """
    monkeypatch.setattr(
        "app.services.quote._video_target",
        lambda project: ("minimax", "MiniMax-Hailuo-2.3-Fast", "768P:6"),
    )
    res = await client.get("/api/projects/1/steps/video/quote")
    assert res.status_code == 200
    body = res.json()
    assert body["step_code"] == "video"
    assert body["basis"] == "media", "цена видео обязана быть точной, а не по истории"
    assert body["exact"] is True
    assert body["price_credits"] == "13.68"


async def test_one_picture_stays_visibly_cheap() -> None:
    """Одна картинка стоит 0.0105 кредита и должна выглядеть дешёвой.

    Округлив её вверх до 0.01, показали бы цену на 90% выше настоящей — а на
    дешевизне картинки держится вся продуктовая петля: итерация идёт на
    кадрах, видео покупается один раз (§6.3). Поэтому ниже одного кредита
    показываются четыре знака, а не два.
    """
    from app.services.credits import format_credits, price_micro

    assert format_credits(price_micro(0.0035), rounding="up") == "0.0105"
    # А ролик целиком — два знака: там четыре были бы шумом.
    assert format_credits(price_micro(5.625), rounding="up") == "16.88"


async def test_default_resolution_costs_more_than_the_spec_assumes(client, monkeypatch) -> None:
    """Разрешение по умолчанию — 1080p, а вся экономика спеки посчитана на 768P.

    Разница не косметическая: 0.33 против 0.19 за клип, то есть 23.76 кредита
    за двадцать четыре клипа вместо 13.68. Это решение владельца, а не
    дефект, — но оно должно быть видимым, а не всплыть в первом счёте.

    Генератор задан явно: в тестовом окружении каталог генераторов не
    разворачивается (`prompts/` вне git), и смета честно откатывается к
    справочной величине §6.1. Проверять здесь конфигурацию машины смысла нет
    — проверяется связь «разрешение → тариф → цена показа».
    """
    from app.services.quote import video_unit_usd

    monkeypatch.setattr(
        "app.services.vibecode_catalog.effective_video_generator_id",
        lambda project, node_type=None: "hailuo_2_3_fast",
    )
    # Провайдер тоже задан явно: маршрут зависит от конфигурации машины, а
    # прайс есть только у MiniMax — у relay-провайдеров тарифы плавают и
    # вносятся владельцем, выдумывать их в коде нельзя.
    monkeypatch.setattr(
        "app.services.media_route.video_provider_for",
        lambda slug: "minimax",
    )
    async with client.factory() as s:  # type: ignore[attr-defined]
        project = await s.get(Project, 1)
    assert project.video_resolution is None, "проект без явного разрешения"
    by_default, key_default = video_unit_usd(project)
    project.video_resolution = "720p"
    cheaper, key_720 = video_unit_usd(project)

    assert (by_default, cheaper) == (0.33, 0.19), (by_default, cheaper)
    assert "1080P" in key_default and "768P" in key_720
    # 24 клипа: то, что заплатит человек, против того, что написано в §6.2.
    assert round(by_default * 24 * 3, 2) == 23.76
    assert round(cheaper * 24 * 3, 2) == 13.68


async def test_cascade_shows_what_burns_and_what_dominates(client) -> None:
    """Каскад отвечает на вопрос «что сгорит», а не только «сколько».

    Пользователь, пошедший поменять промт, должен видеть радиус поражения до
    нажатия: разброс двадцатикратный, и главный расход в нём всегда один.
    """
    res = await client.get("/api/projects/1/steps/img_pr/quote?cascade=1")
    assert res.status_code == 200
    body = res.json()
    assert body["root"] == "img_pr"
    assert body["dominant"] == "video", "самым дорогим в каскаде обязано быть видео"
    codes = [s["step_code"] for s in body["steps"]]
    assert "video" in codes and "img" in codes
    assert float(body["price_credits"]) > 13.0


async def test_cascade_costs_more_than_its_root(client) -> None:
    """Цена каскада строго больше цены одного шага — иначе он не каскад."""
    one = (await client.get("/api/projects/1/steps/img_pr/quote")).json()
    all_of_it = (await client.get("/api/projects/1/steps/img_pr/quote?cascade=1")).json()
    assert all_of_it["price_micro"] > one["price_micro"]


async def test_missing_project_is_404_not_a_zero_price(client) -> None:
    """Смета несуществующего проекта — ошибка, а не бесплатно."""
    assert (await client.get("/api/projects/999/steps/video/quote")).status_code == 404


async def test_balance_is_empty_in_owner_mode(client) -> None:
    """У владельца кредитов нет вовсе: пустой ответ честнее нуля-остатка."""
    body = (await client.get("/api/billing/balance")).json()
    assert body["tenant_id"] is None
    assert body["balance_micro"] == 0
    assert body["entries"] == []


async def test_balance_shows_where_the_money_went(tmp_path, monkeypatch) -> None:
    """Резерв показан отдельной строкой.

    Деньги под идущим шагом уже вычтены из остатка. Без строки резерва
    клиент видит, что баланс упал, и не видит, куда, — а это первый вопрос,
    с которым он придёт.

    Ходим с настоящим токеном, а не подменой контекста: в режиме владельца
    слой личности сбрасывает арендатора на каждом запросе, и подменённый
    контекст до ручки просто не доедет.
    """
    import time

    import jwt

    from app.services import credit_ledger as cl

    secret = "секрет-биллинга-длиною-в-тридцать-два-байта-и-более"
    monkeypatch.setattr(settings, "billing_jwt_secret", secret)
    monkeypatch.setattr(settings, "studio_brand", "")
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bal.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    tenant = str(uuid.uuid4())
    async with factory() as s:
        await cl.topup(s, tenant, 20 * 10**6, memo="стартовый пакет")
        await cl.open_hold(s, tenant, project_id=1, step_code="video", amount_micro=13_680_000)
        await s.commit()

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    token = jwt.encode(
        {
            "sub": tenant,
            "email": "client@example.com",
            "brand": "videostudio",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        secret,
        algorithm="HS256",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/api/billing/balance", headers={"Authorization": f"Bearer {token}"})).json()
    await engine.dispose()

    assert body["tenant_id"] == tenant
    assert body["held_micro"] == 13_680_000
    assert body["balance_micro"] == 20 * 10**6 - 13_680_000
    assert body["entries"][0]["kind"] == "topup"
    assert body["entries"][0]["memo"] == "стартовый пакет"


@pytest.mark.no_harness_gate
async def test_quote_is_closed_by_identity_in_saas(tmp_path, monkeypatch) -> None:
    """Смета — тоже данные проекта: без токена её не отдают."""
    monkeypatch.setattr(settings, "billing_jwt_secret", "секрет-длиною-в-тридцать-два-байта-точно")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'closed.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/projects/1/steps/video/quote")).status_code == 401
        assert (await c.get("/api/billing/balance")).status_code == 401
    await engine.dispose()
