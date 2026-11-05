"""Голос озвучки из чата: «какой голос в озвучке», «поменяй голос на мужской».

Голос хранится в ``meta.node_step_params.audio.elevenlabs_voice_id`` — туда же пишет панель
ноды «Озвучка». Чат отвечал «в фактах не указано» и «смены голоса нет в действиях».
Смена голоса не тратит деньги сама: новая озвучка — отдельным запуском шага.
"""

from __future__ import annotations

from typing import Any

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import RISK_LOCAL_SAFE, ActionSpec, Precondition

# Пол по описанию каталога: «мужской» — первый мужской голос, кроме текущего.
_FEMALE = frozenset(
    {"Xb7hH8MSUJpSbSDYk0k2", "EXAVITQu4vr4xnSDxMaL", "hpp4J3VqNfWAUOO0d1Us", "pFZP5JQG7iQjIQuC4Bku"}
)
_MALE_WORDS = ("мужск", "муж ", "мужчин", "male", "парень", "дядь")
_FEMALE_WORDS = ("женск", "жен ", "женщин", "female", "девуш", "девоч")


def voices() -> list[dict[str, str]]:
    from app.services.elevenlabs_voices import ELEVENLABS_VOICES

    return [{**v, "gender": "женский" if v["id"] in _FEMALE else "мужской"} for v in ELEVENLABS_VOICES]


def voice_name(voice_id: str) -> str:
    for v in voices():
        if v["id"] == voice_id:
            return f"{v['name']} — {v['gender']}, {v['description']}"
    try:
        from app.services.ru_voices import get_ru_voice_by_id

        ru = get_ru_voice_by_id(voice_id)
        if ru:
            return str(ru.get("name") or ru.get("title") or "русский голос из каталога")
    except Exception:  # noqa: BLE001 — каталог русских голосов может быть не настроен
        pass
    return "свой голос (нет в каталоге студии)"


def current_voice_id(meta: Any) -> str:
    from app.services.elevenlabs_voices import DEFAULT_ELEVENLABS_VOICE_ID

    nsp = meta.get("node_step_params") if isinstance(meta, dict) else None
    audio = nsp.get("audio") if isinstance(nsp, dict) else None
    vid = audio.get("elevenlabs_voice_id") if isinstance(audio, dict) else None
    return str(vid).strip() if isinstance(vid, str) and vid.strip() else DEFAULT_ELEVENLABS_VOICE_ID


def voice_text(meta: Any) -> str:
    vid = current_voice_id(meta)
    others = ", ".join(f"{v['name']} ({v['gender']})" for v in voices() if v["id"] != vid)
    return (
        f"Голос озвучки: {voice_name(vid)}. Другие голоса студии: {others}. "
        "Сменить: «поменяй голос на мужской» или «поставь голос Сара»; новая озвучка — после "
        "перезапуска шага озвучки."
    )


def pick_voice(want: str, current: str) -> dict[str, str] | None:
    """«мужской» / «женский» / имя («Сара», «Brian») → голос каталога, отличный от текущего."""
    text = f" {str(want or '').lower().replace('ё', 'е')} "
    pool = voices()
    for v in pool:
        names = v["name"].lower().replace("(", " ").replace(")", " ").split()
        if any(f" {n} " in text or text.strip() == n for n in names if len(n) >= 3):
            return v
    gender = None
    if any(w in text for w in _MALE_WORDS):
        gender = "мужской"
    elif any(w in text for w in _FEMALE_WORDS):
        gender = "женский"
    if gender is None:
        return None
    return next((v for v in pool if v["gender"] == gender and v["id"] != current), None)


async def _project_meta(ctx: ActionContext, pid: int) -> dict:
    data = await ctx.call("GET", f"/projects/{pid}")
    meta = data.get("meta") if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


async def _pre_voice_known(ctx: ActionContext, params: dict) -> str | None:
    meta = await _project_meta(ctx, int(params["project_id"]))
    if pick_voice(str(params.get("voice") or ""), current_voice_id(meta)) is None:
        names = ", ".join(v["name"] for v in voices())
        return f"Не понял, какой голос нужен. Есть: {names}; можно сказать «мужской» или «женский»."
    return None


async def _set_voice(ctx: ActionContext, params: dict) -> dict:
    pid = int(params["project_id"])
    meta = await _project_meta(ctx, pid)
    before = current_voice_id(meta)
    chosen = pick_voice(str(params.get("voice") or ""), before)
    if chosen is None:
        raise ActionCallError(400, {"message": "голос не найден в каталоге"})
    nsp = dict(meta.get("node_step_params") or {}) if isinstance(meta.get("node_step_params"), dict) else {}
    audio = dict(nsp.get("audio") or {}) if isinstance(nsp.get("audio"), dict) else {}
    audio["elevenlabs_voice_id"] = chosen["id"]
    nsp["audio"] = audio
    await ctx.call("PATCH", f"/projects/{pid}", json={"meta": {"node_step_params": nsp}})
    return {
        "ok": True,
        "project_id": pid,
        "before": voice_name(before),
        "after": voice_name(chosen["id"]),
        "text_ru": f"Голос озвучки проекта #{pid}: был {voice_name(before)}, теперь {voice_name(chosen['id'])}. "
        "Новая озвучка будет после перезапуска шага озвучки.",
    }


def voice_specs(pid_schema: dict, pre_project: Precondition, pre_idle: Precondition) -> list[ActionSpec]:
    return [
        ActionSpec(
            id="audio.set_voice",
            title_ru="Сменить голос озвучки",
            description_ru="Голос ноды «Озвучка» для следующей озвучки: имя из каталога (Адам, Сара, "
            "Брайан…) или «мужской» / «женский». Сама озвучка не перезапускается и денег не тратит.",
            params_schema={
                "type": "object",
                "properties": {
                    "project_id": pid_schema,
                    "voice": {"type": "string", "minLength": 2, "maxLength": 60},
                },
                "required": ["project_id", "voice"],
                "additionalProperties": False,
            },
            executor=_set_voice,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            reversible=True,
            preconditions=(
                pre_project,
                pre_idle,
                Precondition("voice_known", "Голос есть в каталоге студии", _pre_voice_known),
            ),
            tags=("audio", "voice", "голос", "озвучка"),
        )
    ]


def voice_keyword(text: str) -> str | None:
    """Что назвал человек: имя голоса («Сара») или «мужской» / «женский» — для карточки и шага."""
    low = f" {str(text or '').lower().replace('ё', 'е')} "
    for v in voices():
        names = v["name"].lower().replace("(", " ").replace(")", " ").split()
        if any(f" {n} " in low for n in names if len(n) >= 3):
            return v["name"].split(" (")[0]
    if any(w in low for w in _MALE_WORDS):
        return "мужской"
    if any(w in low for w in _FEMALE_WORDS):
        return "женский"
    return None
