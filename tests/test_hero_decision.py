"""Режим героя: решается один раз до плана, промты получают готовое указание.

Раньше при `hero_mode=auto` промт плана содержал «hero_needed — как решать»,
промт закадра — «РЕЖИМ ОПРЕДЕЛИ САМ», и модель решала сама на каждом шаге.
Теперь `hero_decision.ensure_hero_mode` пишет режим в проект, а сборка промтов
(`chatgpt_xlsx`) вырезает разделы про выбор и дописывает прямое указание.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.orchestrator.steps.make_plan as mp
from app.models import Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import hero_decision as hd

# ── разбор ответа ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reply", "mode", "reason"),
    [
        ("РЕЖИМ: hero\nПОЧЕМУ: есть Наполеон", "hero", "есть Наполеон"),
        ("РЕЖИМ: no_hero\nПОЧЕМУ: про устройство ГЭС", "no_hero", "про устройство ГЭС"),
        ("**Режим:** `no_hero`\n**Почему:** люди взаимозаменяемы", "no_hero", "люди взаимозаменяемы"),
        ("Вот мой ответ.\nРЕЖИМ — hero\nПОЧЕМУ — за котом следят", "hero", "за котом следят"),
        ("hero_needed=false, потому что явление", "no_hero", ""),
        ("hero_needed: true", "hero", ""),
        ("Думаю, no_hero.", "no_hero", ""),
        ("Ничего не понял", None, ""),
        ("", None, ""),
    ],
)
def test_parse_reply(reply: str, mode: str | None, reason: str) -> None:
    assert hd.parse_reply(reply) == (mode, reason)


def test_build_prompt_substitutes_topic_or_appends() -> None:
    assert "Тема ролика: Вулканы" in hd.build_prompt("Вулканы")
    custom = "Свой промт без метки {и с} скобками"
    out = hd.build_prompt("Вулканы", custom)
    assert out.startswith(custom)
    assert out.endswith("Тема ролика: Вулканы")


# ── решение и запись в проект ────────────────────────────────────────────


class _Gpt:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[str] = []

    async def ask_fresh(self, text: str, *, project_id: int | None = None, **kw: object) -> str:
        self.calls.append(text)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _project(hero_mode: str) -> Project:
    p = Project(slug="t", topic="Кот, который переплыл Волгу", hero_mode=hero_mode)
    p.id = 7
    return p


@pytest.mark.asyncio
async def test_auto_is_decided_and_recorded() -> None:
    p = _project("auto")
    gpt = _Gpt("РЕЖИМ: hero\nПОЧЕМУ: индивидуальный кот с действием")
    assert await hd.ensure_hero_mode(p, gpt) == "hero"
    assert p.hero_mode == "hero"
    d = p.meta["hero_decision"]
    assert d["mode"] == "hero"
    assert d["source"] == "model"
    assert d["reason"] == "индивидуальный кот с действием"
    assert "Кот, который переплыл Волгу" in gpt.calls[0]


@pytest.mark.asyncio
async def test_explicit_mode_is_not_touched() -> None:
    p = _project("no_hero")
    gpt = _Gpt("РЕЖИМ: hero")
    assert await hd.ensure_hero_mode(p, gpt) == "no_hero"
    assert gpt.calls == []
    assert "hero_decision" not in (p.meta or {})


@pytest.mark.asyncio
async def test_model_failure_falls_back_to_no_hero() -> None:
    p = _project("auto")
    assert await hd.ensure_hero_mode(p, _Gpt(RuntimeError("relay down"))) == "no_hero"
    assert p.meta["hero_decision"]["source"] == "fallback"

    p2 = _project("auto")
    assert await hd.ensure_hero_mode(p2, _Gpt("бла-бла")) == "no_hero"
    assert p2.meta["hero_decision"]["source"] == "fallback"


# ── сборка промтов ───────────────────────────────────────────────────────

PLAN_MASTER = """\
# ШАГ 1

---

## РОЛЬ

Ты сценарист.

---

## hero_needed — как решать

`true` — если герой есть. Реши сам.

---

## РИТМ

Коротко.
"""

SCRIPT_MASTER = """\
# ШАГ 2

## РОЛЬ

Пиши речь.

---

## РЕЖИМ A — ПЕРСОНАЖНЫЙ СЦЕНАРИЙ

Герой в первых двух предложениях.

---

## РЕЖИМ B — ТЕМАТИЧЕСКИЙ СЦЕНАРИЙ

Начинай с факта.

---

## РЕЖИМ ОПРЕДЕЛИ САМ

Смотри в план.

---

## ФОРМАТ ОТВЕТА

