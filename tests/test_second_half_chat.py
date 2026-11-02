from __future__ import annotations

import json
import re

import pytest

from app.models import Project, ProjectStatus
from app.services import media_spend
from app.services.action_layer import builtin_launch as bl
from app.services.action_layer import publish_action as pa
from app.services.action_layer import sound_cost as sc
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ensure_builtin_actions
from app.services.elevenlabs_api import MAX_POSTS_PER_SEGMENT
from app.services.kie_catalog import CREDIT_USD, get_model
from app.services.sfx_gen import SFX_MAX_POSTS
from app.settings import settings
from tests.action_layer_support import env

BASE = "/api/action-layer"
VO = "Это закадровый текст ролика про кофе, его хватит, чтобы озвучка имела смысл и цену."


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


@pytest.fixture
def steps_open(monkeypatch):
    monkeypatch.setattr("app.orchestrator.pipeline_steps.is_step_runnable", lambda step, status: True)


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "sfx_enabled", True)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)


@pytest.fixture
def no_eleven(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", None)
    monkeypatch.setattr(settings, "sfx_enabled", True)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)


async def _project(env, pid) -> Project:
    async with env.factory() as s:
        return await s.get(Project, pid)


async def _files(env, pid, files: dict[str, str | bytes], **fields) -> Project:
    async with env.factory() as s:
        p = await s.get(Project, pid)
        for k, v in fields.items():
            setattr(p, k, ProjectStatus(v) if k == "status" else v)
        await s.commit()
    project = await _project(env, pid)
    for rel, body in files.items():
        path = project.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
    return project


def _plan(*durations: float) -> str:
    return json.dumps({"events": [
        {"frame_number": 1, "t_start": float(i), "duration": d, "kind": "whoosh", "prompt": "whoosh"}
        for i, d in enumerate(durations)]})


async def _preview(env, params):
    return await env.client.post(f"{BASE}/plans/preview",
                                 json={"steps": [{"action_id": "step.run_step", "params": params}]})


def test_every_sound_step_is_launchable_and_priced_by_the_layer() -> None:
    codes = bl.launch_step_codes()
    assert {"audio", "music", "sfx_plan", "sfx_gen", "assemble"} <= set(codes)
    assert "publish" not in codes
    assert bl.estimate_any_step({"step_code": "audio"}).known is False
    est = bl.estimate_any_step({"step_code": "audio", "sound_credits": 12.5})
    assert est.known and est.high == 12.5 and "по данным проекта" in est.basis
    assert bl.estimate_any_step({"step_code": "sfx_gen", "sound_credits": 0}).high == 0.0
    # Потолок ниже заявленной цены не подтверждается.
    assert bl.estimate_any_step({"step_code": "audio", "sound_credits": 10,
                                 "ceiling_credits": 5}).known is False
    # Звукам не подставляется дефолтный потолок текстовых шагов.
    assert "ceiling_credits" not in bl.with_default_ceiling({"step_code": "sfx_gen"})
    assert bl.with_default_ceiling({"step_code": "sfx_plan"})["ceiling_credits"] == \
        bl.DEFAULT_TEXT_CEILING_CREDITS


async def test_audio_is_priced_by_characters_of_elevenlabs(env, eleven) -> None:
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {"voiceover.txt": VO})
    basis = sc.basis_for("audio", project)
    usd = len(VO) * media_spend.ELEVENLABS_USD_PER_CHAR * MAX_POSTS_PER_SEGMENT
    assert basis.priced and basis.provider == "elevenlabs" and basis.missing is None
    assert basis.credits == round(usd / CREDIT_USD, 2)
    assert f"{len(VO)} символов" in basis.basis
    assert sc.cost_reason(basis, {}) and "sound_credits" in sc.cost_reason(basis, {})
    assert sc.cost_reason(basis, {"sound_credits": basis.credits}) is None
    assert "не меньше" in sc.cost_reason(basis, {"sound_credits": basis.credits - 1})
    assert "ниже расчёта" in sc.cost_reason(basis, {"ceiling_credits": basis.credits - 1})


