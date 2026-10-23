"""Промты картинок из чата: отказ по кадрам без данных сцены и честный итог по пустым промтам.

Тексты промтов взяты из проектов, где нода «Работа с GPT» не отработала и модель на каждый
кадр ответила заглушкой (#10, #12, #14).
"""
from __future__ import annotations

import itertools
import time
from types import SimpleNamespace

import pytest

from app.generation_options import is_scene_prompt_hollow
from app.models import Frame, Project, ProjectStatus
from app.services.action_layer import builtin_launch as bl
from app.services.action_layer import img_pr_guard as ipg
from app.services.action_layer import step_outcome as so
from tests.action_layer_support import env  # noqa: F401

STYLE = ("\nSTYLE: Archival Noir Watercolor Grunge Dossier Poster Illustration. Transparent "
         "watercolor washes. Negative: photorealism, glossy 3D render.")

HOLLOW_EXACT = ("Референс: нет исходных данных для заполнения; персонажи не заданы. Фон: нет "
                "исходных данных для заполнения. Действие: нет исходных данных для заполнения; не "
                "выдумывать физическое действие, героев, предметы или локацию. Свет: нет исходных "
                "данных для заполнения; использовать только заданные источники света." + STYLE)
HOLLOW_ABSENT = ("Фон: исходные данные о фоне отсутствуют; окружение не выдумывать. Действие: "
                 "исходные данные о физическом действии отсутствуют. Свет: источник, направление и "
                 "характер света не указаны; не добавлять конкретные источники. Акцент / фокус: "
                 "исходные данные отсутствуют. Смысл сцены (видимое): исходные данные отсутствуют."
                 + STYLE)
HOLLOW_NO_DATA = ("Фон: данных о фоне и локации в записи кадра нет; не добавлять окружение или "
                  "предметы. Действие: не изображать выдуманную сцену; исходных визуальных данных "
                  "для действия нет. Свет: источник и направление не заданы, сохранить сдержанный "
                  "контраст без придумывания обстановки. Акцент / фокус: исходные данные "
                  "отсутствуют. Место и время: не указаны.")
HOLLOW_ABOUT = ("Референс: нет исходных данных о персонажах в кадре. Фон: нет исходных данных о "
                "фоне, поверхностях, материалах, цветах и глубине сцены. Действие: нет исходных "
                "данных о видимом физическом действии. Важные детали: предметы не заданы, не "
                "добавлять документы. Место и время: место и эпоха не заданы." + STYLE)
HOLLOW_ONLY_HERO = ("Референс: character sheet for c01 / неясыть — сохранить округлую голову и "
                    "лицевой диск. Фон: исходные данные о фоне кадра отсутствуют. Действие: "
                    "исходные данные о видимом действии кадра отсутствуют. Свет: источник, "
                    "направление и тон освещения не указаны. Scene feature / shot scale: план "
                    "кадра не задан." + STYLE)
HOLLOW_SHORT = "Нет исходных данных для заполнения. "
HOLLOW_WITH_ID = ("[ID: P12-F23-ab12cd34]\nНет исходных данных для заполнения сцены: в записи кадра "
                  "отсутствуют place, shot01_bg, shot01_action, lighting. Не добавлять "
                  "неподтверждённые элементы сцены.")

REAL = ("Фон: подземная камера муравьиного гнезда, тесные земляные стенки с влажными слоями "
        "почвы. Действие: листорезы переносят кусочки листьев по земляному ходу внутрь гнезда. "
        "Свет: холодный рассеянный свет из узкого входного тоннеля. Детали: только почва, "
        "тоннель и листья." + STYLE)
PARTIAL = ("Фон: исходные place и shot01_bg не предоставлены; не добавлять окружение или "
           "предметы. Действие: взрослая сухопутная черепаха неподвижно стоит на четырёх лапах, "
           "панцирь целиком виден сбоку. Свет: источник и направление не заданы; сдержанный "
           "контрастный свет." + STYLE)
REAL_WITH_NET = "Фон: пустая комната, в которой нет мебели. Действие: девочка смотрит в окно."
REAL_PLAIN = "A wide shot of a snowy forest at dawn, a red fox runs across the clearing."


@pytest.mark.parametrize("prompt", [HOLLOW_EXACT, HOLLOW_ABSENT, HOLLOW_NO_DATA, HOLLOW_ABOUT,
                                    HOLLOW_ONLY_HERO, HOLLOW_SHORT, HOLLOW_WITH_ID, "", None,
                                    "[ID: P1-F1-abcdef12]"])
