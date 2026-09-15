"""Перенос из форка заказчика (theirs/main c53a238b), звуковой блок.

1. SFX-ноды в линейном реестре и в множествах `auto_advance`.
2. Линейные fallback'и `_apply_approve`, когда SFX-нод на канвасе нет.
3. `voice_gain` в `sfx_mix.build_mux_audio_args` + SFX в mux `variant2`.
4. Таймаут ffmpeg в `variant2._run`.
5. `sfx_gen`: эндпойнт `/v1/sound-generation`, ретраи, локальный фоллбэк.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator import auto_advance, node_registry
from app.orchestrator.auto_advance import TRANSITIONS, StepTransition, _apply_approve
from app.services import sfx_mix

# ── реестр нод: sfx_plan / sfx_gen между music и assemble ─────────────────


def test_linear_node_types_have_sfx_between_music_and_assemble() -> None:
    types = node_registry.LINEAR_NODE_TYPES
    assert "sfx_plan" in types and "sfx_gen" in types
    assert types.index("music") < types.index("sfx_plan") < types.index("sfx_gen") < types.index("assemble")


def test_linear_running_pipeline_covers_sfx() -> None:
    running = [st for st, _ in node_registry.LINEAR_RUNNING_PIPELINE]
    assert ProjectStatus.sfx_planning in running
    assert ProjectStatus.generating_sfx in running


# ── auto_advance: множества и прогрессия ──────────────────────────────────


def test_auto_advance_sfx_sets_and_progression() -> None:
    assert ProjectStatus.music_ready in auto_advance._LINEAR_MEDIA_READY
    assert ProjectStatus.sfx_plan_ready in auto_advance._LINEAR_MEDIA_READY
    assert ProjectStatus.sfx_ready in auto_advance._LINEAR_MEDIA_READY

    assert ProjectStatus.sfx_planning in auto_advance._LINEAR_MEDIA_RUNNING
    assert ProjectStatus.generating_sfx in auto_advance._LINEAR_MEDIA_RUNNING

    prog = auto_advance.expected_status_progression(None)
    assert prog.index(ProjectStatus.generating_music) < prog.index(ProjectStatus.sfx_planning)
    assert prog.index(ProjectStatus.sfx_planning) < prog.index(ProjectStatus.generating_sfx)
    assert prog.index(ProjectStatus.generating_sfx) < prog.index(ProjectStatus.assembling)


# ── auto_advance: fallback'и, когда SFX-нод на канвасе нет ────────────────


class _StubAsyncSession:
    async def flush(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("execute() в этих кейсах не зовётся")


def _project(status: ProjectStatus) -> Project:
    p = Project(slug="t", topic="t", hero_mode="full_auto")
    p.status = status
    p.enrich_slots_count = 3
    p.hero_count = 1
    p.hero_variations = [1]
    p.auto_mode = True
    p.meta = {}
    return p


def _hitl() -> HITLRequest:
    h = HITLRequest(project_id=1, kind=HITLKind.approve_videos, decision=HITLDecision.pending)
    h.payload = {}
    h.tg_message_id = None
    return h


@pytest.mark.parametrize(
    ("ready", "expected"),
    [
        (ProjectStatus.music_ready, ProjectStatus.sfx_planning),
        (ProjectStatus.sfx_plan_ready, ProjectStatus.generating_sfx),
        (ProjectStatus.sfx_ready, ProjectStatus.assembling),
    ],
)
@pytest.mark.asyncio
async def test_apply_approve_sfx_linear_fallback(
    monkeypatch: pytest.MonkeyPatch,
    ready: ProjectStatus,
    expected: ProjectStatus,
) -> None:
    """Канвас без SFX-нод: graph BFS пуст и next_running пуст — идём линейно,
    а не пишем «no next step after music_ready» каждые 5 секунд."""

    async def _no_graph(*_a, **_kw):
        return None

    async def _skip(_session, _project, nxt):
        return nxt

    async def _data_ok(_session, _project, nxt):
        return nxt

    async def _commit(_session, project, _ready, nxt, **_kw):
        project.status = nxt
        return True

    monkeypatch.setattr(auto_advance, "_graph_next_running", _no_graph)
    monkeypatch.setattr(auto_advance, "skip_disabled_running_async", _skip)
    monkeypatch.setattr(auto_advance, "_apply_running_if_data_ok", _data_ok)
    monkeypatch.setattr(auto_advance, "_commit_running_after_prepare", _commit)

    project = _project(ready)
    # next_running=None — на канвасе SFX-нод нет, линейная таблица не помогает.
    transition = StepTransition(ready, None, HITLKind.approve_videos)
    await _apply_approve(_StubAsyncSession(), project, _hitl(), transition, bot=None, badge="")
    assert project.status is expected


@pytest.mark.asyncio
async def test_apply_approve_enrich_cap_fallback_without_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enrich_N_ready без стрелок на канвасе: fallback учитывает
    enrich_slots_count, а не гонит цепочку до enrich_5."""

    async def _no_graph(*_a, **_kw):
        return None

    async def _skip(_session, _project, nxt):
        return nxt

    async def _data_ok(_session, _project, nxt):
        return nxt

    async def _commit(_session, project, _ready, nxt, **_kw):
        project.status = nxt
        return True

    monkeypatch.setattr(auto_advance, "_graph_next_running", _no_graph)
    monkeypatch.setattr(auto_advance, "skip_disabled_running_async", _skip)
    monkeypatch.setattr(auto_advance, "_apply_running_if_data_ok", _data_ok)
    monkeypatch.setattr(auto_advance, "_commit_running_after_prepare", _commit)

    project = _project(ProjectStatus.enrich_2_ready)
    project.enrich_slots_count = 2
    transition = StepTransition(ProjectStatus.enrich_2_ready, None, HITLKind.approve_hero)
    await _apply_approve(_StubAsyncSession(), project, _hitl(), transition, bot=None, badge="")
    assert project.status is ProjectStatus.generating_image_prompts


