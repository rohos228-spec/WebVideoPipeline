"""Тарифы VIA (RUB, помесячно; год −25%). Кредиты по тарифу не сгорают."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.settings_billing import YEARLY_DISCOUNT


@dataclass(frozen=True)
class StreamLimits:
    llm: int
    agents: int
    images: int
    video: int


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    group: str  # trial | factory
    price_month: int
    credits_month: int
    streams: StreamLimits
    storage_gb: int
    max_videos: int | None = None
    features: tuple[str, ...] = field(default_factory=tuple)
    own_api: bool = False
    own_mechanics: bool = False
    priority_support: bool = False

    @property
    def price_year(self) -> int:
        return round(self.price_month * 12 * (1 - YEARLY_DISCOUNT))

    def price(self, period: str) -> int:
        return self.price_year if period == "year" else self.price_month

    def credits(self, period: str) -> int:
        return self.credits_month * (12 if period == "year" else 1)

    def to_json(self) -> dict:
        d = asdict(self)
        d.update(
            currency="RUB",
            price_year=self.price_year,
            credits_year=self.credits("year"),
            yearly_discount=YEARLY_DISCOUNT,
            thread_limit=self.streams.video,
            credits_expire=False,
        )
        d["features"] = list(self.features)
        return d


_BASE = ("Ассистент-оркестратор", "HTML-отчёты по проектам")
PLANS: dict[str, Plan] = {
    p.id: p
    for p in (
        Plan("probe", "Проба", "trial", 890, 500, StreamLimits(4, 0, 1, 1), 3, 10),
        Plan("start", "Старт", "trial", 1990, 2000, StreamLimits(4, 0, 2, 2), 5, 30),
        Plan("pro", "Про", "trial", 4990, 5100, StreamLimits(4, 0, 3, 3), 10, 80),
        Plan("workshop", "Мастерская", "factory", 9900, 10500, StreamLimits(10, 3, 4, 4), 50,
             features=_BASE + ("Шаблоны пайплайнов", "Библиотека промтов с рейтингами")),
        Plan("manufactory", "Мануфактура", "factory", 30000, 33000, StreamLimits(20, 5, 8, 8), 100,
             features=_BASE + ("Расписания и автозапуск серий", "Маскот-оркестратор",
                               "Брендовые стили и персонажи", "Бэкапы проектов")),
        Plan("factory", "Завод", "factory", 89000, 95000, StreamLimits(40, 10, 16, 16), 500,
             features=_BASE + ("Мультиагенты", "Командный доступ с ролями", "Приоритетная очередь",
                               "Аналитика", "Автопубликация"),
             own_mechanics=True, priority_support=True),
        Plan("conveyor", "Конвейер", "factory", 139000, 160000, StreamLimits(60, 20, 32, 32), 2048,
             features=_BASE + ("Всё из «Завода»", "Экспорт без брендинга VIA"),
             own_api=True, own_mechanics=True, priority_support=True),
    )
}

# Без подписки: минимальные потоки, но не безлимит.
FREE_STREAMS = StreamLimits(llm=1, agents=0, images=1, video=1)
PERIODS = ("month", "year")


def get_plan(plan_id: str | None) -> Plan | None:
    return PLANS.get((plan_id or "").strip().lower())