def test_hollow_prompt_variants(prompt) -> None:
    assert ipg.prompt_is_hollow(prompt)


@pytest.mark.parametrize("prompt", [REAL, PARTIAL, REAL_WITH_NET, REAL_PLAIN])
def test_prompt_with_scene_content_is_not_hollow(prompt) -> None:
    assert not ipg.prompt_is_hollow(prompt)


def test_studio_check_misses_the_variants_this_check_catches() -> None:
    for prompt in (HOLLOW_ABSENT, HOLLOW_NO_DATA, HOLLOW_ABOUT, HOLLOW_ONLY_HERO, HOLLOW_SHORT):
        assert not is_scene_prompt_hollow(prompt) and ipg.prompt_is_hollow(prompt)


def _fr(number, *, vo="закадр кадра", prompt=None, meaning=None, **attrs):
    return SimpleNamespace(number=number, uuid=f"u{number}", voiceover_text=vo, meaning=meaning,
                           image_prompt=prompt, attrs=attrs)


def test_scene_fields_ignore_empty_and_character_only_attrs() -> None:
    assert ipg.scene_fields(_fr(1, characters="", persons="", персонажи="")) == []
    assert ipg.scene_fields(_fr(2, characters="c01 неясыть")) == []
    assert ipg.scene_fields(_fr(3, place="лес")) == ["place"]
    assert ipg.scene_fields(_fr(4, meaning="сова слушает")) == ["meaning"]
    assert ipg.scene_fields(_fr(5, действие="сова пикирует")) == ["shot01_action", "действие"]
    assert ipg.scene_fields(_fr(6, кадры=[{"id": "k1", "действие": "взлёт"}])) == ["кадры"]


@pytest.mark.xfail(strict=True, reason='merge line: img_pr takes every frame with voiceover and multi-shot frames (xlsx_step_runners._is_real_shot), not only grid parents')
def test_frames_without_scene_count_only_frames_that_need_a_prompt() -> None:
    frames = [
        _fr(1),                                    # нужен промт, сцены нет
        _fr(2, prompt=HOLLOW_EXACT),               # заглушка - промт снова нужен, сцены нет
        _fr(3, place="лес"),                       # сцена есть
        _fr(4, prompt=REAL),                       # промт уже есть - шаг его не тронет
        _fr(5, vo=""),                             # без закадра шаг кадр не берёт
        _fr(6, camera_subdivide={"role": "shot", "parent_uuid": "u1"}),  # дочерний шот
    ]
    assert ipg.frames_without_scene(frames) == ([1, 2], 3)


@pytest.mark.xfail(strict=True, reason='merge line: img_pr takes every frame with voiceover and multi-shot frames (xlsx_step_runners._is_real_shot), not only grid parents')
def test_hollow_frames_count_vo_parents() -> None:
    frames = [_fr(1, prompt=HOLLOW_ABSENT), _fr(2, prompt=REAL), _fr(3, prompt=HOLLOW_SHORT),
              _fr(4, prompt=HOLLOW_SHORT, vo=""),
              _fr(5, prompt=HOLLOW_SHORT, camera_subdivide={"role": "shot"})]
    assert ipg.hollow_frames(frames) == ([1, 3], 3)


def test_numbers_are_shortened() -> None:
    assert ipg.numbers_ru([1, 2, 3]) == "1, 2, 3"
    assert ipg.numbers_ru(list(range(1, 21))).endswith("12 и ещё 8")


# --- граф --------------------------------------------------------------------------------------


def _meta(*nodes, edges=()):
    return {"canvas_graph": {
        "nodes": [{"id": k, "type": t, "data": {"label": label} if label else {}}
                  for k, t, label in nodes],
        "edges": [{"source": s, "target": t} for s, t in edges]}}


META = _meta(("n_split", "split", None), ("n_hero", "hero", None),
             ("n_gpt1", "excel_gpt", "Каркас"), ("n_gpt2", "excel_gpt", "Сцены"),
             ("n_report", "excel_gpt", "Отчёт"), ("n_img_pr", "image_prompts", None),
             ("n_img", "images", None),
             edges=[("n_split", "n_gpt1"), ("n_gpt1", "n_hero"), ("n_hero", "n_gpt2"),
                    ("n_gpt2", "n_img_pr"), ("n_img_pr", "n_img")])