def test_transitions_music_to_sfx_chain() -> None:
    assert TRANSITIONS[ProjectStatus.music_ready].next_running is ProjectStatus.sfx_planning
    assert TRANSITIONS[ProjectStatus.sfx_plan_ready].next_running is ProjectStatus.generating_sfx
    assert TRANSITIONS[ProjectStatus.sfx_ready].next_running is ProjectStatus.assembling


# ── sfx_mix: voice_gain ───────────────────────────────────────────────────


def test_sfx_mix_voice_gain(tmp_path: Path) -> None:
    sfx_file = tmp_path / "click.mp3"
    sfx_file.write_bytes(b"dummy")
    sfx_input = sfx_mix.SfxInput(path=sfx_file, t_start=1.5, gain=0.6, kind="hit")

    _args, fc = sfx_mix.build_mux_audio_args(
        bgm_path=None,
        bgm_gain=0.0,
        output_duration=10.0,
        tail=0.0,
        sfx=[sfx_input],
        voice_gain=1.25,
    )
    assert fc is not None
    assert "volume=1.2500" in fc  # применён к дорожке озвучки
    assert "adelay=1500|1500" in fc
    assert "amix=inputs=2" in fc


def test_sfx_mix_voice_gain_neutral_not_emitted(tmp_path: Path) -> None:
    sfx_file = tmp_path / "click.mp3"
    sfx_file.write_bytes(b"dummy")
    sfx_input = sfx_mix.SfxInput(path=sfx_file, t_start=0.5, gain=0.6, kind="hit")
    _args, fc = sfx_mix.build_mux_audio_args(
        bgm_path=None,
        bgm_gain=0.0,
        output_duration=5.0,
        tail=0.0,
        sfx=[sfx_input],
    )
    assert fc is not None
    assert "[vo]" in fc
    assert "apad=whole_dur=5.000[vo]" in fc  # без лишнего volume=


# ── variant2: SFX в mux + таймаут ffmpeg ──────────────────────────────────