async def test_audio_from_disk_is_free_and_without_text_or_key_is_refused(env, no_eleven) -> None:
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {})
    assert "нет закадрового текста" in sc.basis_for("audio", project).missing
    project = await _files(env, pid, {"voiceover.txt": VO})
    no_key = sc.basis_for("audio", project)
    assert "ключ ElevenLabs не задан" in no_key.missing
    project = await _files(env, pid, {"audio/voice.mp3": b"x" * 2000})
    disk = sc.basis_for("audio", project)
    assert (disk.provider, disk.credits, disk.missing) == ("disk", 0.0, None)
    assert "ElevenLabs не вызывается" in disk.basis


async def test_audio_preview_refuses_missing_inputs_and_takes_declared_price(
        env, not_managed, steps_open, no_eleven, monkeypatch) -> None:
    pid = await env.add_project(frames=1)
    await _files(env, pid, {"voiceover.txt": VO})
    refused = await _preview(env, {"project_id": pid, "step_code": "audio", "sound_credits": 50})
    assert refused.status_code == 422 and "sound_inputs" in str(refused.json())
    assert "ElevenLabs" in str(refused.json())

    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    need = sc.basis_for("audio", await _project(env, pid)).credits
    bare = await _preview(env, {"project_id": pid, "step_code": "audio"})
    assert bare.status_code == 422 and "step_cost_declared" in str(bare.json())
    ok = await _preview(env, {"project_id": pid, "step_code": "audio", "sound_credits": need})
    assert ok.status_code == 200, ok.text
    pv = ok.json()["preview"]
    assert pv["needs_approval"] and pv["cost"]["high"] == need and pv["cost"]["unknown_steps"] == 0


async def test_planner_fills_sound_credits_from_the_project(env, eleven, monkeypatch) -> None:
    from app.services.action_layer.builtin_wave5 import declared_media_values

    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {"voiceover.txt": VO})
    need = sc.basis_for("audio", project).credits
    out = await declared_media_values({"project_id": pid, "step_code": "audio", "count": 3})
    assert out["sound_credits"] == need and "count" not in out
    kept = await declared_media_values({"project_id": pid, "step_code": "audio", "sound_credits": 999})
    assert kept["sound_credits"] == 999
    other = await declared_media_values({"project_id": pid, "step_code": "plan", "sound_credits": 5})
    assert "sound_credits" not in other
    image = await declared_media_values({"step_code": "img", "sound_credits": 5})
    assert "sound_credits" not in image
    # Цену музыки код целиком не знает: придуманный sound_credits не должен сорвать план.
    monkeypatch.setattr("app.bots.kie_http.kie_configured", lambda: True)
    music = await declared_media_values({"project_id": pid, "step_code": "music", "sound_credits": 50})
    assert "sound_credits" not in music


async def test_music_through_kie_needs_a_ceiling_not_below_the_track(env, monkeypatch) -> None:
    monkeypatch.setattr("app.bots.kie_http.kie_configured", lambda: True)
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {"voiceover.txt": VO})
    track = float(get_model("suno-music")["pricing"]["default"])
    basis = sc.basis_for("music", project)
    assert (basis.provider, basis.priced, basis.floor) == ("kie", False, track)
    assert "запрос к текстовой модели" in basis.basis
    assert f"не меньше {track:g}" in sc.cost_reason(basis, {})
    assert "ниже известной части" in sc.cost_reason(basis, {"ceiling_credits": track - 1})
    assert sc.cost_reason(basis, {"ceiling_credits": track + 50}) is None
    assert "вместо sound_credits" in sc.cost_reason(basis, {"sound_credits": track})
    as_json = sc.basis_json(basis)
    assert as_json["needs_ceiling"] and as_json["min_ceiling_credits"] == track