Только текст.
"""


@pytest.mark.parametrize("mode", ["hero", "no_hero"])
def test_plan_sections_drop_how_to_decide(mode: str) -> None:
    out = cx.select_plan_sections(PLAN_MASTER, mode)
    assert "как решать" not in out
    assert "Реши сам" not in out
    assert "## РОЛЬ" in out and "## РИТМ" in out
    assert "---\n\n---" not in out


def test_plan_sections_kept_in_auto() -> None:
    assert cx.select_plan_sections(PLAN_MASTER, "auto") == PLAN_MASTER


def test_script_sections_keep_only_chosen_mode() -> None:
    a = cx.select_script_sections(SCRIPT_MASTER, "hero")
    assert "РЕЖИМ A" in a and "РЕЖИМ B" not in a and "ОПРЕДЕЛИ САМ" not in a
    assert "## РОЛЬ" in a and "## ФОРМАТ ОТВЕТА" in a
    b = cx.select_script_sections(SCRIPT_MASTER, "no_hero")
    assert "РЕЖИМ B" in b and "РЕЖИМ A" not in b and "ОПРЕДЕЛИ САМ" not in b
    assert cx.select_script_sections(SCRIPT_MASTER, "auto") == SCRIPT_MASTER


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(slug="test-proj", topic="Тема теста", hero_mode="hero")
    p.id = 1
    return p


def test_plan_prompt_file_gets_directive_not_choice(project: Project) -> None:
    tmp_dir = cx.tmp_gpt_dir(project)
    with patch("app.services.chatgpt_xlsx.get_project_prompt", return_value=PLAN_MASTER):
        content = cx.write_plan_prompt_file(project, tmp_dir).read_text(encoding="utf-8")
    assert "УКАЗАНИЕ ПРОЕКТА" in content
    assert "hero_needed=true" in content
    assert "как решать" not in content

    project.hero_mode = "no_hero"
    with patch("app.services.chatgpt_xlsx.get_project_prompt", return_value=PLAN_MASTER):
        content = cx.write_plan_prompt_file(project, tmp_dir).read_text(encoding="utf-8")
    assert "hero_needed=false" in content
    assert "hero_needed=true" not in content


def test_plan_prompt_file_auto_embeds_rules_from_code(project: Project) -> None:
    """Легаси-путь без решения: правила выбора приходят из кода, шаг не ломается."""
    project.hero_mode = "auto"
    tmp_dir = cx.tmp_gpt_dir(project)
    with patch("app.services.chatgpt_xlsx.get_project_prompt", return_value="MASTER"):
        content = cx.write_plan_prompt_file(project, tmp_dir).read_text(encoding="utf-8")
    assert "Реши сам, нужен ли ролику сквозной персонаж" in content


def test_script_prompt_file_single_mode(project: Project) -> None:
    tmp_dir = cx.tmp_gpt_dir(project)
    with patch("app.services.chatgpt_xlsx.get_project_prompt", return_value=SCRIPT_MASTER):
        content = cx.write_script_prompt_file(project, tmp_dir).read_text(encoding="utf-8")
    assert "РЕЖИМ: A (герой)" in content
    assert "## РЕЖИМ A" in content
    assert "## РЕЖИМ B" not in content
    assert "ОПРЕДЕЛИ САМ" not in content


# ── шаг плана зовёт решение до сборки промта ─────────────────────────────


def test_template_comes_from_library_when_it_is_there() -> None:
    """Папка `01a_hero_decision/` есть — её текст замещает встроенный промт."""
    with patch("app.services.prompt_library.read_prompt", return_value="  СВОЙ ПРОМТ  "):
        assert mp._hero_decision_template() == "СВОЙ ПРОМТ"


@pytest.mark.parametrize("reply", ["", "   ", RuntimeError("нет такой папки")])
def test_template_absent_falls_back_to_builtin(reply: str | Exception) -> None:
    """Ни файла, ни записи в базе — None, и `hero_decision` возьмёт свой.

    На проде это основной путь: `prompts/` в образ намеренно не кладётся.
    """
    kw = {"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply}
    with patch("app.services.prompt_library.read_prompt", **kw):
        assert mp._hero_decision_template() is None


class _FlushingSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.asyncio
async def test_make_plan_decides_hero_before_building_the_plan() -> None:
    """Решение принимается ДО `run_plan_xlsx` — иначе промт соберётся с «реши сам».

    Шаг обрывается на пустом плане: до этого места важен только порядок.
    """
    project = _project("auto")
    project.status = ProjectStatus.planning
    session = _FlushingSession()
    order: list[str] = []

    async def _decide(p: Project, gpt: object, *, template: str | None = None) -> str:
        order.append("decide")
        p.hero_mode = "hero"
        return "hero"

    async def _plan(p: Project) -> object:
        order.append("plan")
        assert p.hero_mode == "hero", "план собирается уже с решённым режимом"
        return SimpleNamespace(plan_text="")

    with (
        patch("app.services.hero_decision.ensure_hero_mode", _decide),
        patch("app.services.gpt_client.get_gpt_client", lambda: object()),
        patch.object(mp.xsr, "run_plan_xlsx", _plan),
    ):
        with pytest.raises(RuntimeError, match="пустой общий_план"):
            await mp.run(session, project, bot=None)  # type: ignore[arg-type]

    assert order == ["decide", "plan"]
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_make_plan_skips_when_status_is_not_planning() -> None:
    project = _project("auto")
    project.status = ProjectStatus.plan_ready
    with patch("app.services.hero_decision.ensure_hero_mode") as decide:
        await mp.run(_FlushingSession(), project, bot=None)  # type: ignore[arg-type]
    decide.assert_not_called()


def test_drop_sections_without_headings_returns_text_as_is() -> None:
    """Мастер-промт из базы может не иметь разделов `## …` — вырезать нечего."""
    text = "Сплошной текст без заголовков.\nВторая строка."
    assert cx.drop_sections(text, lambda _h: True) == text
