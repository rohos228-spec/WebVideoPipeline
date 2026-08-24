"""Промты клиента: посмотреть, поправить, вернуть как было.

То самое место, куда человек идёт «менять промпт на более лучший»
(`docs/SAAS-PIVOT.md` §9.4). Существующие ручки `/api/prompts` и
`/api/prompt-files` для этого не годятся: они правят промты ПЛАТФОРМЫ, одни
на всех, и потому в SaaS закрыты списком разрешённого. Клиенту нужен свой
вход, пишущий в его собственную область.

**Правка всегда идёт на свой уровень.** Клиент не выбирает, «сохранить как
системный» — область определяется тем, кто он, а не тем, что он попросил.
Иначе один клиент поменял бы промт всем остальным, и заметили бы это по
чужим роликам.

**Уровень видно в ответе.** «Правлю, а не меняется» — первый вопрос
человека, у которого есть проектное переопределение поверх личного. Отдать
текст, не сказав, чей он, значит гарантировать этот вопрос.

**Сброс убирает СВОЮ строку, а не системную.** «Вернуть как было» для
арендатора означает «перестать переопределять», и удалить он может только то,
что сам и записал.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.deps import get_session

router = APIRouter(prefix="/my-prompts", tags=["prompts"])

#: Имя варианта. Клиент правит основной; варианты — операторская механика,
#: и тащить её в продукт до того, как она понадобится, незачем.
DEFAULT_NAME = "default"


class MyPrompt(BaseModel):
    step_code: str
    title: str = ""
    text: str = ""
    #: project | tenant | brand | system | disk — чей текст сейчас действует.
    source: str = "disk"
    source_title: str = ""
    #: true — у клиента есть своя правка этого шага.
    overridden: bool = False


class SavePrompt(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    #: Правка только для одного проекта, а не для всех. Пусто — для всех моих.
    project_id: int | None = None


def _scope(project_id: int | None):
    from app.services.prompt_store import PromptScope
    from app.services.tenant import current_tenant
    from app.settings import settings

    return PromptScope(
        tenant_id=current_tenant(),
        brand=settings.studio_brand.strip(),
        project_id=project_id,
    )


def _title(step_code: str) -> str:
    from app.telegram.menu import step_by_code

    step = step_by_code(step_code)
    return step.title if step is not None else step_code


@router.get("", response_model=list[MyPrompt])
async def list_my_prompts(project_id: int | None = None) -> list[MyPrompt]:
    """Промты всех шагов: что действует и откуда взято."""
    from app.services.prompt_library import STEP_FOLDERS
    from app.services.prompt_store import LEVEL_NAMES, resolve_with_source

    scope = _scope(project_id)
    out: list[MyPrompt] = []
    for step_code in STEP_FOLDERS:
        text, source = resolve_with_source(step_code, DEFAULT_NAME, scope)
        out.append(
            MyPrompt(
                step_code=step_code,
                title=_title(step_code),
                text=text or "",
                source=source,
                source_title=LEVEL_NAMES.get(source, source),
                overridden=source in ("tenant", "project"),
            )
        )
    return out


@router.get("/{step_code}", response_model=MyPrompt)
async def get_my_prompt(step_code: str, project_id: int | None = None) -> MyPrompt:
    from app.services.prompt_library import STEP_FOLDERS, read_prompt
    from app.services.prompt_store import LEVEL_NAMES, resolve_with_source

    if step_code not in STEP_FOLDERS:
        raise HTTPException(status_code=404, detail=f"у шага {step_code!r} нет мастер-промта")

    scope = _scope(project_id)
    text, source = resolve_with_source(step_code, DEFAULT_NAME, scope)
    if text is None:
        # База пуста — показываем то, что реально применится: файл с диска.
        # Пустая форма редактирования означала бы «промта нет», а он есть.
        try:
            text = read_prompt(step_code, DEFAULT_NAME)
        except (FileNotFoundError, ValueError):
            text = ""
    return MyPrompt(
        step_code=step_code,
        title=_title(step_code),
        text=text,
        source=source,
        source_title=LEVEL_NAMES.get(source, source),
        overridden=source in ("tenant", "project"),
    )


@router.put("/{step_code}", response_model=MyPrompt)
async def save_my_prompt(
    step_code: str, body: SavePrompt, session: AsyncSession = Depends(get_session)
) -> MyPrompt:
    """Сохранить свою правку. Уровень определяется тем, кто правит."""
    from app.services.prompt_library import STEP_FOLDERS
    from app.services.prompt_store import save
    from app.services.tenant import current_tenant

    if step_code not in STEP_FOLDERS:
        raise HTTPException(status_code=404, detail=f"у шага {step_code!r} нет мастер-промта")
    if current_tenant() is None:
        # В режиме владельца правка ушла бы в системный уровень, то есть всем.
        # У владельца для этого есть свой редактор — файловый.
        raise HTTPException(
            status_code=409,
            detail="в режиме владельца промты правятся файлами в prompts/",
        )

    await save(session, step_code, DEFAULT_NAME, body.text, scope=_scope(body.project_id))
    await session.commit()
    return await get_my_prompt(step_code, body.project_id)


@router.delete("/{step_code}", response_model=MyPrompt)
async def reset_my_prompt(
    step_code: str, project_id: int | None = None, session: AsyncSession = Depends(get_session)
) -> MyPrompt:
    """Убрать свою правку и вернуться к тому, что было до неё."""
    from app.services.prompt_store import drop
    from app.services.tenant import current_tenant

    if current_tenant() is None:
        raise HTTPException(
            status_code=409,
            detail="в режиме владельца промты правятся файлами в prompts/",
        )
    await drop(session, step_code, DEFAULT_NAME, scope=_scope(project_id))
    await session.commit()
    return await get_my_prompt(step_code, project_id)