@pytest.mark.asyncio
async def test_variant2_mux_passes_sfx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.montage import variant2

    sfx_file = tmp_path / "a.wav"
    sfx_file.write_bytes(b"dummy")
    captured: list[list[str]] = []

    async def _fake_run(cmd, **_kw):
        captured.append(cmd)

    monkeypatch.setattr(variant2, "_run", _fake_run)
    await variant2._mux(
        tmp_path / "v.mp4",
        tmp_path / "voice.mp3",
        tmp_path / "out.mp4",
        voice_s=12.0,
        bgm=None,
        sfx=[sfx_mix.SfxInput(path=sfx_file, t_start=2.0, gain=0.5, kind="hit")],
    )
    assert captured, "ffmpeg не собрался"
    cmd = captured[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=2000|2000" in fc
    assert "amix=inputs=2" in fc
    assert str(sfx_file) in cmd


@pytest.mark.asyncio
async def test_variant2_mux_without_sfx_keeps_plain_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.montage import variant2

    captured: list[list[str]] = []

    async def _fake_run(cmd, **_kw):
        captured.append(cmd)

    monkeypatch.setattr(variant2, "_run", _fake_run)
    await variant2._mux(
        tmp_path / "v.mp4",
        tmp_path / "voice.mp3",
        tmp_path / "out.mp4",
        voice_s=12.0,
        bgm=None,
        sfx=None,
    )
    fc = captured[0][captured[0].index("-filter_complex") + 1]
    assert fc.startswith("[1:a]volume=")
    assert "amix" not in fc


@pytest.mark.asyncio
async def test_variant2_run_kills_hanging_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.montage import variant2

    class _HangingProc:
        def __init__(self) -> None:
            self.killed = False
            self.returncode: int | None = None

        async def communicate(self):
            await asyncio.sleep(10.0)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return 0

    proc = _HangingProc()

    async def _spawn(*_a, **_kw):
        return proc

    monkeypatch.setattr(variant2.asyncio, "create_subprocess_exec", _spawn)
    with pytest.raises(TimeoutError, match="висел дольше"):
        await variant2._run(["ffmpeg", "-i", "dummy.mp4"], context="mux", timeout=0.05)
    assert proc.killed is True


# ── sfx_gen: эндпойнт, ретраи, фоллбэк на локальный синтез ────────────────


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeClient:
    """Мок httpx.AsyncClient: отдаёт заранее заготовленные ответы по очереди."""

    calls: list[dict] = []
    responses: list = []

    def __init__(self, **kwargs) -> None:
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, url: str, **kwargs):
        type(self).calls.append({"url": url, **kwargs})
        resp = type(self).responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _install_fake_httpx(monkeypatch: pytest.MonkeyPatch, responses: list) -> type[_FakeClient]:
    import httpx

    _FakeClient.calls = []
    _FakeClient.responses = list(responses)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return _FakeClient


@pytest.mark.asyncio
async def test_elevenlabs_sfx_uses_sound_generation_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    client = _install_fake_httpx(monkeypatch, [_FakeResponse(200, content=b"MP3")])
    out = tmp_path / "sfx" / "a.mp3"
    await sfx_gen._elevenlabs_sfx("boom", 1.0, out)
    assert client.calls[0]["url"] == "https://api.elevenlabs.io/v1/sound-generation"
    assert out.read_bytes() == b"MP3"
    timeout = client.init_kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 90.0 and timeout.connect == 10.0


@pytest.mark.asyncio
async def test_elevenlabs_sfx_retries_on_5xx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _install_fake_httpx(
        monkeypatch,
        [
            _FakeResponse(503, text="upstream"),
            _FakeResponse(429, text="slow down"),
            _FakeResponse(200, content=b"MP3"),
        ],
    )
    out = tmp_path / "a.mp3"
    await sfx_gen._elevenlabs_sfx("boom", 1.0, out)
    assert len(client.calls) == 3
    assert out.read_bytes() == b"MP3"


