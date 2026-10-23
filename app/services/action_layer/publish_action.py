from __future__ import annotations

import hashlib

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    ActionRegistry,
    ActionSpec,
    JobState,
    Precondition,
)

PUBLISH_STEP = "publish"
MAX_CAPTION = 2200

PLATFORM_TITLES = {
    "tiktok": "TikTok",
    "yt_shorts": "YouTube Shorts",
    "ig_reels": "Instagram Reels",
    "vk_clips": "VK Клипы",
    "likee": "Likee",
}
_PID = {"type": "integer", "minimum": 1}


def registry_platforms() -> list[str]:
    from app.bots.publishers import ALL_PUBLISHERS

    return [cls.platform for cls in ALL_PUBLISHERS]


def caption_of(project: dict) -> str:
    return str(project.get("topic") or project.get("slug") or "")[:MAX_CAPTION]


_FACTS: dict[int, dict] = {}


def _remember_facts(project_id: int, facts: object) -> None:
    got = {}
    if isinstance(facts, dict):
        got = {
            k: facts.get(k)
            for k in ("sha256", "caption_sha256")
            if isinstance(facts.get(k), str) and facts.get(k)
        }
    if got:
        if len(_FACTS) > 64:
            _FACTS.clear()
        _FACTS[project_id] = got
    else:
        _FACTS.pop(project_id, None)


def account_text(platforms: list[str] | None = None) -> str:
    from app.services import publish_accounts as pa
    from app.settings import settings

    try:
        cfg = pa.load_accounts()
    except pa.AccountsConfigError as exc:
        return f"конфиг аккаунтов публикации неверен ({exc}), публикация не начнётся"
    if cfg.configured:
        names = platforms or registry_platforms()
        parts = []
        for p in names:
            acct = cfg.for_platform(p)
            if not acct.enabled:
                parts.append(f"{PLATFORM_TITLES.get(p, p)}: площадка выключена в конфиге")
            elif not acct.profile_id:
                parts.append(f"{PLATFORM_TITLES.get(p, p)}: профиль не задан, площадка не опубликуется")
            else:
                parts.append(f"{PLATFORM_TITLES.get(p, p)}: {acct.label()}")
        return "; ".join(parts)
    pid = str(settings.morelogin_profile_id or "").strip()
    if not pid:
        return "профиль MoreLogin не задан (MORELOGIN_PROFILE_ID), публикация не начнётся"
    return f"профиль MoreLogin …{pid[-4:]} (под ним выполнен вход в аккаунты площадок)"


def _titles(platforms) -> str:
    return ", ".join(PLATFORM_TITLES.get(p, p) for p in platforms)


def effects_text(params: dict) -> str:
    names = _titles(params.get("platforms") or [])
    facts = _FACTS.get(params.get("project_id")) or {}
    caption = params.get("caption", "")
    csha = facts.get("caption_sha256") or hashlib.sha256(str(caption).encode("utf-8")).hexdigest()
    sha = facts.get("sha256") or "не определён (финальный файл не найден)"
    return (
        f"Готовый ролик проекта {params.get('project_id')} будет ОПУБЛИКОВАН на площадки: {names}. "
        f"Аккаунт: {account_text(list(params.get('platforms') or []))}. Подпись дословно: «{caption}». "
        f"Файл (sha256): {sha}. Подпись (sha256): {csha}. "
        "Опубликованное нельзя отозвать из студии; если сбой случится после нажатия «Опубликовать», "
        "автоповтора не будет и итог нужно проверить на площадке вручную. "
        "Клик возможен только после отдельного подтверждения человека на каждую площадку в панели "
        "«Публикации» для этого файла и этой подписи: шаг попросит подтверждение для всех площадок "
        f"без публикации, а опубликует только там, где его дадут - подтвердите только {names}."
    )