async def test_music_on_disk_is_free_and_without_voiceover_is_refused(env, monkeypatch) -> None:
    monkeypatch.setattr("app.bots.kie_http.kie_configured", lambda: False)
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {})
    assert "voiceover.txt" in sc.basis_for("music", project).missing
    outsee = sc.basis_for("music", await _files(env, pid, {"voiceover.txt": VO}))
    assert outsee.provider == "outsee" and outsee.floor == 0.0 and outsee.missing is None
    disk = sc.basis_for("music", await _files(env, pid, {"music/music_1.mp3": b"x" * 2000}))
    assert (disk.provider, disk.credits) == ("disk", 0.0)


async def test_sfx_without_plan_is_refused_and_with_key_is_priced_by_seconds(env, eleven) -> None:
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {})
    assert "План звуков" in sc.basis_for("sfx_gen", project).missing
    project = await _files(env, pid, {"sfx_plan.json": _plan(1.0, 30.0, 0.2)})
    basis = sc.basis_for("sfx_gen", project)
    seconds = 1.0 + 22.0 + 0.5
    usd = seconds * media_spend.ELEVENLABS_SFX_USD_PER_SEC * SFX_MAX_POSTS
    assert basis.provider == "elevenlabs" and basis.credits == round(usd / CREDIT_USD, 2)
    assert basis.notice is None and "3 звуков" in basis.basis


async def test_sfx_without_key_is_local_synth_said_before_the_run(env, not_managed, steps_open,
                                                                   no_eleven) -> None:
    pid = await env.add_project(frames=1)
    await _files(env, pid, {"sfx_plan.json": _plan(1.0)})
    basis = sc.basis_for("sfx_gen", await _project(env, pid))
    assert (basis.provider, basis.credits) == ("local_synth", 0.0)
    assert "локальным синтезом" in basis.notice and "не ElevenLabs" in basis.notice
    r = await _preview(env, {"project_id": pid, "step_code": "sfx_gen", "sound_credits": 0})
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["cost"]["high"] == 0.0
    assert any("локальным синтезом" in w["text"] for w in pv["warnings"])


async def test_sfx_pinned_to_local_synth_is_told_even_with_a_key(env, eleven) -> None:
    pid = await env.add_project(frames=1)
    project = await _files(env, pid, {"sfx_plan.json": _plan(1.0)}, meta={"sfx_provider": "local_synth"})
    basis = sc.basis_for("sfx_gen", project)
    assert basis.provider == "local_synth" and "закреплён локальный синтез" in basis.notice