def _runs(**status):
    return [{"node_key": k, "status": v} for k, v in status.items()]


def test_img_pr_step_is_always_checked() -> None:
    assert ipg.launch_skips_scene(META, "img_pr", None, [], auto_chain=False)


def test_auto_chain_checked_only_when_no_scene_node_will_run() -> None:
    pending = _runs(n_gpt1="done", n_gpt2="pending")
    done = _runs(n_gpt1="done", n_gpt2="skipped")
    assert not ipg.launch_skips_scene(META, "hero", None, pending, auto_chain=True)
    assert ipg.launch_skips_scene(META, "hero", None, done, auto_chain=True)
    assert not ipg.launch_skips_scene(META, "hero", None, done, auto_chain=False)
    # План, сценарий и разбивка сбрасывают ноды ниже - сцены пойдут заново.
    assert not ipg.launch_skips_scene(META, "split", None, done, auto_chain=True)
    # Нода «Работа с GPT» перед промтами как старт сама пишет сцены.
    assert not ipg.launch_skips_scene(META, "excel_gpt", "n_gpt2", done, auto_chain=True)
    # «Каркас» сцен не пишет: цепочка от него идёт мимо пропущенных «Сцен» к промтам.
    assert ipg.launch_skips_scene(META, "excel_gpt", "n_gpt1", done, auto_chain=True)
    assert not ipg.launch_skips_scene(META, "excel_gpt", "n_gpt1", pending, auto_chain=True)


def test_only_the_nearest_gpt_node_fills_scenes() -> None:
    assert ipg.scene_nodes_before_img_pr(META) == ["n_gpt2"]
    fan = _meta(("n_gpt1", "excel_gpt", None), ("n_sd", "sd_agent", None),
                ("n_asm", "sd_assemble", None), ("n_img_pr", "image_prompts", None),
                edges=[("n_gpt1", "n_sd"), ("n_sd", "n_asm"), ("n_asm", "n_img_pr")])
    assert ipg.scene_nodes_before_img_pr(fan) == ["n_asm"]


# Граф и прогон как в #10/#12: вторая «Работа с GPT» так и ждёт, а промты картинок уже шли.
INCIDENT = _meta(("n_split", "split", None), ("n_excel_gpt_1", "excel_gpt", "Работа с GPT"),
                 ("n_hero", "hero", None), ("n_items", "items", None),
                 ("n_excel_gpt_2", "excel_gpt", "Работа с GPT"),
                 ("n_image_prompts", "image_prompts", None), ("n_images", "images", None),
                 edges=[("n_split", "n_excel_gpt_1"), ("n_excel_gpt_1", "n_hero"),
                        ("n_hero", "n_items"), ("n_items", "n_excel_gpt_2"),
                        ("n_excel_gpt_2", "n_image_prompts"), ("n_image_prompts", "n_images")])
INCIDENT_RUNS = [{"node_key": k, "status": v} for k, v in (
    ("n_split", "done"), ("n_excel_gpt_1", "done"), ("n_hero", "done"), ("n_items", "done"),
    ("n_excel_gpt_2", "pending"), ("n_image_prompts", "failed"), ("n_images", "pending"))]


def test_pending_scene_node_that_the_queue_already_passed_does_not_count() -> None:
    assert ipg.jumped_scene_nodes(INCIDENT, INCIDENT_RUNS) == ["n_excel_gpt_2"]
    assert ipg.queue_skips_scene(INCIDENT, INCIDENT_RUNS)
    assert ipg.launch_skips_scene(INCIDENT, "hero", None, INCIDENT_RUNS, auto_chain=True)
    assert ipg.launch_skips_scene(INCIDENT, "excel_gpt", "n_excel_gpt_1", INCIDENT_RUNS,
                                  auto_chain=True)
    # Сама вторая нода как старт сцены пишет.
    assert not ipg.launch_skips_scene(INCIDENT, "excel_gpt", "n_excel_gpt_2", INCIDENT_RUNS,
                                      auto_chain=True)
    # Промты картинок ещё не запускались - ждущая нода сцены пойдёт.
    fresh = [r if r["node_key"] != "n_image_prompts" else {**r, "status": "pending"}
             for r in INCIDENT_RUNS]
    assert ipg.jumped_scene_nodes(INCIDENT, fresh) == []
    assert not ipg.queue_skips_scene(INCIDENT, fresh)


