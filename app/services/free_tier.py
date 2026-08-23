"""Бесплатный уровень: всё до первой генерации видео на аккаунте.

Решение владельца (`docs/SAAS-PIVOT.md` §5.7): человек получает готовую
раскадровку — сценарий, кадры, контактный лист — бесплатно и платит только за
рендер. Витрина честная: качество видно до оплаты, а момент оплаты наступает
там, где ценность уже доказана.

**Экономика подарка.** Полная себестоимость довидеочасти — $0.97, то есть 17%
ролика. Это не мелочь на аккаунт, а мелочь на аккаунт, помноженная на всех,
кто зайдёт посмотреть, поэтому предохранители в §5.7 названы обязательными.
Здесь они и живут.

**Три границы, и каждая закрывает свою дыру.**

1. *Видео не бесплатно никогда.* Это 82% себестоимости, и именно оно
   отделяет «посмотреть» от «получить». Граница проходит по аккаунту, а не по
   проекту: иначе подарок повторялся бы с каждым новым проектом и стоил бы
   $0.97 за штуку без всякого предела.
2. *Потолок расхода.* Раскадровку можно перезапускать, и каждый перезапуск
   стоит платформе денег. Без потолка бесплатный аккаунт — это открытый кран.
3. *Число бесплатных проектов.* Тот же кран, только с другой стороны: завести
   двадцать проектов по $0.97 дешевле, чем один за $3.

**Счётчиков нет — есть леджер.** §5.7 требует вести учёт проводками с нулевой
дельтой и `kind='promo'`, где `cost_usd` заполнен фактической
себестоимостью. Отдельная таблица счётчиков разошлась бы с проводками при
первой же ручной правке, и разошлась бы молча. Отсюда всё состояние
бесплатного уровня — производная от леджера, а не хранимая величина.

**Чего здесь нет.** §5.7 требует подтверждённой почты до первого шага. Токен
биллинга несёт `sub`, `email` и `brand`, но не `emailVerified` — проверить
нечем. Это правка на стороне биллинга (добавить поле в
`auth.service.ts::sign`), и до неё требование не выполняется.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.models import CreditEntry, CreditHold

#: Шаги, которые бесплатными не бывают. Всё, что идёт от рендера и дальше:
#: видео — 82% себестоимости, а озвучка, музыка и эффекты идут после него и
#: потому в подарок не попадают по построению конвейера (§6.5).
PAID_STEPS: frozenset[str] = frozenset({"video", "audio", "music", "sfx_gen"})

#: Метка промо-проводки. Отличается от `memo` списания: по ней считается и
#: подаренная себестоимость, и число бесплатных проектов.
PROMO_KIND = "promo"


class FreeTierExhausted(RuntimeError):
    """Подарок кончился: нужен платный баланс, а не отказ шага.

    Наружу это выглядит так же, как нехватка кредитов, и обрабатывается так
    же: проект ждёт пополнения, а не откатывается назад.
    """


@dataclass(frozen=True)
class FreeTierState:
    """Состояние подарка. Всё посчитано по леджеру, ничего не хранится."""

    active: bool
    granted_usd: float
    projects: tuple[int, ...]
    reason: str = ""

    @property
    def project_count(self) -> int:
        return len(self.projects)


async def free_tier_state(session: Any, tenant_id: str) -> FreeTierState:
    """Действует ли ещё бесплатный уровень и сколько уже подарено."""
    from app.settings import settings

    if not getattr(settings, "free_tier_enabled", True):
        return FreeTierState(active=False, granted_usd=0.0, projects=(), reason="выключен настройкой")

    granted = float(
        (
            await session.execute(
                select(func.coalesce(func.sum(CreditEntry.cost_usd), 0)).where(
                    CreditEntry.tenant_id == tenant_id, CreditEntry.kind == PROMO_KIND
                )
            )
        ).scalar_one()
        or 0.0
    )
    projects = tuple(
        sorted(
            int(pid)
            for pid in (
                await session.execute(
                    select(CreditEntry.project_id)
                    .where(
                        CreditEntry.tenant_id == tenant_id,
                        CreditEntry.kind == PROMO_KIND,
                        CreditEntry.project_id.is_not(None),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    )

    if await _video_was_paid(session, tenant_id):
        # Граница по аккаунту, а не по проекту: после первой оплаченной
        # генерации видео тарифицируется всё и везде.
        return FreeTierState(
            active=False,
            granted_usd=granted,
            projects=projects,
            reason="видео уже оплачивалось",
        )
    return FreeTierState(active=True, granted_usd=granted, projects=projects)


async def check_step_allowed(
    session: Any, tenant_id: str, *, step_code: str, project_id: int, cost_usd: float
) -> bool:
    """Пойдёт ли шаг бесплатно. `False` — платить обычным порядком.

    Отказ — не «нет», а `FreeTierExhausted`: клиент упёрся в предохранитель, и
    ему нужно пополнение, а не сообщение об ошибке шага.
    """
    from app.settings import settings

    if step_code in PAID_STEPS:
        return False
    state = await free_tier_state(session, tenant_id)
    if not state.active:
        return False

    cap = float(getattr(settings, "free_tier_spend_cap_usd", 3.0))
    if cap > 0 and state.granted_usd + max(0.0, cost_usd) > cap:
        raise FreeTierExhausted(
            f"бесплатный уровень исчерпан: подарено ${state.granted_usd:.2f} "
            f"из ${cap:.2f}. Дальше нужен баланс."
        )

    max_projects = int(getattr(settings, "free_tier_max_projects", 1))
    if max_projects > 0 and project_id not in state.projects and state.project_count >= max_projects:
        raise FreeTierExhausted(
            f"бесплатно можно вести {max_projects} проект(а): уже заведено "
            f"{state.project_count}. Дальше нужен баланс."
        )
    return True


async def record_promo(
    session: Any,
    tenant_id: str,
    *,
    project_id: int,
    step_code: str,
    cost_usd: float,
    ref_table: str = "",
    ref_ids: list[int] | None = None,
    memo: str = "",
) -> None:
    """Записать подарок: нулевая дельта, фактическая себестоимость.

    Нулевая дельта не формальность. Проводка не двигает баланс — платит
    платформа, — но обязана существовать: из этих строк складывается и
    «сколько стоит привлечение», и все предохранители выше. Отчёт по марже
    считается по тому же леджеру, а не по отдельной подсистеме (§5.7).
    """
    import uuid
    from datetime import UTC, datetime

    from app.services.credit_ledger import ensure_account

    await ensure_account(session, tenant_id)
    session.add(
        CreditEntry(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            hold_id=None,
            delta_micro=0,
            kind=PROMO_KIND,
            cost_usd=round(max(0.0, float(cost_usd)), 8),
            project_id=int(project_id) or None,
            step_code=step_code,
            ref_table=ref_table,
            ref_ids=list(ref_ids or []),
            memo=memo or f"бесплатный уровень: {step_code}",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await session.flush()


async def _video_was_paid(session: Any, tenant_id: str) -> bool:
    """Была ли на аккаунте оплаченная генерация видео.

    Признак — закрытый холд под шагом `video`. Не проводка: у списания с
    нулевым фактом (шаг сходил впустую) `cost_usd` тоже ноль, а холд под
    видео означает ровно то, что нужно, — клиент дошёл до рендера и заплатил.
    """
    found = (
        await session.execute(
            select(func.count())
            .select_from(CreditHold)
            .where(
                CreditHold.tenant_id == tenant_id,
                CreditHold.step_code == "video",
                CreditHold.state == "settled",
            )
        )
    ).scalar_one()
    return bool(found)
