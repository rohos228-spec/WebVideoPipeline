"""Админский учёт расходов: доступ, агрегаты, recount, reconcile.

Дашборд — только владелец/админ: участнику путь обязан отдавать 404 (тем же
слоем `app/web/identity.py`, что закрывает `/api/llm-costs`). Числа считаются
в границах арендатора запроса — на SQLite RLS нет, поэтому проверяем и
зеркальный фильтр `tenant_id`, иначе тест на изоляцию был бы декорацией.

Деньги в проверках считаются руками: себестоимость в USD и кредиты
(MICRO = 1_000_000) — разные величины, и смешивать их в ожиданиях нельзя.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models import CreditEntry, LlmCall, MediaCall, Project
from app.web.api import create_app
from app.web.deps import get_session
from tests import accounts_harness as ah

_MSK = timezone(timedelta(hours=3), "Europe/Moscow")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _period(days_back: int = 1) -> dict[str, str]:
    today = datetime.now(_MSK).date()
    return {
        "from": (today - timedelta(days=days_back)).isoformat(),
        "to": today.isoformat(),
    }


def _today_local() -> str:
    return datetime.now(_MSK).date().isoformat()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    ah.configure(monkeypatch)
    engine, factory = await ah.make_engine(tmp_path / "admin-costs.db")
    ah.bind_identity_session(monkeypatch, factory)

    async def _gen():
        async with factory() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    member = await ah.make_account(factory, email="member@studio.local")
    admin = await ah.make_admin(factory)

    now = _now()
    tenant = admin.user_id
    async with factory() as s:
        s.add(Project(id=1, tenant_id=tenant, slug="p1", title="Проект 1", topic="тема"))
        llm_ok = LlmCall(
            tenant_id=tenant,
            project_id=1,
            node_key="n1",
            logical_call_id="lc-ok",
            model="gpt-5.5",
            served_model="gpt-5.5",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=0.002,
            created_at=now,
        )
        # Цены нет: токены есть, а модели в llm_prices.json нет.
        # Новая конвенция: неизвестная цена — NULL, а не 0 (миграция 0018).
        llm_unpriced = LlmCall(
            tenant_id=tenant,
            project_id=1,
            node_key="n2",
            logical_call_id="lc-unpriced",
            model="no-such-llm",
            served_model="no-such-llm",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=None,
            created_at=now,
        )
        # Старая конвенция: та же неизвестная цена записана нулём — такие
        # строки считаем нерасцененными (страховка) и показываем счётчиком.
        s.add(
            LlmCall(
                tenant_id=tenant,
                project_id=1,
                node_key="n2-legacy",
                logical_call_id="lc-legacy-zero",
                model="no-such-llm-legacy",
                served_model="no-such-llm-legacy",
                prompt_tokens=500,
                completion_tokens=250,
                total_tokens=750,
                cost_usd=0.0,
                created_at=now,
            )
        )
        media_ok = MediaCall(
            tenant_id=tenant,
            project_id=1,
            provider="outsee",
            kind="image",
            model="nano-banana-2",
            units=2.0,
            unit="item",
            cost_usd=0.04,
            unpriced=False,
            created_at=now,
        )
        # unpriced, но цена в прайсе уже появилась — recount обязан оценить.
        media_recountable = MediaCall(
            tenant_id=tenant,
            project_id=1,
            provider="outsee",
            kind="image",
            model="nano-banana-2",
            units=1.0,
            unit="item",
            cost_usd=0.0,
            unpriced=True,
            created_at=now,
        )
        # unpriced, и цены по-прежнему нет — врать нулём нельзя.
        media_unknown = MediaCall(
            tenant_id=tenant,
            project_id=1,
            provider="acme",
            kind="image",
            model="mystery-model",
            units=1.0,
            unit="item",
            cost_usd=0.0,
            unpriced=True,
            created_at=now,
        )
        # Вне периода по умолчанию (30 дней): проверяем границу окна.
        media_old = MediaCall(
            tenant_id=tenant,
            project_id=1,
            provider="outsee",
            kind="image",
            model="nano-banana-2",
            units=1.0,
            unit="item",
            cost_usd=1.0,
            unpriced=False,
            created_at=now - timedelta(days=40),
        )
        s.add_all([llm_ok, llm_unpriced, media_ok, media_recountable, media_unknown, media_old])
        await s.flush()

        # settle со ссылками на вызовы (step_billing): сумма подтверждается фактом.
        s.add(
            CreditEntry(
                id=str(uuid.uuid4()),
                tenant_id=tenant,
                kind="settle",
                delta_micro=-120_000,
                cost_usd=Decimal("0.04"),
                margin=Decimal("3.0"),
                ref_table="llm_calls+media_calls",
                ref_ids=[1, 2],
                project_id=1,
                created_at=now,
            )
        )
        # settle по оценке каталога (kie/outsee-create): ссылок нет, факта нет.
        s.add(
            CreditEntry(
                id=str(uuid.uuid4()),
                tenant_id=tenant,
                kind="settle",
                delta_micro=-1_500_000,
                cost_usd=Decimal("0.5"),
                margin=Decimal("3.0"),
                ref_table="create_generations",
                ref_ids=[],
                project_id=0,
                created_at=now,
            )
        )
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {
            "client": client,
            "member": member,
            "admin": admin,
            "factory": factory,
            "recountable_id": media_recountable.id,
            "priced_id": media_ok.id,
            "llm_priced_id": llm_ok.id,
            "llm_null_id": llm_unpriced.id,
        }
    await engine.dispose()


# ── доступ ───────────────────────────────────────────────────────────────────


async def test_member_gets_404_on_every_admin_cost_route(env) -> None:
    """Участнику — 404, а не 403: существование инструментов владельца — тайна."""
    client = env["client"]
    paths = (
        "/api/admin/costs/overview",
        "/api/admin/costs/by-day",
        "/api/admin/costs/by-model",
        "/api/admin/costs/by-project",
        "/api/admin/costs/prices",
        "/api/admin/costs/reconcile",
    )
    for path in paths:
        res = await client.get(path, headers=env["member"].auth)
        assert res.status_code == 404, f"{path} отдал {res.status_code} участнику"

    recount = await client.post(
        "/api/admin/costs/recount", json={"dry_run": True}, headers=env["member"].auth
    )
    assert recount.status_code == 404


async def test_admin_reaches_every_route(env) -> None:
    client = env["client"]
    for path in (
        "/api/admin/costs/overview",
        "/api/admin/costs/by-day",
        "/api/admin/costs/by-model",
        "/api/admin/costs/by-project",
        "/api/admin/costs/prices",
        "/api/admin/costs/reconcile",
    ):
        res = await client.get(path, headers=env["admin"].auth)
        assert res.status_code == 200, f"{path} отдал {res.status_code}: {res.text}"


# ── overview ─────────────────────────────────────────────────────────────────


async def test_overview_totals_split_usd_and_credits(env) -> None:
    body = (
        await env["client"].get(
            "/api/admin/costs/overview", params=_period(), headers=env["admin"].auth
        )
    ).json()

    totals = body["totals"]
    # Факт: llm 0.002 + media 0.04. Старая строка (40 дней) вне периода.
    assert totals["actual_usd"] == 0.042
    assert totals["calls"] == 6
    # Без цены: два LLM (NULL и старый ноль) + две unpriced медиа-строки.
    assert totals["unpriced_calls"] == 4
    assert totals["price_coverage_pct"] == 33.33
    # Кредиты — отдельная величина: тариф ×3, округление вверх.
    assert totals["credits_micro"] == 126_000
    assert totals["credits_charged"] == 0.126
    # Списания леджера, расширение контракта: со ссылками и без.
    assert totals["charged_linked_usd"] == 0.12
    assert totals["charged_unlinked_usd"] == 1.5
    # Старая конвенция: неизвестная цена LLM записана нулём — отдельный счётчик.
    assert totals["legacy_zero_calls"] == 1


async def test_llm_unknown_price_is_stored_as_null(env) -> None:
    """Миграция 0018: неизвестная цена — NULL. Если ORM молча записал 0.0,
    тест на `unpriced` прошёл бы и без NULL-ветки — проверяем именно хранение."""
    async with env["factory"]() as s:
        row = await s.get(LlmCall, env["llm_null_id"])
        assert row is not None
        assert row.cost_usd is None
        legacy = (
            await s.execute(
                select(LlmCall).where(LlmCall.model == "no-such-llm-legacy")
            )
        ).scalar_one()
        assert legacy.cost_usd == 0.0, "старая конвенция пишет ноль — он и остаётся"

    body = (
        await env["client"].get(
            "/api/admin/costs/overview", params=_period(), headers=env["admin"].auth
        )
    ).json()
    # NULL-строка входит в unpriced, но не в legacy-счётчик (там только нули).
    assert body["totals"]["unpriced_calls"] == 4
    assert body["totals"]["legacy_zero_calls"] == 1


async def test_overview_by_kind(env) -> None:    body = (
        await env["client"].get(
            "/api/admin/costs/overview", params=_period(), headers=env["admin"].auth
        )
    ).json()
    kinds = {row["kind"]: row for row in body["by_kind"]}
    assert set(kinds) == {"image", "llm"}

    image = kinds["image"]
    assert image["calls"] == 3
    assert image["units"] == 4.0
    assert image["actual_usd"] == 0.04
    assert image["unpriced_calls"] == 2
    assert image["credits_charged"] == 0.12

    llm = kinds["llm"]
    assert llm["calls"] == 3
    assert llm["units"] == 3750.0
    assert llm["actual_usd"] == 0.002
    assert llm["unpriced_calls"] == 2
    assert llm["credits_charged"] == 0.006


async def test_overview_default_period_is_30_days(env) -> None:
    """Старая строка не должна всплывать, если период не задан."""
    body = (await env["client"].get("/api/admin/costs/overview", headers=env["admin"].auth)).json()
    assert body["totals"]["actual_usd"] == 0.042

    wide = dict(_period())
    wide["from"] = (datetime.now(_MSK).date() - timedelta(days=60)).isoformat()
    long_body = (
        await env["client"].get(
            "/api/admin/costs/overview", params=wide, headers=env["admin"].auth
        )
    ).json()
    assert long_body["totals"]["actual_usd"] == 1.042


async def test_overview_rejects_bad_period(env) -> None:
    client = env["client"]
    bad = await client.get(
        "/api/admin/costs/overview", params={"from": "2026-13-40"}, headers=env["admin"].auth
    )
    assert bad.status_code == 400
    swapped = await client.get(
        "/api/admin/costs/overview",
        params={"from": "2026-10-22", "to": "2026-10-01"},
        headers=env["admin"].auth,
    )
    assert swapped.status_code == 400


async def test_other_tenants_rows_are_invisible(env) -> None:
    """На SQLite RLS нет: изоляцию держит явный `tenant_id`-фильтр в запросах."""
    async with env["factory"]() as s:
        s.add(
            MediaCall(
                tenant_id="someone-else",
                project_id=None,
                provider="outsee",
                kind="image",
                model="nano-banana-2",
                units=10.0,
                unit="item",
                cost_usd=9.99,
                unpriced=True,
                created_at=_now(),
            )
        )
        s.add(
            LlmCall(
                tenant_id="someone-else",
                project_id=None,
                logical_call_id="lc-foreign",
                model="gpt-5.5",
                prompt_tokens=10_000,
                completion_tokens=10_000,
                total_tokens=20_000,
                cost_usd=9.99,
                created_at=_now(),
            )
        )
        s.add(
            CreditEntry(
                id=str(uuid.uuid4()),
                tenant_id="someone-else",
                kind="settle",
                delta_micro=-9_000_000,
                cost_usd=Decimal("3.0"),
                margin=Decimal("3.0"),
                ref_table="create_generations",
                ref_ids=[],
                project_id=0,
                created_at=_now(),
            )
        )
        await s.commit()

    body = (
        await env["client"].get(
            "/api/admin/costs/overview", params=_period(), headers=env["admin"].auth
        )
    ).json()
    assert body["totals"]["actual_usd"] == 0.042
    assert body["totals"]["calls"] == 6
    assert body["totals"]["charged_unlinked_usd"] == 1.5

    prices = (
        await env["client"].get(
            "/api/admin/costs/prices",
            params={"kind": "image", "model": "nano-banana-2"},
            headers=env["admin"].auth,
        )
    ).json()
    row = next(r for r in prices if r["model"] == "nano-banana-2")
    assert row["calls_30d"] == 2, "чужие вызовы не должны попасть в статистику прайса"


# ── разрезы ──────────────────────────────────────────────────────────────────


async def test_by_day_groups_by_moscow_day(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/by-day", params=_period(), headers=env["admin"].auth
        )
    ).json()
    by_kind = {row["kind"]: row for row in rows}
    assert set(by_kind) == {"image", "llm"}
    for row in rows:
        assert row["day"] == _today_local()
    assert by_kind["image"]["calls"] == 3
    assert by_kind["image"]["actual_usd"] == 0.04
    assert by_kind["llm"]["calls"] == 3
    assert by_kind["llm"]["unpriced_calls"] == 2


async def test_by_day_kind_filter(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/by-day",
            params={**_period(), "kind": "llm"},
            headers=env["admin"].auth,
        )
    ).json()
    assert [row["kind"] for row in rows] == ["llm"]


async def test_by_model_keeps_priced_and_unpriced_apart(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/by-model", params=_period(), headers=env["admin"].auth
        )
    ).json()
    keyed = {(row["kind"], row["provider"], row["model"]): row for row in rows}

    priced = keyed[("image", "outsee", "nano-banana-2")]
    assert priced["calls"] == 2
    assert priced["units"] == 3.0
    assert priced["unit"] == "item"
    assert priced["actual_usd"] == 0.04
    assert priced["avg_usd"] == 0.02
    assert priced["has_price"] is True

    unknown = keyed[("image", "acme", "mystery-model")]
    assert unknown["calls"] == 1
    assert unknown["actual_usd"] == 0.0
    assert unknown["unpriced_calls"] == 1
    assert unknown["has_price"] is False

    llm = keyed[("llm", "relay", "gpt-5.5")]
    assert llm["calls"] == 1
    assert llm["units"] == 1500.0
    assert llm["has_price"] is True

    llm_unknown = keyed[("llm", "relay", "no-such-llm")]
    assert llm_unknown["has_price"] is False
    assert llm_unknown["unpriced_calls"] == 1, "NULL-цена — unpriced, а не 0"

    llm_legacy = keyed[("llm", "relay", "no-such-llm-legacy")]
    assert llm_legacy["has_price"] is False
    assert llm_legacy["unpriced_calls"] == 1, "старый ноль тоже без цены (страховка)"


async def test_by_project_has_titles(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/by-project", params=_period(), headers=env["admin"].auth
        )
    ).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["project_id"] == 1
    assert row["project_title"] == "Проект 1"
    assert row["calls"] == 6
    assert row["actual_usd"] == 0.042


# ── прайс ────────────────────────────────────────────────────────────────────


async def test_prices_flags_models_without_price(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/prices", headers=env["admin"].auth
        )
    ).json()
    keyed = {(row["kind"], row["provider"], row["model"]): row for row in rows}

    banana = keyed[("image", "outsee", "nano-banana-2")]
    assert banana["has_price"] is True
    assert banana["usd_per_unit"] == 0.02
    assert banana["calls_30d"] == 2
    assert banana["unpriced_30d"] == 1

    # Цена за клип (usd_per_call), а не за единицу: «без цены» быть не должно.
    veo = keyed[("video", "outsee", "veo-3-1-lite")]
    assert veo["usd_per_call"] == 0.45
    assert veo["has_price"] is True

    mystery = keyed[("image", "acme", "mystery-model")]
    assert mystery["has_price"] is False
    assert mystery["calls_30d"] == 1
    assert mystery["unpriced_30d"] == 1

    llm = keyed[("llm", "relay", "gpt-5.5")]
    assert llm["has_price"] is True
    assert llm["calls_30d"] == 1

    llm_null = keyed[("llm", "relay", "no-such-llm")]
    assert llm_null["has_price"] is False
    assert llm_null["calls_30d"] == 1
    assert llm_null["unpriced_30d"] == 1, "NULL-цена в прайсе не превращается в 0"

    llm_legacy = keyed[("llm", "relay", "no-such-llm-legacy")]
    assert llm_legacy["has_price"] is False
    assert llm_legacy["unpriced_30d"] == 1


async def test_prices_kind_filter(env) -> None:
    rows = (
        await env["client"].get(
            "/api/admin/costs/prices", params={"kind": "video"}, headers=env["admin"].auth
        )
    ).json()
    assert rows
    assert {row["kind"] for row in rows} == {"video"}


# ── recount ──────────────────────────────────────────────────────────────────


async def test_recount_dry_run_counts_but_does_not_write(env) -> None:
    body = (
        await env["client"].post(
            "/api/admin/costs/recount",
            json={**_period(), "dry_run": True},
            headers=env["admin"].auth,
        )
    ).json()
    # Кандидаты: две unpriced медиа + два LLM без цены (NULL и старый ноль).
    assert body["dry_run"] is True
    assert body["scanned"] == 4
    assert body["priced"] == 1
    assert body["still_unpriced"] == 3
    assert body["added_usd"] == 0.02

    async with env["factory"]() as s:
        row = await s.get(MediaCall, env["recountable_id"])
        assert row is not None
        assert row.unpriced is True, "dry_run записал цену в базу"
        assert row.cost_usd == 0.0


async def test_recount_apply_prices_only_unpriced(env) -> None:
    first = (
        await env["client"].post(
            "/api/admin/costs/recount",
            json={**_period(), "dry_run": False},
            headers=env["admin"].auth,
        )
    ).json()
    assert first["priced"] == 1
    assert first["added_usd"] == 0.02

    async with env["factory"]() as s:
        recountable = await s.get(MediaCall, env["recountable_id"])
        assert recountable is not None
        assert recountable.unpriced is False
        assert recountable.cost_usd == 0.02
        # Уже оценённое не трогается.
        priced = await s.get(MediaCall, env["priced_id"])
        assert priced is not None
        assert priced.cost_usd == 0.04
        assert priced.unpriced is False

    # Повторный прогон: оценивать больше нечего, остались только безнадёжные.
    second = (
        await env["client"].post(
            "/api/admin/costs/recount",
            json={**_period(), "dry_run": True},
            headers=env["admin"].auth,
        )
    ).json()
    assert second["priced"] == 0


async def test_recount_respects_period(env) -> None:
    """Строки вне периода recount не сканирует — иначе кнопка «за месяц»
    правила бы всю историю."""
    body = (
        await env["client"].post(
            "/api/admin/costs/recount",
            json={
                "from": (datetime.now(_MSK).date() - timedelta(days=90)).isoformat(),
                "to": (datetime.now(_MSK).date() - timedelta(days=60)).isoformat(),
                "dry_run": True,
            },
            headers=env["admin"].auth,
        )
    ).json()
    assert body["scanned"] == 0


async def test_recount_mode_all_rescans_whole_window(env) -> None:
    """mode=all — режим CLI/владельца: scanned считает все строки окна, уже
    оценённые переоцениваются по текущему прайсу (added_usd — дельта)."""
    body = (
        await env["client"].post(
            "/api/admin/costs/recount",
            json={**_period(), "dry_run": True, "mode": "all"},
            headers=env["admin"].auth,
        )
    ).json()
    # В окне 3 медиа + 3 LLM; сменились цены у одной медиа и у оценённого LLM.
    assert body["mode"] == "all"
    assert body["scanned"] == 6
    assert body["priced"] == 2
    assert body["still_unpriced"] == 3
    # 0.02 за медиа минус small-дельта переоценки gpt-5.5 (0.002 → цена прайса).
    assert 0.0195 < body["added_usd"] < 0.0201

    # dry_run: база не тронута — цена LLM осталась прежней.
    async with env["factory"]() as s:
        llm = await s.get(LlmCall, env["llm_priced_id"])
        assert llm is not None
        assert llm.cost_usd == 0.002


async def test_recount_rejects_unknown_mode(env) -> None:
    res = await env["client"].post(
        "/api/admin/costs/recount",
        json={**_period(), "dry_run": True, "mode": "whatever"},
        headers=env["admin"].auth,
    )
    assert res.status_code == 400


async def test_recount_is_dry_run_by_default(env) -> None:
    """Без dry_run в теле ручка ничего не пишет: запись — только явным false."""
    body = (
        await env["client"].post(
            "/api/admin/costs/recount", json=_period(), headers=env["admin"].auth
        )
    ).json()
    assert body["dry_run"] is True
    assert body["mode"] == "unpriced"
    async with env["factory"]() as s:
        row = await s.get(MediaCall, env["recountable_id"])
        assert row is not None
        assert row.unpriced is True


# ── reconcile ────────────────────────────────────────────────────────────────


async def test_reconcile_splits_charges_by_ref_table_and_link(env) -> None:
    body = (
        await env["client"].get(
            "/api/admin/costs/reconcile", params=_period(), headers=env["admin"].auth
        )
    ).json()

    assert body["actual_usd"] == 0.042
    # Все списания периода: 0.12 + 1.5 кредита по курсу 1:1.
    assert body["charged_usd"] == 1.62
    assert body["charged_linked_usd"] == 0.12
    assert body["charged_unlinked_usd"] == 1.5
    # Недоказуемая часть: charged / margin = 1.5 / 3.
    assert body["expected_unlinked_usd"] == 0.5
    assert body["delta_usd"] == 1.578
    assert body["ratio"] == round(1.62 / 0.042, 6)

    by_table = {row["ref_table"]: row for row in body["by_ref_table"]}
    assert set(by_table) == {"llm_calls+media_calls", "create_generations"}

    linked = by_table["llm_calls+media_calls"]
    assert linked["entries"] == 1
    assert linked["charged_usd"] == 0.12
    assert linked["actual_usd"] == 0.04, "фактовая база проводки — её cost_usd"
    assert linked["charged_unlinked_usd"] == 0.0

    unlinked = by_table["create_generations"]
    assert unlinked["entries"] == 1
    assert unlinked["charged_usd"] == 1.5
    assert unlinked["actual_usd"] == 0.0, "ссылок на вызовы нет — факта тоже"
    assert unlinked["expected_unlinked_usd"] == 0.5