def test_stale_active_gpt_key_means_the_scene_node_will_be_closed_without_a_call() -> None:
    runs = _runs(n_gpt1="done", n_hero="done", n_gpt2="pending", n_img_pr="pending")
    stale = {**META, "active_excel_gpt_node_key": "n_gpt1", "excel_gpt_completed_keys": ["n_gpt1"]}
    assert ipg.jumped_scene_nodes(stale, runs) == ["n_gpt2"]
    assert ipg.queue_skips_scene(stale, runs)
    assert ipg.launch_skips_scene(stale, "hero", None, runs, auto_chain=True)
    # Явный запуск «Работы с GPT» из чата снимает залипший ключ: дальше «Сцены» пойдут.
    assert not ipg.launch_skips_scene(stale, "excel_gpt", "n_gpt1", runs, auto_chain=True)
    # Ключ указывает на саму ноду сцен, ещё не готов или стоит полный перезапуск - не залип.
    for meta in ({**stale, "active_excel_gpt_node_key": "n_gpt2"},
                 {**stale, "excel_gpt_completed_keys": []},
                 {**stale, "excel_gpt_ui_force_full": True}, META):
        assert not ipg.queue_skips_scene(meta, runs)


def test_queue_checked_when_scene_nodes_are_done_or_skipped() -> None:
    skipped = _runs(n_gpt1="done", n_gpt2="skipped", n_img_pr="pending")
    assert ipg.queue_skips_scene(META, skipped)
    assert not ipg.queue_skips_scene(META, _runs(n_gpt1="done", n_gpt2="pending"))
    assert not ipg.queue_skips_scene(META, _runs(n_gpt1="done", n_gpt2="done", n_img_pr="done"))
    assert not ipg.queue_skips_scene(META, skipped, mode="until_node", target_key="n_gpt2")
    assert ipg.queue_skips_scene(META, skipped, mode="until_node", target_key="n_img")
    assert ipg.queue_skips_scene(META, skipped, mode="until_node", target_type="image_prompts")
    assert not ipg.queue_skips_scene({}, skipped)


def test_reason_names_the_skipped_scene_node() -> None:
    runs = _runs(n_gpt1="done", n_gpt2="skipped")
    text = ipg.no_scene_text(12, [2, 3], 4, META, runs)
    assert text.startswith("у проекта #12 кадры 2, 3 (2 из 4, которым нужен промт) без данных сцены")
    assert "пишет нода «Работа с GPT» «Сцены» (n_gpt2)" in text
    assert "«Каркас»" not in text and "деньги уйдут зря" in text
    assert text.endswith("Сначала запустите ноду сцен, потом промты картинок")
    done = ipg.no_scene_text(12, [2], 4, META, _runs(n_gpt1="done", n_gpt2="done"))
    assert "пишет нода «Работа с GPT» «Сцены» (n_gpt2)" in done and "«Каркас»" not in done


def test_reason_says_the_queue_passes_the_waiting_scene_node() -> None:
    text = ipg.no_scene_text(12, [1, 2], 2, INCIDENT, INCIDENT_RUNS)
    assert "(n_excel_gpt_2) ещё не отработала, но очередь и автопродвижение проходят мимо неё" in text
    assert "n_excel_gpt_1" not in text
    assert "пишет нода «Работа с GPT 2» (n_excel_gpt_2)" in text  # подпись не повторяется
    assert text.endswith("Сначала запустите эту ноду «Работа с GPT» отдельно, потом промты картинок")


# --- действия на базе проекта ---------------------------------------------------------------

_SLUGS = itertools.count(1)


async def _seed(env, frames: list[dict], meta: dict | None = None) -> int:
    async with env.factory() as s:
        p = Project(topic="совы", title="Совы", slug=f"imgpr-{next(_SLUGS)}",
                    status=ProjectStatus.enrich_2_ready, meta=dict(meta or META))
        s.add(p)
        await s.commit()
        pid = p.id
    # Одна база на этой линии: кадры в ней же (project.db нет).
    async with env.factory() as s:
        for row in frames:
            s.add(Frame(project_id=pid, **{"voiceover_text": "закадр", "attrs": {}, **row}))
        await s.commit()
    return pid


