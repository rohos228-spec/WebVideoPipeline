"""Живая проверка port/today (m1 → m2): карточка смены модели, «продолжи» без кадров в базе,
цитата промта в ответе."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from app.orchestrator.agents import summarizer as sm
from app.services.action_layer import img_pr_guard as ipg
from app.services.action_layer import project_gen_settings as pgs
from app.services.action_layer import settings_diff as sd

# --- модель, которой нет в каталоге этой линии -------------------------------------------


def test_unknown_image_model_is_named_not_shown_raw() -> None:
    assert sd.value_ru("image_generator", "gpt_image_2_vip") == "GPT Image 2 VIP (нет в каталоге)"
    assert sd.value_ru("video_generator", "old-video-x2") == "Old Video X2 (нет в каталоге)"
    # Известная модель называется по каталогу, как раньше.
    assert sd.value_ru("image_generator", "gpt_image_2") == "GPT Image 2"


def test_settings_diff_card_text_has_no_raw_id() -> None:
    items = sd.diff({"image_generator": "gpt_image_2_vip"}, {"project_id": 5, "image_generator": "gpt_image_2"})
    text = sd.change_text(items)
    assert text == "модель картинок: GPT Image 2 VIP (нет в каталоге) → GPT Image 2"
    assert "gpt_image_2_vip" not in text
    # Правка остаётся правкой: новая модель из каталога, старая - нет.
    assert items[0]["after"] == "gpt_image_2" and not items[0]["same"]


def test_media_change_label_for_unknown_model() -> None:
    current = {"image_generator": "gpt_image_2_vip", "image_resolution": "2k"}
    change = pgs.media_changes(current, {"image_generator": "gpt_image_2"})[0]
    assert change["before_label"] == "GPT Image 2 VIP (нет в каталоге)"
    assert "gpt_image_2_vip" not in pgs.media_change_text(change)


_NODES = [
    {"id": "n_hero", "type": "hero", "data": {"label": "Персонажи"}},
    {"id": "n_items", "type": "items", "data": {"label": "Предметы"}},
    {"id": "n_images", "type": "images", "data": {"label": "Картинки"}},
]


def test_node_overrides_gpt_image_2_matches_its_node() -> None:
    """На этой линии gpt-image-2 нода = gpt_image_2 проекта: VIP-id сюда не переносится."""
    nodes = copy.deepcopy(_NODES)
    for node in nodes:
        node["data"]["modelId"] = "gpt-image-2"
    meta = {"canvas_graph": {"workflow_id": 1, "nodes": nodes, "edges": []}}
    assert pgs.node_overrides(meta, {"image_generator": "gpt_image_2"}) == []


# --- «продолжи»: кадров в базе нет, а project.xlsx есть ----------------------------------


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.rolled_back = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def scalars(self, _query: Any) -> _Scalars:
        return _Scalars(list(self.rows))

    async def get(self, _model: Any, pid: int) -> Any:
        return SimpleNamespace(id=pid, data_dir=_DIR)

    async def rollback(self) -> None:
        self.rolled_back += 1
        self.rows.clear()


class _Dir:
    def __truediv__(self, name: str) -> Any:
        return SimpleNamespace(
            is_file=lambda: True, name=name, stat=lambda: SimpleNamespace(st_mtime_ns=1, st_size=1)
        )


_DIR = _Dir()


@pytest.fixture(autouse=True)
def _fresh_preview_cache() -> Any:
    ipg.clear_preview_cache()
    yield
    ipg.clear_preview_cache()


def _frame(number: int, **kw: Any) -> SimpleNamespace:
    base = {"number": number, "uuid": f"u{number}", "voiceover_text": f"закадр {number}", "meaning": None,
            "image_prompt": None, "attrs": {}}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_live_frames_fall_back_to_xlsx_preview_and_roll_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.action_layer import builtin_insight as bi

    sessions: list[_Session] = []

    def factory() -> _Session:
        sessions.append(_Session([]))
        return sessions[-1]

    monkeypatch.setattr(bi, "_sessions", lambda: factory)

    async def fake_import(session: _Session, project: Any, xlsx: Any) -> bool:
        session.rows.extend([_frame(2), _frame(1)])
        return True

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", fake_import)
    frames = await ipg.load_live_frames(10)
    assert [f.number for f in frames] == [1, 2]
    # Первая сессия - чтение базы (кадров нет), вторая - импорт xlsx, всегда откатанный.
    assert len(sessions) == 2 and sessions[1].rolled_back == 1 and sessions[1].rows == []
    empty, total = ipg.frames_without_scene(frames)
    assert empty == [1, 2] and total == 2


@pytest.mark.asyncio
async def test_live_frames_from_db_skip_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.action_layer import builtin_insight as bi

    monkeypatch.setattr(bi, "_sessions", lambda: (lambda: _Session([_frame(1, meaning="лес")])))

    async def boom(*_a: Any) -> bool:
        raise AssertionError("xlsx не нужен, кадры есть в базе")

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", boom)
    frames = await ipg.load_live_frames(10)
    assert [f.number for f in frames] == [1]


@pytest.mark.asyncio
async def test_failed_xlsx_preview_means_no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.action_layer import builtin_insight as bi

    monkeypatch.setattr(bi, "_sessions", lambda: (lambda: _Session([])))

    async def broken(*_a: Any) -> bool:
        raise ValueError("битый xlsx")

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", broken)
    assert await ipg.load_live_frames(10) == []
    assert await ipg.scene_gap(10, {}, []) is None


# --- «покажи промт»: текст цитатой, а не пересказ ---------------------------------------

_BODY = "# Шаг 4 «ИИ-редактор сцен»\n\nТы — главный режиссёр-постановщик сцен.\nОпиши план, ракурс, свет."
_NOTE_TEXT = (
    "Нода «Работа с GPT» (Работа с GPT).\nПромт: «cinematic_scene_director» (общий для всех проектов).\n"
    f"Текст промта «cinematic_scene_director» (системный):\n{_BODY}\n(показаны первые 90 из 5647 знаков)"
)


def _facts() -> sm.TaskFacts:
    note = sm.StepNote("node.describe", "Показать настройки ноды", ok=True, text=_NOTE_TEXT)
    return sm.TaskFacts(goal="покажи какой промт стоит у первой работы с гпт в проекте 7", status="done",
                        steps=[note])


def test_prompt_excerpt_is_parsed() -> None:
    (item,) = sm.prompt_excerpts(_facts())
    assert item.head == "Текст промта «cinematic_scene_director» (системный)"
    assert item.body == _BODY
    assert item.tail == "(показаны первые 90 из 5647 знаков)"


def test_summary_prompt_asks_for_a_quote() -> None:
    system = sm._facts_prompt(_facts())[0]["content"]
    assert sm.PROMPT_QUOTE_RULE in system and _BODY in system


def test_paraphrase_gets_the_quote_appended() -> None:
    told = "В проекте #7 стоит промт «cinematic_scene_director». Полный текст содержал 5 647 знаков."
    out = sm.ensure_prompt_quoted(told, _facts())
    assert out.startswith(told)
    assert "> Ты — главный режиссёр-постановщик сцен." in out
    assert "(показаны первые 90 из 5647 знаков)" in out


def test_quoted_answer_is_left_alone() -> None:
    told = "Промт:\n> # Шаг 4 «ИИ-редактор сцен»\n>\n> Ты — главный режиссёр-постановщик сцен.\n> Опиши план, ракурс, свет."
    assert sm.ensure_prompt_quoted(told, _facts()) == told


@pytest.mark.asyncio
async def test_summarize_appends_quote_to_model_paraphrase() -> None:
    class Router:
        async def call(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(text="Стоял общий промт «cinematic_scene_director».", approval=None)

    note = sm.StepNote("node.describe", "Показать настройки ноды", ok=True, text=_NOTE_TEXT, readonly=True)
    facts = sm.TaskFacts(goal="покажи промт", status="done", steps=[note])
    text = await sm.TaskSummarizer(Router()).summarize(facts, run_id="r1", task_id=1)
    assert "> Ты — главный режиссёр-постановщик сцен." in text
