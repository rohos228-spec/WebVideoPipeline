"""Звуковая часть конвейера в чате: озвучка, музыка, звуки сопровождения.

Цена и готовность шага считаются по тем же правилам, что у самих шагов
(app/orchestrator/steps/generate_audio, generate_music, generate_sfx): что уже лежит на диске,
какой провайдер сработает и сколько он берёт по константам из кода. ElevenLabs - по символам
(media_spend.ELEVENLABS_USD_PER_CHAR) и секундам звука (ELEVENLABS_SFX_USD_PER_SEC), трек Suno -
по каталогу Kie. Оценка верхняя и по балансу провайдеров не сверена.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.action_layer.node_run_cost import MEDIA_UNCALIBRATED
from app.services.action_layer.spec import CostEstimate

SOUND_STEPS: tuple[str, ...] = ("audio", "music", "sfx_gen")
MIN_TTS_CHARS = 50
SUNO_MODEL_ID = "suno-music"

SOUND_CREDITS = {
    "type": "number",
    "minimum": 0,
    "maximum": 100000,
    "description": "Верхняя оценка звукового шага в кредитах из step.get_run_cost_basis "
    "(озвучка, музыка, звуки)",
}

_UNKNOWN = {
    "audio": "озвучка: цена считается по числу символов текста, возьмите её у step.get_run_cost_basis",
    "music": "музыка: цена трека зависит от провайдера и того, что уже лежит на диске, возьмите её у "
    "step.get_run_cost_basis",
    "sfx_gen": "звуки: цена считается по секундам из плана звуков, возьмите её у step.get_run_cost_basis",
}


@dataclass(frozen=True)
class SoundBasis:
    step_code: str
    provider: str
    credits: float | None
    floor: float
    basis: str
    missing: str | None = None
    notice: str | None = None

    @property
    def priced(self) -> bool:
        return self.credits is not None


def _credits(usd: float) -> float:
    from app.services.kie_catalog import CREDIT_USD

    return round(usd / CREDIT_USD, 2)


def _meta(project: Any) -> dict:
    meta = getattr(project, "meta", None)
    return meta if isinstance(meta, dict) else {}


def audio_basis(project: Any) -> SoundBasis:
    from app.services import media_spend
    from app.services.elevenlabs_api import (
        MAX_POSTS_PER_SEGMENT,
        elevenlabs_api_configured,
        normalize_text_for_tts,
    )
    from app.services.frame_audio import find_voice_full_on_disk, resolve_full_voiceover_text

    voice = find_voice_full_on_disk(Path(project.data_dir), meta=_meta(project))
    if voice is not None:
        return SoundBasis(
            "audio",
            "disk",
            0.0,
            0.0,
            f"озвучка уже лежит в проекте ({voice.name}): ElevenLabs не вызывается, "
            "по ней только расставляются таймкоды (локальное распознавание)",
        )
    text = resolve_full_voiceover_text(project)
    if len(text) < MIN_TTS_CHARS:
        return SoundBasis(
            "audio",
            "elevenlabs",
            None,
            0.0,
            "озвучивать нечего",
            missing="нет закадрового текста (voiceover.txt или текст сценария): сначала "
            "шаг «Закадровый текст» или загрузите готовую озвучку",
        )
    chars = len(normalize_text_for_tts(text))
    usd = chars * media_spend.ELEVENLABS_USD_PER_CHAR * MAX_POSTS_PER_SEGMENT
    basis = (
        f"{MEDIA_UNCALIBRATED}: ElevenLabs, {chars} символов x "
        f"{media_spend.ELEVENLABS_USD_PER_CHAR:g} $ за символ, до {MAX_POSTS_PER_SEGMENT} "
        "отправок (повтор при перегрузке сервиса); таймкоды распознаются локально"
    )
    if not elevenlabs_api_configured():
        return SoundBasis(
            "audio",
            "elevenlabs",
            _credits(usd),
            0.0,
            basis,
            missing="готовой озвучки в проекте нет, а ключ ElevenLabs не задан: загрузите "
            "озвучку (montage.upload_voice) или попросите добавить ключ",
        )
    return SoundBasis("audio", "elevenlabs", _credits(usd), 0.0, basis)


def music_basis(project: Any) -> SoundBasis:
    from app.bots import kie_http
    from app.orchestrator.steps.generate_music import _find_music_on_disk
    from app.services import kie_catalog

    data_dir = Path(project.data_dir)
    disk = _find_music_on_disk(data_dir / "music")
    if disk is not None:
        return SoundBasis(
            "music", "disk", 0.0, 0.0, f"музыка уже лежит в проекте ({disk.name}): Suno не вызывается"
        )
    vo = data_dir / "voiceover.txt"
    try:
        has_vo = vo.is_file() and bool(vo.read_text(encoding="utf-8").strip())
    except OSError:
        has_vo = False
    missing = None
    if not has_vo:
        missing = (
            "нет voiceover.txt, по нему пишется заказ музыки: сначала шаг «Закадровый текст» "
            "или загрузите свою музыку (montage.upload_music)"
        )
    elif not str(getattr(project, "topic", "") or "").strip():
        missing = "у проекта не задана тема, она нужна как название трека"
    llm = "плюс один запрос к текстовой модели за описанием трека, его цену код не определяет"
    if kie_http.kie_configured():
        spec = kie_catalog.get_model(SUNO_MODEL_ID) or {}
        pricing = spec.get("pricing") or {}
        track = float(pricing.get("default") or 0.0)
        if track <= 0:
            return SoundBasis(
                "music",
                "kie",
                None,
                0.0,
                f"трек Suno через Kie: тарифа {SUNO_MODEL_ID} в каталоге нет; {llm}",
                missing=missing,
            )
        note = str(pricing.get("note") or "").strip()
        return SoundBasis(
            "music",
            "kie",
            None,
            track,
            f"{MEDIA_UNCALIBRATED}: трек Suno через Kie - {track:g} кр по каталогу"
            + (f" ({note})" if note else "")
            + f"; {llm}",
            missing=missing,
        )
    return SoundBasis(
        "music",
        "outsee",
        None,
        0.0,
        f"трек Suno через браузерный Outsee (ключа Kie нет): тарифа в коде нет; {llm}",
        missing=missing,
    )


def _sfx_local_reason(project: Any) -> str | None:
    from app.settings import settings

    if not settings.elevenlabs_api_key:
        return "ключ ElevenLabs не задан"
    if _meta(project).get("sfx_provider") == "local_synth":
        return "в проекте закреплён локальный синтез (после прошлого прогона звуков)"
    return None


def sfx_basis(project: Any) -> SoundBasis:
    from app.services import media_spend
    from app.services.sfx_gen import SFX_MAX_POSTS, sfx_billed_seconds
    from app.services.sfx_plan import load_sfx_plan
    from app.settings import settings

    if not settings.sfx_enabled:
        return SoundBasis(
            "sfx_gen",
            "off",
            0.0,
            0.0,
            "звуки сопровождения выключены (SFX_ENABLED=false): шаг пропускается без генерации",
            notice="Звуки сопровождения выключены в настройках: шаг их пропустит, в ролике их не будет.",
        )
    plan = load_sfx_plan(project)
    if not plan:
        return SoundBasis(
            "sfx_gen",
            "elevenlabs",
            None,
            0.0,
            "плана звуков нет",
            missing="нет плана звуков: сначала шаг «План звуков» (step_code=sfx_plan)",
        )
    local = _sfx_local_reason(project)
    if local:
        return SoundBasis(
            "sfx_gen",
            "local_synth",
            0.0,
            0.0,
            f"{len(plan)} звуков локальным синтезом, платных вызовов нет",
            notice=f"Звуки будут сделаны локальным синтезом (простые шумы, свисты, удары), "
            f"а не ElevenLabs: {local}.",
        )
    seconds = round(sum(sfx_billed_seconds(ev.duration) for ev in plan), 1)
    usd = seconds * media_spend.ELEVENLABS_SFX_USD_PER_SEC * SFX_MAX_POSTS
    return SoundBasis(
        "sfx_gen",
        "elevenlabs",
        _credits(usd),
        0.0,
        f"{MEDIA_UNCALIBRATED}: ElevenLabs, {len(plan)} звуков, {seconds:g} с x "
        f"{media_spend.ELEVENLABS_SFX_USD_PER_SEC:g} $ за секунду, до {SFX_MAX_POSTS} "
        "отправок на звук; если ElevenLabs откажет, звук молча заменится локальным "
        "синтезом - об этом будет сказано в итоге",
    )


_BASIS = {"audio": audio_basis, "music": music_basis, "sfx_gen": sfx_basis}


def basis_for(step_code: str, project: Any) -> SoundBasis:
    return _BASIS[step_code](project)


async def load_basis(project_id: int, step_code: str) -> SoundBasis:
    from app.models import Project
    from app.services.action_layer import store

    async with store.sessions()() as s:
        project = await s.get(Project, int(project_id))
    if project is None:
        return SoundBasis(
            step_code, "unknown", None, 0.0, "проект не найден", missing=f"проект #{project_id} не найден"
        )
    return basis_for(step_code, project)


def estimate(params: dict) -> CostEstimate:
    from app.services.action_layer import node_run_cost as nrc

    code = str(params.get("step_code") or "")
    declared = params.get("sound_credits")
    if declared is None:
        base = CostEstimate.unknown(_UNKNOWN.get(code, "звуковой шаг: цена по коду неизвестна"))
    else:
        base = CostEstimate(
            0.0,
            round(float(declared), 2),
            f"{MEDIA_UNCALIBRATED}: заявлено {float(declared):g} кр по данным проекта",
        )
    return nrc.apply_ceiling(base, params.get("ceiling_credits"))


def cost_reason(basis: SoundBasis, params: dict) -> str | None:
    declared = params.get("sound_credits")
    ceiling = params.get("ceiling_credits")
    if basis.priced:
        need = float(basis.credits or 0.0)
        if declared is None and ceiling is None:
            return f"заявите sound_credits: по данным проекта {need:g} кр ({basis.basis})"
        if declared is not None and float(declared) < need:
            return f"sound_credits: заявлено {float(declared):g}, по данным проекта нужно не меньше {need:g}"
        if ceiling is not None and float(ceiling) < need:
            return f"ceiling_credits {float(ceiling):g} ниже расчёта {need:g} кр по данным проекта"
        return None
    if declared is not None:
        return (
            "цену шага по коду целиком не определить (" + basis.basis + "): вместо sound_credits "
            "укажите ceiling_credits - потолок расхода на этот шаг"
        )
    if ceiling is None:
        floor = f" не меньше {basis.floor:g}" if basis.floor else ""
        return (
            "стоимость шага по коду определяется не целиком: "
            + basis.basis
            + f". Укажите ceiling_credits{floor} - потолок расхода на этот шаг"
        )
    if float(ceiling) < basis.floor:
        return f"потолок {float(ceiling):g} кр ниже известной части цены {basis.floor:g} кр ({basis.basis})"
    return None


def basis_json(basis: SoundBasis) -> dict:
    out: dict[str, Any] = {
        "step_code": basis.step_code,
        "kind": "sound",
        "provider": basis.provider,
        "estimable": basis.priced,
        "uncalibrated": True,
        "basis": basis.basis,
        "needs_ceiling": not basis.priced,
    }
    if basis.priced:
        out["upper_bound_credits"] = basis.credits
        out["declare"] = {"sound_credits": basis.credits}
    else:
        out["hint"] = (
            "цена по коду определяется не целиком: для запуска укажите ceiling_credits - "
            "потолок, который вы готовы потратить на этот шаг"
        )
        if basis.floor:
            out["min_ceiling_credits"] = basis.floor
    if basis.missing:
        out["missing"] = basis.missing
    if basis.notice:
        out["notice"] = basis.notice
    return out


def sfx_outcome_text(meta: dict) -> str:
    """Чем на самом деле сделаны звуки: шаг пишет это в meta (generate_sfx.degradation_summary)."""
    if meta.get("sfx_skipped"):
        return "Звуки сопровождения пропущены (выключены в настройках), в ролике их нет."
    if meta.get("sfx_degraded"):
        reason = str(meta.get("sfx_degraded_reason") or "").strip()
        tail = f": {reason}" if reason else ""
        if meta.get("sfx_provider") == "local_synth":
            return f"Звуки сделаны локальным синтезом (простые шумы), а не ElevenLabs{tail}."
        count = meta.get("sfx_synthetic_count")
        part = f"{count} звуков" if isinstance(count, int) and count > 0 else "часть звуков"
        return f"ElevenLabs сделал не все звуки: {part} заменены локальным синтезом{tail}."
    if meta.get("sfx_provider") == "elevenlabs":
        return "Звуки сделаны через ElevenLabs."
    return ""