@pytest.mark.asyncio
async def test_elevenlabs_sfx_no_retry_on_4xx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    client = _install_fake_httpx(monkeypatch, [_FakeResponse(401, text="bad key")])
    with pytest.raises(RuntimeError, match="11labs sfx 401"):
        await sfx_gen._elevenlabs_sfx("boom", 1.0, tmp_path / "a.mp3")
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_elevenlabs_sfx_gives_up_after_three_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _install_fake_httpx(
        monkeypatch,
        [_FakeResponse(500, text="boom")] * 3,
    )
    with pytest.raises(RuntimeError, match="11labs sfx 500"):
        await sfx_gen._elevenlabs_sfx("boom", 1.0, tmp_path / "a.mp3")
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_generate_sfx_files_falls_back_to_local_synth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """API упал — пишем локальный синтез и помечаем провайдера честно."""
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _boom(*_a, **_kw):
        raise RuntimeError("11labs sfx 500: boom")

    monkeypatch.setattr(sfx_gen, "_elevenlabs_sfx", _boom)

    session = SimpleNamespace(flush=lambda: asyncio.sleep(0))
    project = SimpleNamespace(id=1, meta={}, data_dir=tmp_path)
    events = [SfxEvent(1, 0.5, 0.8, "whoosh", "swoosh", 0.5, False)]
    files = await sfx_gen.generate_sfx_files(session, project, events)
    assert len(files) == 1
    assert files[0]["provider"] == "local_synth"
    assert files[0]["prompt"] == "swoosh"
    assert Path(files[0]["path"]).suffix == ".wav"


@pytest.mark.asyncio
async def test_generate_sfx_files_forced_local_synth_skips_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """meta.sfx_provider == local_synth: в сеть не ходим даже с ключом."""
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _forbidden(*_a, **_kw):  # pragma: no cover
        raise AssertionError("ElevenLabs не должен вызываться")

    monkeypatch.setattr(sfx_gen, "_elevenlabs_sfx", _forbidden)

    session = SimpleNamespace(flush=lambda: asyncio.sleep(0))
    project = SimpleNamespace(id=1, meta={"sfx_provider": "local_synth"}, data_dir=tmp_path)
    events = [SfxEvent(1, 0.5, 0.8, "hit", "bang", 0.5, False)]
    files = await sfx_gen.generate_sfx_files(session, project, events)
    assert files[0]["provider"] == "local_synth"


# ── generate_music: чистка ответа GPT перед проверкой длины ───────────────


def test_clean_suno_prompt_strips_wrappers() -> None:
    from app.orchestrator.steps.generate_music import _clean_suno_prompt

    raw = '```\n**Промпт**: "cinematic dark orchestral, deep brass, instrumental"\n```'
    assert _clean_suno_prompt(raw) == "cinematic dark orchestral, deep brass, instrumental"
    assert _clean_suno_prompt("Prompt: ambient pads, slow tempo") == "ambient pads, slow tempo"
    assert _clean_suno_prompt(None) == ""


# ── sfx_gen: ретрай на сетевой ошибке (не RuntimeError) ───────────────────


@pytest.mark.asyncio
async def test_elevenlabs_sfx_retries_on_transport_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Порвалась сеть (не HTTP-код) — повтор, а не мгновенный отказ."""
    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    slept: list[float] = []

    async def _no_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _install_fake_httpx(
        monkeypatch,
        [ConnectionError("dns died"), _FakeResponse(200, content=b"MP3")],
    )
    out = tmp_path / "a.mp3"
    await sfx_gen._elevenlabs_sfx("boom", 1.0, out)
    assert len(client.calls) == 2
    assert slept == [2.0]
    assert out.read_bytes() == b"MP3"


@pytest.mark.asyncio
async def test_elevenlabs_sfx_reraises_last_transport_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Все попытки легли на транспорте — наружу уходит последняя ошибка."""
    from app.services import sfx_gen
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = _install_fake_httpx(monkeypatch, [ConnectionError("dns died")] * 3)
    with pytest.raises(ConnectionError, match="dns died"):
        await sfx_gen._elevenlabs_sfx("boom", 1.0, tmp_path / "a.mp3")
    assert len(client.calls) == 3