class _Ctx:
    def __init__(self, pid: int, status: str = "enrich_2_ready", node_runs=None, meta=None):
        self.pid = pid
        self.detail = {"id": pid, "title": "Совы", "status": status, "meta": meta or META,
                       "auto_mode": False}
        self.node_runs = node_runs if node_runs is not None else _runs(n_gpt1="done",
                                                                       n_gpt2="skipped")
        self.calls: list[tuple[str, str]] = []

    async def call(self, method, path, **kw):
        self.calls.append((method, path))
        if path == "/projects":
            return [{"id": self.pid, "status": self.detail["status"]}]
        if path.endswith("/run"):
            return {"node_runs": self.node_runs}
        return self.detail


VO_ONLY = [{"number": 1, "uuid": "a1", "attrs": {"characters": "", "персонажи": ""}},
           {"number": 2, "uuid": "a2"},
           {"number": 3, "uuid": "a3", "attrs": {"place": "лес", "shot01_action": "сова летит"}},
           {"number": 1_000_004, "uuid": "old", "attrs": {"archived": True}}]


async def test_run_step_refuses_img_pr_without_scene_data(env) -> None:
    pid = await _seed(env, VO_ONLY)
    reason = await bl._pre_img_pr_scenes(_Ctx(pid), {"project_id": pid, "step_code": "img_pr"})
    assert reason.startswith(f"у проекта #{pid} кадры 1, 2 (2 из 3, которым нужен промт)")
    assert "«Сцены» (n_gpt2)" in reason and "1000004" not in reason


async def test_run_step_allows_img_pr_when_frames_have_scenes(env) -> None:
    pid = await _seed(env, [{"number": 1, "uuid": "a1", "meaning": "сова слушает"},
                            {"number": 2, "uuid": "a2", "attrs": {"place": "лес"}},
                            {"number": 3, "uuid": "a3", "image_prompt": REAL}])
    assert await bl._pre_img_pr_scenes(_Ctx(pid), {"project_id": pid, "step_code": "img_pr"}) is None


async def test_run_step_other_steps_are_not_checked(env) -> None:
    pid = await _seed(env, VO_ONLY)
    assert await bl._pre_img_pr_scenes(_Ctx(pid), {"project_id": pid, "step_code": "img"}) is None
    chain = {"project_id": pid, "step_code": "hero", "allow_auto_chain": True}
    assert (await bl._pre_img_pr_scenes(_Ctx(pid), chain)).startswith(f"у проекта #{pid} кадры 1, 2")
    pending = _Ctx(pid, node_runs=_runs(n_gpt1="done", n_gpt2="pending"))
    assert await bl._pre_img_pr_scenes(pending, chain) is None


async def test_run_step_spec_carries_the_precondition() -> None:
    spec = next(s for s in bl._specs() if s.id == "step.run_step")
    queue = next(s for s in bl._specs() if s.id == "queue.enqueue_many")
    assert "img_pr_scene_input" in {p.id for p in spec.preconditions}
    assert "img_pr_scene_input" in {p.id for p in queue.preconditions}
    assert spec.risk == "destructive" and queue.risk == "destructive"


async def test_queue_refuses_project_that_would_reach_img_pr_without_scenes(env, monkeypatch) -> None:
    good = await _seed(env, [{"number": 1, "uuid": "g1", "attrs": {"place": "лес"}}])
    bad = await _seed(env, VO_ONLY)
    ctx = _Ctx(bad)

    async def projects(_ctx, ids):
        return [{"id": pid, "meta": META} for pid in ids]

    monkeypatch.setattr(bl, "_load_projects", projects)
    reason = await bl._pre_queue_img_pr_scenes(ctx, {"project_ids": [good, bad]})
    assert reason.startswith(f"у проекта #{bad} кадры 1, 2")
    until = {"project_ids": [bad], "mode": "until_node", "target_node_key": "n_gpt2"}
    assert await bl._pre_queue_img_pr_scenes(ctx, until) is None
    ctx.node_runs = _runs(n_gpt1="done", n_gpt2="pending")
    assert await bl._pre_queue_img_pr_scenes(ctx, {"project_ids": [bad]}) is None
    # Очередь уже проскочила ждущие «Сцены» к промтам картинок - снова туда не пускаем.
    ctx.node_runs = _runs(n_gpt1="done", n_gpt2="pending", n_img_pr="failed")
    jumped = await bl._pre_queue_img_pr_scenes(ctx, {"project_ids": [bad]})
    assert jumped.startswith(f"у проекта #{bad} кадры 1, 2") and "проходят мимо неё" in jumped


