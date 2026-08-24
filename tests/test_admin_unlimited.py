"""У админа не бесконечный баланс, а отсутствие кассы. Это разные вещи.

Соблазн выдать админу `10**18` микрокредитов велик: ничего не меняется в коде,
всё «просто работает». Так нельзя по двум причинам.

Первая арифметическая: холд под шаг вычитается из остатка, проводки
накапливаются, и однажды сумма упирается в границу `BigInteger` посреди
прогона.

Вторая важнее: огромный баланс — это ложь в леджере. Проводки перестают
сходиться с себестоимостью, и инвариант `balance = Σ delta − Σ held`
показывает расхождение на пустом месте, то есть перестаёт быть сигналом.
Леджер — единственный источник правды по деньгам (`docs/SAAS-PIVOT.md` §5.3),
и класть в него неправду ради удобства нельзя.

Поэтому: шаг админа не тарифицируется вовсе, холдов под ним не возникает,
проводок не появляется, а наружу уезжает флаг `unlimited`.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models import CreditEntry, CreditHold
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

# asyncio_mode = "auto" в pyproject: асинхронные тесты подхватываются сами,
# а модульная метка вешала бы её и на синхронные — pytest на это ругается.


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "admin.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "member": member, "admin": admin, "factory": factory}
    await engine.dispose()


# ── наружу ───────────────────────────────────────────────────────────────────


async def test_me_reports_unlimited_for_admin(env) -> None:
    body = (await env["client"].get("/api/me", headers=env["admin"].auth)).json()
    assert body["unlimited"] is True
    assert body["role"] == "admin"


async def test_me_reports_a_real_balance_for_member(env) -> None:
    body = (await env["client"].get("/api/me", headers=env["member"].auth)).json()
    assert body["unlimited"] is False
    assert body["balance_micro"] == 0


async def test_admin_balance_is_not_a_huge_number(env) -> None:
    """Флаг, а не цифра. Огромная цифра — ложь, которая сойдётся с чем угодно."""
    body = (await env["client"].get("/api/billing/balance", headers=env["admin"].auth)).json()
    assert body["unlimited"] is True
    assert body["balance_micro"] == 0
    assert body["held_micro"] == 0


async def test_member_balance_is_not_unlimited(env) -> None:
    body = (await env["client"].get("/api/billing/balance", headers=env["member"].auth)).json()
    assert body["unlimited"] is False


# ── касса ────────────────────────────────────────────────────────────────────


async def test_admin_step_is_not_billed_at_all(env) -> None:
    """Ни холда, ни проводки. Пустой леджер после шага админа — это цель.

    Проверяется через `step_billing` напрямую: касса врезана в исполнение
    шага, и подниматься до HTTP тут незачем — решение принимается здесь.
    """
    from app.services.step_billing import step_billing
    from app.services.studio_auth import ROLE_ADMIN, StudioIdentity, set_identity
    from app.services.tenant import tenant_scope

    class _Project:
        id = 1
        video_resolution = "720p"

    who = StudioIdentity(user_id=env["admin"].user_id, email=env["admin"].email, role=ROLE_ADMIN, epoch=0)
    set_identity(who)
    try:
        with tenant_scope(env["admin"].user_id):
            async with step_billing(_Project(), "video", frames=24) as bill:
                assert bill.billed is False
                assert bill.hold_id is None
    finally:
        set_identity(None)

    async with env["factory"]() as s:
        holds = (await s.execute(select(func.count()).select_from(CreditHold))).scalar_one()
        entries = (await s.execute(select(func.count()).select_from(CreditEntry))).scalar_one()
    assert holds == 0, "под шагом админа возник резерв — кассы у него быть не должно"
    assert entries == 0, "шаг админа оставил проводку — леджер обязан остаться пустым"


async def test_member_step_creates_a_hold(env) -> None:
    """Обратная сторона: выключить кассу «для всех» этим тестом нельзя.

    Без него правка «админ не платит» легко превращается в «никто не платит»,
    и заметить это будет нечем. Поэтому здесь не «не упало», а положительное
    утверждение: под шагом участника ВОЗНИК резерв, а после выхода из блока он
    закрылся проводкой.
    """
    from app.services import credit_ledger as cl
    from app.services.step_billing import step_billing
    from app.services.studio_auth import ROLE_MEMBER, StudioIdentity, set_identity
    from app.services.tenant import tenant_scope

    class _Project:
        id = 1
        video_resolution = "720p"

    # Денег кладём с запасом: тест про то, что касса вмешалась, а не про то,
    # что баланса не хватило, — второе проверяется отдельно.
    async with env["factory"]() as s:
        with tenant_scope(env["member"].user_id):
            await cl.topup(s, env["member"].user_id, 500_000_000, memo="тестовое пополнение")
            await s.commit()

    who = StudioIdentity(user_id=env["member"].user_id, email=env["member"].email, role=ROLE_MEMBER, epoch=0)
    set_identity(who)
    try:
        with tenant_scope(env["member"].user_id):
            async with step_billing(_Project(), "video", frames=24) as bill:
                assert bill.tenant_id == env["member"].user_id
                assert bill.hold_id is not None, "резерв под шагом участника не возник"

                async with env["factory"]() as s:
                    held = (
                        await s.execute(
                            select(func.count()).select_from(CreditHold).where(CreditHold.state == "held")
                        )
                    ).scalar_one()
                assert held == 1, "живого резерва под идущим шагом нет"
    finally:
        set_identity(None)

    async with env["factory"]() as s:
        open_holds = (
            await s.execute(select(func.count()).select_from(CreditHold).where(CreditHold.state == "held"))
        ).scalar_one()
    assert open_holds == 0, "резерв остался висеть после закрытия шага"


async def test_admin_flag_comes_from_the_role_and_not_from_the_context(env) -> None:
    """Без личности `current_is_admin` обязан быть False, а не падать.

    Функцию зовут из кассы, в том числе из фоновых задач воркера, где личности
    нет вовсе. `None.is_admin` там был бы падением на ровном месте.
    """
    from app.services.studio_auth import current_is_admin, set_identity

    set_identity(None)
    assert current_is_admin() is False