# ── sfx_gen: verify красный → локальный синтез и отказ ────────────────────


@pytest.mark.asyncio
async def test_generate_sfx_files_verify_red_switches_to_local_synth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """API отдал брак: verify красный → перерисовываем локально и принимаем."""
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")

    async def _ok(_prompt: str, _duration: float, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"MP3")
        return out_path

    monkeypatch.setattr(sfx_gen, "_elevenlabs_sfx", _ok)

    seen: list[tuple[str, float]] = []

    def _verify(path: Path, *, expect_duration=None, duration_tol=0.35, **_kw) -> list[str]:
        seen.append((path.suffix, duration_tol))
        return ["rms -70 dB"] if len(seen) == 1 else []

    monkeypatch.setattr(sfx_gen, "verify_audio_file", _verify)

    session = SimpleNamespace(flush=lambda: asyncio.sleep(0))
    project = SimpleNamespace(id=7, meta={}, data_dir=tmp_path)
    events = [SfxEvent(1, 0.5, 0.8, "hit", "bang", 0.5, False)]

    files = await sfx_gen.generate_sfx_files(session, project, events)
    assert len(files) == 1
    assert files[0]["provider"] == "local_synth"
    assert Path(files[0]["path"]).suffix == ".wav"
    # первый заход — mp3 с широким допуском, второй — wav с жёстким 0.6
    assert seen[0][0] == ".mp3"
    assert seen[1] == (".wav", 0.6)