async def _project(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("GET", f"/projects/{params['project_id']}")


async def _pre_project(ctx: ActionContext, params: dict) -> str | None:
    await _project(ctx, params)
    try:
        _remember_facts(
            params["project_id"], await ctx.call("GET", f"/projects/{params['project_id']}/publications")
        )
    except Exception:
        _remember_facts(params["project_id"], None)
    return None


async def _not_managed(ctx: ActionContext, params: dict) -> str | None:
    try:
        from app.services.actions_v1 import managed_project_enabled

        managed = await managed_project_enabled(params["project_id"])
    except Exception as exc:
        return f"не удалось проверить управляемый режим ({type(exc).__name__})"
    if managed:
        return (
            "проект под управляемым (агентским) режимом Actions-v1: публикацию запускает только "
            "человек, управляемый запуск публикации отключён"
        )
    return None


async def _publish_enabled(ctx: ActionContext, params: dict) -> str | None:
    from app.settings import settings

    if not settings.social_publish_enabled:
        return (
            "автопубликация выключена (SOCIAL_PUBLISH_ENABLED=false): шаг ничего не отправит "
            "и проект не станет опубликованным"
        )
    return None


async def _assembled(ctx: ActionContext, params: dict) -> str | None:
    status = (await _project(ctx, params)).get("status")
    if status != "assembled":
        return f"публикация возможна для собранного ролика (статус assembled), сейчас статус {status}"
    return None


async def _caption_matches(ctx: ActionContext, params: dict) -> str | None:
    actual = caption_of(await _project(ctx, params))
    if not actual.strip():
        return "подпись проекта пуста: публикация не начнётся"
    if params.get("caption") != actual:
        return (
            "подпись в плане не совпадает с темой проекта (шаг публикует тему проекта); "
            "составьте план заново, чтобы увидеть актуальную подпись"
        )
    return None


def _pending_platforms(project: dict) -> list[str]:
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    done = meta.get("published_platforms") if isinstance(meta.get("published_platforms"), dict) else {}
    return [p for p in registry_platforms() if p not in done]


async def _approved_platforms(ctx: ActionContext, params: dict) -> set[str]:
    facts = await ctx.call("GET", f"/projects/{params['project_id']}/publications")
    items = (facts or {}).get("platforms") if isinstance(facts, dict) else None
    return {str(i.get("platform")) for i in items or [] if isinstance(i, dict) and i.get("approved")}


async def _platforms_expected(ctx: ActionContext, params: dict) -> str | None:
    """Шаг сам площадки не выбирает: жмёт там, где нет публикации и есть подтверждение человека."""
    pending = _pending_platforms(await _project(ctx, params))
    got = list(params.get("platforms") or [])
    done = [p for p in got if p not in pending]
    if done:
        return (
            f"на площадках {_titles(done)} ролик уже опубликован; публикации ещё нет на: "
            f"{_titles(pending) or 'таких нет'}"
        )
    try:
        approved = await _approved_platforms(ctx, params)
    except ActionCallError as exc:
        return f"не удалось прочитать подтверждения публикаций ({exc.detail})"
    extra = [p for p in pending if p in approved and p not in got]
    if extra:
        return (
            f"в панели «Публикации» уже подтверждена площадка {_titles(extra)}: шаг опубликует и туда. "
            "Назовите её в просьбе тоже или снимите там подтверждение"
        )
    return None


PRECONDITIONS = (
    Precondition("project_exists", "Проект существует", _pre_project),
    Precondition("not_managed", "Управляемый (агентский) режим не запускает публикацию", _not_managed),
    Precondition("publish_enabled", "Автопубликация включена в настройках", _publish_enabled),
    Precondition("project_assembled", "Ролик собран и ждёт публикации", _assembled),
    Precondition("caption_matches", "Подпись совпадает с темой проекта", _caption_matches),
    Precondition(
        "platforms_expected",
        "Площадки ещё без публикации, других подтверждённых площадок нет",
        _platforms_expected,
    ),
)


async def _publish_send(ctx: ActionContext, params: dict) -> dict:
    for pre in PRECONDITIONS[1:]:
        reason = await pre.check(ctx, params)
        if reason:
            raise ActionCallError(409, f"{pre.id}: {reason}")
    await ctx.call(
        "POST", f"/projects/{params['project_id']}/steps/{PUBLISH_STEP}/run", params={"mode": "resume"}
    )
    return {"started": True, "platforms": list(params["platforms"])}


async def _publish_poll(ctx: ActionContext, params: dict, started: dict) -> JobState:
    proj = await _project(ctx, params)
    status = proj.get("status")
    if status == "publishing":
        return JobState(done=False, note="публикация идёт")
    meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
    published = meta.get("published_platforms") if isinstance(meta.get("published_platforms"), dict) else {}
    unconfirmed = meta.get("publish_unconfirmed") if isinstance(meta.get("publish_unconfirmed"), dict) else {}
    awaiting = (
        meta.get("publish_awaiting_approval")
        if isinstance(meta.get("publish_awaiting_approval"), dict)
        else {}
    )
    waiting = [p for p in awaiting.get("platforms") or [] if p not in published]
    result = {
        "final_status": status,
        "published": sorted(published),
        "unconfirmed": sorted(unconfirmed),
        "awaiting_approval": waiting,
    }
    asked = list(params.get("platforms") or [])
    if status == "published" or (asked and all(p in published for p in asked)):
        result["text_ru"] = (
            f"Ролик опубликован: {_titles(p for p in asked if p in published) or _titles(published)}."
        )
        return JobState(done=True, ok=True, note="площадки подтвердили публикацию", result=result)
    if waiting and not unconfirmed and not meta.get("publish_needs_human"):
        mine = [p for p in asked if p in waiting] or waiting
        result["text_ru"] = (
            f"Публикация ждёт вашего подтверждения в панели «Публикации»: {_titles(mine)}. "
            "После подтверждения шаг опубликует ролик сам."
        )
        return JobState(done=True, ok=True, note="ждёт подтверждения человека", result=result)
    return JobState(
        done=True,
        ok=False,
        result=result,
        error="публикация подтверждена не везде: проверьте журнал публикаций и площадки вручную",
    )


def _specs() -> list[ActionSpec]:
    platforms = registry_platforms()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["project_id", "platforms", "caption"],
        "properties": {
            "project_id": _PID,
            "platforms": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(platforms),
                "items": {"type": "string", "enum": platforms},
                "description": "площадки публикации",
            },
            "caption": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_CAPTION,
                "description": "подпись: тема проекта",
            },
        },
    }
    return [
        ActionSpec(
            id="publish.send",
            title_ru="Опубликовать ролик на площадки",
            description_ru="Публикует готовый ролик проекта на площадки через профиль MoreLogin с подписью "
            "из темы проекта. Нельзя отозвать из студии; после сбоя автоповтора нет. "
            "Только по подтверждению человека.",
            params_schema=schema,
            executor=_publish_send,
            poll=_publish_poll,
            poll_interval_s=5.0,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            destructive_effects=effects_text,
            preconditions=PRECONDITIONS,
            tags=("publish", "social"),
        )
    ]


def register_publish_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
