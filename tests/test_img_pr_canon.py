"""Канон внешности и фона: то же лечение, что получила расстановка.

Главный герой консистентен фотографией — провайдер берёт её как
`subject_reference`. Второму фотографии не достанется: `image-01` принимает
РОВНО ОДНУ character-ссылку. Значит его сходство держит текст, а текст модель
до сих пор сочиняла заново в каждом батче — отсюда дрейф: c02 из первых
кадров и c02 из последних описаны разными словами.

Фон — та же болезнь. `shot01_bg` канон сцены, мастер-промт велит «фон
подробно из shot01_bg, священно», и это требование к модели, проверять
которое было некому.

Здесь проверяется, что теперь оба держатся кодом.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Entity, Frame, FrameStatus, Project, ProjectStatus
from app.services.img_pr_canon import (
    appearance_line,
    canon_lines_present,
    enforce_canon_in_prompts,
    ensure_appearance_lines,
    ensure_background_line,
)


@dataclass
class _Card:
    id: str
    look: str = ""
    clothes: str = ""


def test_appearance_canon_is_built_from_the_passport() -> None:
    """Канон — это внешность и одежда, и только они.

    Характер и правила героя описывают поведение; генератору картинки нужно,
    как он выглядит, а бюджет промта у MiniMax 1500 символов на всё.
    """
    line = appearance_line(_Card("c02", look="женщина 35 лет, тёмные волосы", clothes="серое пальто"))
    assert line == "Внешность c02: женщина 35 лет, тёмные волосы, серое пальто"


def test_empty_passport_gives_no_canon() -> None:
    """Пустой паспорт — вставлять нечего, и выдумывать код не должен.

    Досочинение это когда код выдумывает то, чего в данных нет. Здесь честнее
    ничего не вставить и сказать об этом в журнал.
    """
    assert appearance_line(_Card("c03")) == ""
    assert appearance_line(_Card("", look="кто-то")) == ""


def test_canon_is_cut_on_a_word_boundary() -> None:
    """Обрубленное на полуслове описание модель достраивает сама — каждый раз иначе."""
    long_look = "мужчина " + "очень " * 60 + "высокий"
    line = appearance_line(_Card("c01", look=long_look), budget=60)
    assert len(line) <= 60 + len("Внешность c01: ")
    assert not line.endswith("оче")


def test_the_same_card_always_gives_the_same_line() -> None:
    """Главное свойство канона: он не меняется между вызовами.

    Именно этим он и лечит дрейф — c02 описан одними и теми же словами в
    кадре 1 и в кадре 24, потому что слова взяты из паспорта, а не сочинены
    моделью в очередном батче.
    """
    card = _Card("c02", look="женщина 35 лет", clothes="серое пальто")
    assert appearance_line(card) == appearance_line(card)


def test_background_line_is_replaced_not_appended() -> None:
    """Две строки фона хуже одной неверной.

    Модель складывает их в третий фон, которого нет нигде, — и кадр уезжает
    дальше от сцены, чем был до починки.
    """
    prompt = "Фон: размытый двор\nДействие: идёт к двери"
    fixed = ensure_background_line(prompt, "вагон метро, жёлтый свет")
    assert fixed.count("Фон:") == 1
    assert "вагон метро" in fixed
    assert "размытый двор" not in fixed


def test_background_is_added_before_the_story_when_missing() -> None:
    """Порядок важен: у MiniMax хвост промта уходит первым."""
    prompt = "Действие: идёт к двери\nЭмоция: тревога"
    fixed = ensure_background_line(prompt, "вагон метро").splitlines()
    assert fixed[0] == "Фон: вагон метро"


def test_empty_canon_changes_nothing() -> None:
    """Нет канона — нет и правки: пустая строка фона хуже отсутствия строки."""
    prompt = "Фон: что-то своё\nДействие: идёт"
    assert ensure_background_line(prompt, "") == prompt
    assert ensure_background_line(prompt, None) == prompt


def test_appearance_goes_right_after_the_background() -> None:
    """Личность важнее эмоции: кадр с неверным лицом переснимают, с бледной — нет."""
    prompt = "Фон: вагон метро\nДействие: входит\nЭмоция: тревога"
    fixed = ensure_appearance_lines(prompt, {"c02": "Внешность c02: женщина в сером"})
    lines = fixed.splitlines()
    assert lines[0].startswith("Фон:")
    assert lines[1] == "Внешность c02: женщина в сером"


def test_existing_canon_is_left_alone(monkeypatch) -> None:
    """Повторный прогон не двигает текст.

    Сдвинутый текст означает «промт изменился», а изменившийся промт — это
    перерисовка кадра за деньги клиента.
    """
    prompt = "Фон: вагон\nВнешность c02: женщина в сером\nДействие: входит"
    assert ensure_appearance_lines(prompt, {"c02": "Внешность c02: другое"}) == prompt
    assert canon_lines_present(prompt) == {"c02"}


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'canon.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_second_character_gets_the_same_words_in_every_frame(db) -> None:
    """Сквозная проверка: во всех кадрах c02 описан одинаково.

    Это и есть «сделать второго персонажа консистентным». Фотография ему не
    достанется — провайдер берёт одну, — но слова у него теперь везде одни и
    те же, а не заново сочинённые в каждом батче.
    """
    async with db() as s:
        p = Project(slug="canon", topic="канон", status=ProjectStatus.image_prompts_ready)
        s.add(p)
        await s.flush()
        s.add_all(
            [
                Entity(
                    project_id=p.id,
                    type="character",
                    code="c01",
                    name="Он",
                    attrs={"look": "мужчина 40 лет, борода", "clothes": "чёрная куртка"},
                ),
                Entity(
                    project_id=p.id,
                    type="character",
                    code="c02",
                    name="Она",
                    attrs={"look": "женщина 35 лет, тёмные волосы", "clothes": "серое пальто"},
                ),
            ]
        )
        # Три кадра одной сцены: агент описал c02 каждый раз по-своему.
        for n, prompt in enumerate(
            [
                "Фон: свой фон 1\nДействие: входят",
                "Фон: свой фон 2\nДействие: говорят",
                "Фон: свой фон 3\nДействие: уходят",
            ],
            start=1,
        ):
            s.add(
                Frame(
                    project_id=p.id,
                    number=n,
                    voiceover_text="текст",
                    image_prompt=prompt,
                    status=FrameStatus.image_prompt_ready,
                    attrs={"characters": "c01, c02", "shot01_bg": "вагон метро, жёлтый свет"},
                )
            )
        await s.commit()
        project_id = p.id

    async with db() as s:
        project = await s.get(Project, project_id)
        stats = await enforce_canon_in_prompts(s, project)
        await s.commit()

    assert stats["appearance"] == 3
    assert stats["background"] == 3

    async with db() as s:
        from sqlalchemy import select

        prompts = (await s.execute(select(Frame.image_prompt).order_by(Frame.number))).scalars().all()

    canon_c02 = "Внешность c02: женщина 35 лет, тёмные волосы, серое пальто"
    canon_c01 = "Внешность c01: мужчина 40 лет, борода, чёрная куртка"
    for prompt in prompts:
        assert canon_c02 in prompt, "второй герой описан не каноном"
        assert canon_c01 in prompt, "первому тоже нужна строка: иначе он уедет за описанием второго"
        assert "Фон: вагон метро, жёлтый свет" in prompt
        assert "свой фон" not in prompt
    assert len({p for p in prompts}) == 3, "кадры не должны схлопнуться в один"


async def test_single_character_frame_needs_no_words(db) -> None:
    """Один человек в кадре — фотография справляется, бюджет тратить не на что."""
    async with db() as s:
        p = Project(slug="solo", topic="один", status=ProjectStatus.image_prompts_ready)
        s.add(p)
        await s.flush()
        s.add(
            Entity(
                project_id=p.id,
                type="character",
                code="c01",
                name="Он",
                attrs={"look": "мужчина", "clothes": "куртка"},
            )
        )
        s.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="т",
                image_prompt="Фон: вагон\nДействие: стоит",
                status=FrameStatus.image_prompt_ready,
                attrs={"characters": "c01", "shot01_bg": "вагон"},
            )
        )
        await s.commit()
        project_id = p.id

    async with db() as s:
        project = await s.get(Project, project_id)
        stats = await enforce_canon_in_prompts(s, project)
    assert stats["appearance"] == 0


async def test_empty_passport_is_reported_not_invented(db) -> None:
    """Героя без паспорта код не выдумывает, но и не замалчивает."""
    async with db() as s:
        p = Project(slug="nopass", topic="без паспорта", status=ProjectStatus.image_prompts_ready)
        s.add(p)
        await s.flush()
        s.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="т",
                image_prompt="Фон: вагон\nДействие: стоят",
                status=FrameStatus.image_prompt_ready,
                attrs={"characters": "c01, c02"},
            )
        )
        await s.commit()
        project_id = p.id

    async with db() as s:
        project = await s.get(Project, project_id)
        stats = await enforce_canon_in_prompts(s, project)
    assert stats["no_card"] == 2
    assert stats["appearance"] == 0