@pytest.mark.asyncio
async def test_generate_sfx_files_raises_when_verify_stays_red(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Локальный синтез тоже красный — шаг падает, а не отдаёт пустой список."""
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(sfx_gen, "verify_audio_file", lambda *_a, **_kw: ["пустой файл"])

    session = SimpleNamespace(flush=lambda: asyncio.sleep(0))
    project = SimpleNamespace(id=7, meta={}, data_dir=tmp_path)
    events = [SfxEvent(1, 0.5, 0.8, "hit", "bang", 0.5, False)]

    with pytest.raises(RuntimeError, match="ни один файл не прошёл верификацию"):
        await sfx_gen.generate_sfx_files(session, project, events)


# ── sfx_mix: откуда берутся старты кадров ─────────────────────────────────


def test_planned_frame_starts_without_data_dir() -> None:
    """Проект без data_dir (голый SimpleNamespace) — пустая карта, не падение."""
    assert sfx_mix._planned_frame_starts(SimpleNamespace()) == {}


def test_planned_frame_starts_from_words_json(tmp_path: Path) -> None:
    import json

    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    (audio / "words_1.json").write_text(
        json.dumps({"frames": [{"frame_number": 1, "start_ts": 0.0}, {"frame_number": 2, "start_ts": 3.5}]}),
        encoding="utf-8",
    )
    starts = sfx_mix._planned_frame_starts(SimpleNamespace(data_dir=tmp_path))
    assert starts == {1: 0.0, 2: 3.5}


def test_planned_frame_starts_broken_words_json_falls_to_plan(tmp_path: Path) -> None:
    """words_*.json битый — не роняем микс, читаем sfx_plan.json."""
    import json

    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    (audio / "words_1.json").write_text("{не json", encoding="utf-8")
    (tmp_path / "sfx_plan.json").write_text(
        json.dumps({"frame_starts": {"1": 0.0, "2": 4.25}}), encoding="utf-8"
    )
    starts = sfx_mix._planned_frame_starts(SimpleNamespace(data_dir=tmp_path))
    assert starts == {1: 0.0, 2: 4.25}


def test_planned_frame_starts_broken_plan_json_is_empty(tmp_path: Path) -> None:
    (tmp_path / "sfx_plan.json").write_text("{битый", encoding="utf-8")
    assert sfx_mix._planned_frame_starts(SimpleNamespace(data_dir=tmp_path)) == {}


def test_sfx_mix_voice_gain_without_output_duration(tmp_path: Path) -> None:
    """Длительность неизвестна — anull вместо apad, усиление всё равно на месте."""
    sfx_file = tmp_path / "click.mp3"
    sfx_file.write_bytes(b"dummy")
    _args, fc = sfx_mix.build_mux_audio_args(
        bgm_path=None,
        bgm_gain=0.0,
        output_duration=None,
        tail=0.0,
        sfx=[sfx_mix.SfxInput(path=sfx_file, t_start=1.0, gain=0.6, kind="hit")],
        voice_gain=0.8,
    )
    assert fc is not None
    assert "[1:a]anull,volume=0.8000[vo]" in fc


# ── sfx_plan: таймлайн кадров ─────────────────────────────────────────────


def test_frame_timeline_broken_words_json_falls_back_to_frames(tmp_path: Path) -> None:
    """Битый words_*.json не роняет планировщик — идём по Frame.start_ts."""
    from app.services import sfx_plan

    audio = tmp_path / "audio"
    audio.mkdir(parents=True)
    (audio / "words_1.json").write_text("{битый", encoding="utf-8")

    frames = [
        SimpleNamespace(number=1, start_ts=0.0, end_ts=2.0, voiceover_text="Привет"),
        SimpleNamespace(number=2, start_ts=2.0, end_ts=5.0, voiceover_text="Кадр 2"),
    ]
    rows = sfx_plan.frame_timeline(frames, project=SimpleNamespace(data_dir=tmp_path))
    assert [r["frame_number"] for r in rows] == [1, 2]
    assert rows[0]["voiceover"] == "Привет"
    # служебный «кадр N» в закадр не попадает
    assert "voiceover" not in rows[1]


def test_frame_timeline_mixed_ts_uses_duration_for_broken_frame() -> None:
    """Часть кадров без start_ts/end_ts — их длина берётся из duration_seconds."""
    from app.services import sfx_plan

    frames = [
        SimpleNamespace(number=1, start_ts=0.0, end_ts=2.0, duration_seconds=None, voiceover_text=""),
        SimpleNamespace(number=2, start_ts=None, end_ts=None, duration_seconds=4.0, voiceover_text=""),
        SimpleNamespace(number=3, start_ts=None, end_ts=None, duration_seconds=None, voiceover_text=""),
    ]
    rows = sfx_plan.frame_timeline(frames)
    assert rows[1] == {"frame_number": 2, "start": 2.0, "end": 6.0}
    # нет ни ts, ни duration — 3 секунды по умолчанию
    assert rows[2] == {"frame_number": 3, "start": 6.0, "end": 9.0}


@pytest.mark.asyncio
async def test_plan_sfx_events_writes_plan_with_frame_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """План уходит на диск вместе с frame_starts и offset_in_frame."""
    import json

    from app.services import sfx_plan

    frames = [
        SimpleNamespace(number=1, start_ts=0.0, end_ts=3.0, voiceover_text="раз", attrs={}, meaning=""),
        SimpleNamespace(number=2, start_ts=3.0, end_ts=7.0, voiceover_text="два", attrs={}, meaning=""),
    ]
    payload = {
        "events": [
            {
                "frame_number": 2,
                "t_start": 3.5,
                "duration": 1.0,
                "kind": "whoosh",
                "prompt": "deep whoosh",
                "gain": 0.5,
                "duck": False,
            }
        ]
    }

    async def _fake_text_job(_project, **_kw):
        return SimpleNamespace(payload=payload, attempts=1)

    monkeypatch.setattr(sfx_plan, "text_job", _fake_text_job)

    project = SimpleNamespace(id=3, meta={}, data_dir=tmp_path)
    events = await sfx_plan.plan_sfx_events(None, project, frames)
    assert len(events) == 1 and events[0].kind == "whoosh"

    saved = json.loads((tmp_path / "sfx_plan.json").read_text(encoding="utf-8"))
    assert saved["frame_starts"] == {"1": 0.0, "2": 3.0}
    assert saved["events"][0]["offset_in_frame"] == 0.5