async def test_sfx_switched_off_is_free_and_said(env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sfx_enabled", False)
    pid = await env.add_project(frames=1)
    basis = sc.basis_for("sfx_gen", await _project(env, pid))
    assert basis.credits == 0.0 and "выключены" in basis.notice


def test_sfx_outcome_text_names_local_synth() -> None:
    whole = sc.sfx_outcome_text({"sfx_degraded": True, "sfx_provider": "local_synth",
                                 "sfx_degraded_reason": "нет ключа ElevenLabs"})
    assert whole == "Звуки сделаны локальным синтезом (простые шумы), а не ElevenLabs: нет ключа ElevenLabs."
    part = sc.sfx_outcome_text({"sfx_degraded": True, "sfx_provider": "mixed", "sfx_synthetic_count": 2})
    assert "2 звуков заменены локальным синтезом" in part
    assert sc.sfx_outcome_text({"sfx_provider": "elevenlabs"}) == "Звуки сделаны через ElevenLabs."
    assert "пропущены" in sc.sfx_outcome_text({"sfx_skipped": True})
    assert sc.sfx_outcome_text({}) == ""


class _Ctx:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers

    async def call(self, method, path, **kw):
        for pattern, answer in self.answers.items():
            if re.fullmatch(pattern, path):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(path)


@pytest.mark.parametrize("code", ["sfx_gen", "assemble"])
async def test_finished_sound_step_says_the_sounds_are_local(code) -> None:
    meta = {"sfx_degraded": True, "sfx_provider": "local_synth", "sfx_degraded_reason": "нет ключа ElevenLabs"}
    ctx = _Ctx({r"/projects/7": {"id": 7, "title": "Кофе", "status": "sfx_ready", "meta": meta}})
    state = await bl._poll_any_step(ctx, {"project_id": 7, "step_code": code}, {})
    assert state.done and state.ok
    assert "локальным синтезом" in state.result["text_ru"] and "не ElevenLabs" in state.result["text_ru"]
    assert state.result["sfx_provider"] == "local_synth"


async def test_assemble_keeps_quiet_about_normal_elevenlabs_sounds() -> None:
    ctx = _Ctx({r"/projects/7": {"id": 7, "status": "assembled", "meta": {"sfx_provider": "elevenlabs"}}})
    state = await bl._poll_any_step(ctx, {"project_id": 7, "step_code": "assemble"}, {})
    assert "ElevenLabs" not in state.result["text_ru"]


def _pub(approved=(), published=None, status="assembled", **meta):
    project = {"id": 7, "status": status, "topic": "кофе",
               "meta": {"published_platforms": published or {}, **meta}}
    facts = {"platforms": [{"platform": p, "approved": p in approved} for p in pa.registry_platforms()]}
    return _Ctx({r"/projects/7": project, r"/projects/7/publications": facts})


async def test_publish_card_must_name_every_platform_the_step_will_post_to() -> None:
    """Шаг заказчика выкладывает везде, где ролика ещё нет: карточка не может назвать меньше."""
    plats = pa.registry_platforms()
    one = plats[0]
    assert await pa._platforms_expected(_pub(), {"project_id": 7, "platforms": plats}) is None
    extra = await pa._platforms_expected(_pub(), {"project_id": 7, "platforms": [one]})
    assert "на все площадки без публикации" in extra
    assert pa.PLATFORM_TITLES.get(plats[1], plats[1]) in extra
    rest = plats[1:]
    assert await pa._platforms_expected(_pub(published={one: "u"}), {"project_id": 7, "platforms": rest}) is None
    done = await pa._platforms_expected(_pub(published={one: "u"}), {"project_id": 7, "platforms": [one]})
    assert "уже опубликован" in done


def test_publish_effects_describe_the_customer_step() -> None:
    text = pa.effects_text({"project_id": 7, "platforms": pa.registry_platforms(), "caption": "кофе"})
    assert "сразу на все площадки, где его ещё нет" in text
    assert "панели" not in text


async def test_publish_poll_reports_done_and_missing_platforms() -> None:
    one, two = pa.registry_platforms()[:2]
    running = _pub(status="publishing")
    state = await pa._publish_poll(running, {"project_id": 7, "platforms": [one]}, {})
    assert state.done is False
    posted = _pub(published={one: "https://x/1"})
    state = await pa._publish_poll(posted, {"project_id": 7, "platforms": [one]}, {})
    assert state.ok and state.result["text_ru"].startswith("Ролик опубликован")
    part = _pub(published={one: "https://x/1"})
    state = await pa._publish_poll(part, {"project_id": 7, "platforms": [one, two]}, {})
    assert state.ok is False and pa.PLATFORM_TITLES.get(two, two) in state.error


async def test_finish_rule_and_next_step_reach_the_planner() -> None:
    from app.orchestrator.agents.project_nodes import FINISH_RULE, ProjectNodes

    async def meta(_pid):
        return {}

    async def frames(_pid):
        return 3

    async def status(_pid):
        return "audio_ready"

    async def empty(_pid):
        return []

    nodes = ProjectNodes(meta_of=meta, frames_of=frames, project_of=lambda _t: 7, status_of=status,
                         proposals_of=empty, blocked_of=empty, snapshots_of=empty)
    block = await nodes.block("доведи ролик до готового mp4", task_id=1)
    assert FINISH_RULE in block and "step_code=music" in block
    plain = await nodes.block("покажи ноды", task_id=1)
    assert FINISH_RULE not in plain and "следующий шаг" not in plain
