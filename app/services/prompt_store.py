"""Промт из базы, а не с диска. С переопределением на арендатора.

Библиотека мастер-промтов лежит в `prompts/` — каталоге, намеренно вне git.
Для одной машины это удобно: правишь файл, перезапускаешь, смотришь. В SaaS
не работает вовсе: диска узла у клиента нет, а «поменять промпт на более
лучший» — это ровно то, зачем он пришёл (`docs/SAAS-PIVOT.md` §9.4).

**Разрешение от частного к общему**: проект → арендатор → бренд → системный →
файл на диске. Каждый уровень отвечает на свой вопрос — системный «как
правильно», бренд «как принято у нас», арендатор «как хочу я», проект «как в
этом ролике». Файл остаётся последним звеном намеренно: он источник правды
режима владельца и он же наполняет системный уровень загрузчиком.

**Почему кэш, а не запрос на каждое чтение.** `prompt_library.read_prompt`
синхронна и зовётся из глубины шагов — переписать её на `async` значит
тронуть половину конвейера ради того, чтобы прочитать десяток килобайт,
которые между шагами не меняются. Поэтому библиотека целиком поднимается в
память при старте и обновляется при записи; чтение остаётся синхронным и
бесплатным.

Кэш живёт в процессе и ничего не знает про соседние узлы: правка промта
доедет до них при следующем старте или при их собственной записи. Для
библиотеки, которую правят руками и редко, это допустимо; когда правки
станут частыми, здесь понадобится инвалидация через шину — и это будет видно
по жалобе «поправил, а не применилось», а не молча.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

#: Кэш процесса: (арендатор, бренд, проект, шаг, имя) → текст.
_CACHE: dict[tuple[str | None, str, int | None, str, str], str] = {}
_LOADED = False


@dataclass(frozen=True)
class PromptScope:
    """Чей промт ищем. Пустые поля — уровни, которые не задействованы."""

    tenant_id: str | None = None
    brand: str = ""
    project_id: int | None = None


def resolve(step_code: str, name: str, scope: PromptScope | None = None) -> str | None:
    """Текст промта по цепочке переопределений. `None` — в базе ничего нет.

    Порядок фиксирован и идёт от частного к общему. Первый найденный
    побеждает: иначе «переопределение» означало бы «иногда переопределение»,
    а это худший вид настройки — тот, который работает через раз.
    """
    sc = scope or _scope_from_context()
    for key in _lookup_order(step_code, name, sc):
        text = _CACHE.get(key)
        if text is not None:
            return text
    return None


#: Как называется уровень человеку. Внутреннее «tenant_id is None» ему ничего
#: не говорит, а вопрос «почему у меня не мой промт» задаётся первым.
LEVEL_NAMES = {
    "project": "этот проект",
    "tenant": "мои",
    "brand": "бренд",
    "system": "системный",
    "disk": "файл на диске",
}


def list_names(step_code: str, scope: PromptScope | None = None) -> list[str]:
    """Имена промтов шага, видимые из области: все уровни цепочки разом.

    Нужно редактору: он показывает файлы, а на сервере файлов нет — диск
    только для чтения, и промт нового шага живёт лишь в базе. Без этого
    списка редактор отвечал бы «не заведено» про промт, который прямо сейчас
    работает.
    """
    sc = scope or _scope_from_context()
    wanted = {key[:3] for key in _lookup_order(step_code, "", sc)}
    names = {
        name
        for (tenant, brand, project_id, step, name) in _CACHE
        if step == step_code and (tenant, brand, project_id) in wanted
    }
    out = sorted(names)
    if "default" in out:
        out.remove("default")
        out.insert(0, "default")
    return out


def resolve_with_source(
    step_code: str, name: str, scope: PromptScope | None = None
) -> tuple[str | None, str]:
    """Текст и УРОВЕНЬ, с которого он взят.

    Уровень нужен интерфейсу: «правлю, а не меняется» — первый вопрос
    человека, у которого есть проектное переопределение поверх личного.
    Показать текст, не сказав чей он, значит гарантировать этот вопрос.
    """
    sc = scope or _scope_from_context()
    for level, key in zip(_level_names(sc), _lookup_order(step_code, name, sc), strict=True):
        text = _CACHE.get(key)
        if text is not None:
            return text, level
    return None, "disk"


def _level_names(sc: PromptScope) -> list[str]:
    out = []
    if sc.tenant_id and sc.project_id:
        out.append("project")
    if sc.tenant_id:
        out.append("tenant")
    if sc.brand:
        out.append("brand")
    out.append("system")
    return out


def _lookup_order(
    step_code: str, name: str, sc: PromptScope
) -> list[tuple[str | None, str, int | None, str, str]]:
    out: list[tuple[str | None, str, int | None, str, str]] = []
    if sc.tenant_id and sc.project_id:
        out.append((sc.tenant_id, "", sc.project_id, step_code, name))
    if sc.tenant_id:
        out.append((sc.tenant_id, "", None, step_code, name))
    if sc.brand:
        out.append((None, sc.brand, None, step_code, name))
    out.append((None, "", None, step_code, name))
    return out


def _scope_from_context() -> PromptScope:
    from app.services.tenant import current_tenant
    from app.settings import settings

    return PromptScope(tenant_id=current_tenant(), brand=settings.studio_brand.strip())


async def refresh(session: Any) -> int:
    """Поднять библиотеку в память. Возвращает число строк.

    Зовётся на старте и после записи. Читает ВСЁ: библиотека это десятки
    килобайт, а выборочная подгрузка означала бы запрос к базе из синхронной
    функции в глубине шага — то самое, чего кэш и избегает.
    """
    global _LOADED
    from sqlalchemy import select

    from app.models import PromptLibraryEntry

    rows = (await session.execute(select(PromptLibraryEntry))).scalars().all()
    _CACHE.clear()
    for row in rows:
        _CACHE[(row.tenant_id, row.brand or "", row.project_id, row.step_code, row.name)] = row.text
    _LOADED = True
    return len(rows)


def loaded() -> bool:
    """Поднимали ли библиотеку. `False` — читатели идут на диск, и это норма."""
    return _LOADED


async def save(
    session: Any,
    step_code: str,
    name: str,
    text: str,
    *,
    scope: PromptScope | None = None,
) -> None:
    """Записать промт на нужный уровень и обновить кэш.

    Уровень определяется областью: есть проект — проектный, есть арендатор —
    арендаторский, есть бренд — брендовый, иначе системный. Явного выбора
    уровня у вызывающего нет намеренно: «сохранить как системный, будучи
    арендатором» — это не настройка, а ошибка.
    """
    from sqlalchemy import select

    from app.models import PromptLibraryEntry

    sc = scope or _scope_from_context()
    tenant = sc.tenant_id
    brand = "" if tenant else sc.brand
    project_id = sc.project_id if tenant else None

    row = (
        await session.execute(
            select(PromptLibraryEntry).where(
                PromptLibraryEntry.tenant_id.is_(None)
                if tenant is None
                else PromptLibraryEntry.tenant_id == tenant,
                PromptLibraryEntry.brand == brand,
                PromptLibraryEntry.project_id.is_(None)
                if project_id is None
                else PromptLibraryEntry.project_id == project_id,
                PromptLibraryEntry.step_code == step_code,
                PromptLibraryEntry.name == name,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = PromptLibraryEntry(
            tenant_id=tenant,
            brand=brand,
            project_id=project_id,
            step_code=step_code,
            name=name,
            text=text,
        )
        session.add(row)
    else:
        row.text = text
    await session.flush()
    _CACHE[(tenant, brand, project_id, step_code, name)] = text


async def import_from_disk(session: Any, *, overwrite: bool = False) -> dict[str, int]:
    """Наполнить СИСТЕМНЫЙ уровень из каталога `prompts/`.

    ``overwrite=False`` (по умолчанию) не трогает уже загруженное: файл на
    диске мог остаться от прошлой версии, а в базе промт уже правили — и
    затереть правку файлом значит потерять работу человека без вопроса.
    """
    from app.services.prompt_library import STEP_FOLDERS, list_prompts, read_prompt

    stats = {"seen": 0, "written": 0, "skipped": 0}
    for step_code in STEP_FOLDERS:
        try:
            names = list(list_prompts(step_code))
        except Exception:  # noqa: BLE001 — нет каталога шага, это не сбой
            continue
        for name in names:
            stats["seen"] += 1
            if not overwrite and _CACHE.get((None, "", None, step_code, name)) is not None:
                stats["skipped"] += 1
                continue
            try:
                text = read_prompt(step_code, name)
            except (FileNotFoundError, ValueError):
                continue
            await save(session, step_code, name, text, scope=PromptScope())
            stats["written"] += 1
    if stats["written"]:
        logger.info(
            "промт-библиотека: с диска загружено {} промтов, пропущено {}",
            stats["written"],
            stats["skipped"],
        )
    return stats


async def drop(session: Any, step_code: str, name: str, *, scope: PromptScope | None = None) -> bool:
    """Убрать переопределение своего уровня. Возвращает True, если было что убирать.

    Снимается ровно свой уровень, и это важно: «сбросить к системному» для
    арендатора означает удалить СВОЮ строку, а не системную. Удалить чужую он
    не может по построению — область та же, что и при записи.
    """
    from sqlalchemy import select

    from app.models import PromptLibraryEntry

    sc = scope or _scope_from_context()
    tenant = sc.tenant_id
    brand = "" if tenant else sc.brand
    project_id = sc.project_id if tenant else None

    row = (
        await session.execute(
            select(PromptLibraryEntry).where(
                PromptLibraryEntry.tenant_id.is_(None)
                if tenant is None
                else PromptLibraryEntry.tenant_id == tenant,
                PromptLibraryEntry.brand == brand,
                PromptLibraryEntry.project_id.is_(None)
                if project_id is None
                else PromptLibraryEntry.project_id == project_id,
                PromptLibraryEntry.step_code == step_code,
                PromptLibraryEntry.name == name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    _CACHE.pop((tenant, brand, project_id, step_code, name), None)
    return True


def reset_cache() -> None:
    """Забыть кэш. Нужно тестам и перезагрузке библиотеки."""
    global _LOADED
    _CACHE.clear()
    _LOADED = False