@pytest.fixture
def studio(monkeypatch):
    monkeypatch.setattr(so, "_stop_flag", lambda pid: False)
    monkeypatch.setattr(so, "_in_gen_queue", lambda pid: False)


def _started() -> dict:
    return {"started": True, "step_code": "img_pr", "running_status": "generating_image_prompts",
            "status_before": "enrich_2_ready", "launched_at": time.time() - 60}


async def test_hollow_prompts_after_img_pr_are_not_success(env, studio) -> None:
    pid = await _seed(env, [{"number": 1, "uuid": "a1", "image_prompt": HOLLOW_ABSENT},
                            {"number": 2, "uuid": "a2", "image_prompt": HOLLOW_NO_DATA},
                            {"number": 3, "uuid": "a3", "image_prompt": REAL,
                             "attrs": {"place": "лес"}}])
    state = await bl._poll_any_step(_Ctx(pid, status="image_prompts_ready"),
                                    {"project_id": pid, "step_code": "img_pr"}, _started())
    assert state.done and not state.ok
    assert state.error.startswith(f"#{pid} «Совы»: Шаг «")
    assert "шаг формально готов, но промты пустые у кадров 1, 2 (2 из 3)" in state.error
    assert ("У этих кадров нет данных сцены: сначала нужна нода «Работа с GPT» «Сцены» (n_gpt2)"
            in state.error)
    # Единый детектор: Студия тоже считает эти заглушки пустыми, повтор шага их перепишет.
    assert "не перепишет" not in state.error and "Сотрите" not in state.error
    assert "Потом запустите промты картинок заново" in state.error


async def test_hollow_prompts_studio_redoes_need_no_erasing(env, studio) -> None:
    pid = await _seed(env, [{"number": 1, "uuid": "a1", "image_prompt": HOLLOW_EXACT},
                            {"number": 2, "uuid": "a2", "image_prompt": HOLLOW_ABSENT,
                             "attrs": {"place": "лес"}}])
    state = await bl._poll_any_step(_Ctx(pid, status="image_prompts_ready"),
                                    {"project_id": pid, "step_code": "img_pr"}, _started())
    assert "промты пустые у кадров 1, 2 (2 из 2)" in state.error
    # Студия теперь считает пустыми те же промты, что и проверка (единый детектор
    # prompt_is_hollow), поэтому повторный запуск перепишет и кадр 2 — оговорки нет.
    assert "не перепишет" not in state.error and "нет данных сцены" not in state.error
    only_exact = await _seed(env, [{"number": 1, "uuid": "b1", "image_prompt": HOLLOW_EXACT}])
    state = await bl._poll_any_step(_Ctx(only_exact, status="image_prompts_ready"),
                                    {"project_id": only_exact, "step_code": "img_pr"}, _started())
    assert "не перепишет" not in state.error and "Сотрите" not in state.error
    assert "Потом запустите промты картинок заново" in state.error


async def test_full_prompts_after_img_pr_are_success(env, studio) -> None:
    pid = await _seed(env, [{"number": 1, "uuid": "a1", "image_prompt": REAL},
                            {"number": 2, "uuid": "a2", "image_prompt": PARTIAL}])
    state = await bl._poll_any_step(_Ctx(pid, status="image_prompts_ready"),
                                    {"project_id": pid, "step_code": "img_pr"}, _started())
    assert state.ok and "завершён" in state.result["text_ru"]


async def test_auto_chain_through_img_pr_reports_hollow_prompts(env, studio) -> None:
    pid = await _seed(env, [{"number": 1, "uuid": "a1", "image_prompt": HOLLOW_SHORT}])
    started = {**_started(), "step_code": "hero", "running_status": "generating_hero",
               "status_before": "enrich_1_ready"}
    params = {"project_id": pid, "step_code": "hero", "allow_auto_chain": True}
    state = await bl._poll_any_step(_Ctx(pid, status="images_ready"), params, started)
    assert not state.ok and "промты пустые у кадров 1 (1 из 1)" in state.error
    # Цепочка не дошла до промтов картинок - старые промты не её итог.
    early = await bl._poll_any_step(_Ctx(pid, status="hero_ready"), params, started)
    assert early.ok
