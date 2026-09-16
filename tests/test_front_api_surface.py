"""Каждая ручка, которую зовёт фронт, должна быть кому-то разрешена.

В SaaS половина API закрыта арендаторам списком разрешённого
(`identity.TENANT_ALLOWED_PREFIXES`): парк машин, обозреватель базы, ручки к
провайдерам мимо кассы, промты платформы, себестоимость. Закрыты правильно —
но КНОПКИ, которые их зовут, оставались видны, и клиент нажимал на то, что
молча отвечает 404. Кнопка, ведущая в никуда, хуже отсутствующей: она обещает
возможность.

Ловить это глазами не выйдет: адресов во фронте под сотню, и каждый новый
экран добавляет свои. Поэтому здесь список сверяется автоматически — каждый
путь обязан быть либо разрешён арендатору, либо назван инструментом
владельца ЯВНО. Новый путь, не попавший ни туда ни сюда, роняет тест: это не
придирка, а вопрос «а кому ты это показываешь».

Тест не проверяет, что интерфейс прячет кнопку, — этого он знать не может.
Он проверяет, что решение принято и записано.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.web.identity import DOC_PATHS, PUBLIC_PATHS, TENANT_ALLOWED_PREFIXES

WEB_SRC = Path(__file__).resolve().parent.parent / "web" / "src"

#: Ручки инструментов владельца. Фронт их зовёт, и это нормально: панель
#: парка, обозреватель базы, студия промтов и прямые вызовы провайдеров —
#: рабочие инструменты хозяина. В SaaS они закрыты, а панели, которые их
#: зовут, обязаны быть спрятаны (`useOwnerMode`).
OWNER_UI_PREFIXES: tuple[str, ...] = (
    "/api/create",  # очередь массовой генерации
    "/api/db",  # прямой обозреватель базы
    "/api/fleet",  # парк машин, запуск команд
    "/api/generation-config-presets",  # пресеты генераторов платформы
    "/api/generation-options",  # выбор генератора: решение платформы, оно же цена
    "/api/gpt-workspace",  # браузерный GPT владельца
    # kie.ai в «Генерации» студии заказчика: бэкенда у нас нет (решение
    # владельца 2026-09-03), вкладка за флагом NEXT_PUBLIC_KIE_CREATE — клиент
    # адресов не увидит, но фронт их знает, чтобы следующий перенос не конфликтовал.
    "/api/kie-create",
    "/api/library",  # библиотека платформы
    "/api/llm-costs",  # СЕБЕСТОИМОСТЬ: отсюда видно маржу
    "/api/outsee",  # прямой вызов провайдера мимо кассы
    "/api/outsee-create",
    "/api/prompt-files",  # промты платформы, одни на всех
    "/api/prompt-studio",
    "/api/prompts",
    "/api/studio-version",  # версия сборки: сведения об установке
    "/api/text-llm",  # выбор текстовой модели платформы
)

#: Не адреса, а куски документации, префиксы и шаблоны в строках.
_NOT_A_PATH = {"/api/*", "/api/...", "/api/", "/api"}

_LITERAL = re.compile(r'["`](/api/[A-Za-z0-9/_.-]*)')


def _front_paths() -> set[str]:
    found: set[str] = set()
    for path in list(WEB_SRC.rglob("*.ts")) + list(WEB_SRC.rglob("*.tsx")):
        for match in _LITERAL.findall(path.read_text(encoding="utf-8")):
            clean = match.rstrip("/?")
            if clean and clean not in _NOT_A_PATH:
                found.add(clean)
    return found


def _classified(path: str) -> bool:
    if path in PUBLIC_PATHS or path.startswith(tuple(PUBLIC_PATHS)):
        return True
    # Схема API открыта владельцу и закрыта в SaaS — решение уже принято.
    if path in DOC_PATHS:
        return True
    return path.startswith(TENANT_ALLOWED_PREFIXES) or path.startswith(OWNER_UI_PREFIXES)


def test_front_calls_nothing_unclassified() -> None:
    """Каждый адрес фронта отнесён либо к продукту, либо к инструментам.

    Непонятно куда отнести — значит непонятно, кому этот экран показывать, и
    выяснится это на клиенте, который увидел 404.
    """
    unknown = sorted(p for p in _front_paths() if not _classified(p))
    assert not unknown, (
        "фронт зовёт ручки, про которые не решено, кому они видны: "
        + ", ".join(unknown)
        + ". Добавь в TENANT_ALLOWED_PREFIXES (продукт клиента) или в "
        "OWNER_UI_PREFIXES (инструмент владельца, панель прячется по useOwnerMode)."
    )


def test_the_two_lists_do_not_overlap() -> None:
    """Путь не может быть одновременно продуктом и инструментом.

    Пересечение означало бы, что решение принято дважды и по-разному — а
    работает при этом то, которое проверяется первым.
    """
    overlap = [p for p in OWNER_UI_PREFIXES if p.startswith(TENANT_ALLOWED_PREFIXES)]
    assert not overlap, f"пути в обоих списках сразу: {overlap}"


def test_product_surface_is_not_empty() -> None:
    """Обратная сторона: список разрешённого не ужат до безопасного и пустого.

    Проверка «всё закрыто» проходит идеально на продукте, который ничего не
    умеет, поэтому здесь названы адреса, без которых клиенту нечего делать.
    """
    for needed in ("/api/projects", "/api/artifacts", "/api/hitl", "/api/chat", "/api/me"):
        assert needed.startswith(TENANT_ALLOWED_PREFIXES), f"{needed} закрыт для клиента"
