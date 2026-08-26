"""Разбор состава: персонажи и предметы из плана.

Шаг «Герои и предметы» ждал готовых описаний. У владельца их даёт лист
«Персонажи» в книге проекта; при учётных записях книги нет, и заполнить их
некому — шаг молча пропускался, конвейер вставал. Живой прогон 2026-08-25
остановился ровно здесь: в плане прямым текстом стояло «четверо помятых жизнью
агентов», а `hero_descriptions` оставался пуст.

Разбор нарочно снисходительный. Схему enforce'ят только релеи из
`GPT_STRUCTURED_RELAYS`, поэтому опора только на формат в
промте — а модель ставит заголовки то решёткой, то жирным, нумерует списки
по-разному и любит дописать вступление. Требовать точности значит ронять шаг на
пустом месте, тогда как цена ошибки мала: строку человек поправит в редакторе.
"""

from __future__ import annotations

import pytest

from app.services import cast_extract
from app.services.cast_extract import MAX_PEOPLE, MAX_THINGS, build_prompt, parse_reply


def test_parses_the_shape_a_model_actually_returns() -> None:
    """Живой ответ: вступление, жирные заголовки, смешанная нумерация."""
    reply = """Вот что получилось.

**ПЕРСОНАЖИ**
- Крот-один — командир, вязаная шапка вместо балаклавы
- Боец-2 — щуплый, фингал под глазом
1. Боец-3 — грузный, ссадина на скуле

## ПРЕДМЕТЫ
* Карта на кассовом чеке — мятая бумажка
- Банка кротовухи — мутная жидкость
"""
    people, things = parse_reply(reply)

    assert len(people) == 3
    assert people[0].startswith("Крот-один")
    assert len(things) == 2
    assert "чеке" in things[0]


def test_headers_survive_declension() -> None:
    """«Персонажи», «Герои», «Действующие лица» — всё это один раздел.

    Заголовок модель склоняет и переименовывает; привязка к точной строке
    означала бы пустой результат при полностью годном ответе.
    """
    for header in ("ГЕРОИ", "Персонажи:", "### Действующие лица", "**герой**"):
        people, _ = parse_reply(f"{header}\n- кто-то — описание\n")
        assert people == ["кто-то — описание"], f"заголовок {header!r} не распознан"


def test_empty_and_shapeless_replies_are_not_errors() -> None:
    """Пустые списки — штатный исход, а не повод падать."""
    assert parse_reply("") == ([], [])
    assert parse_reply("Извините, не могу помочь.") == ([], [])
    # Список без заголовка непонятно куда класть — молча пропускаем.
    assert parse_reply("- строка без раздела") == ([], [])


def test_limits_are_enforced() -> None:
    """Ограничение не косметическое: каждый персонаж — деньги за референс."""
    many = "ПЕРСОНАЖИ\n" + "".join(f"- герой {i} — описание\n" for i in range(20))
    many += "ПРЕДМЕТЫ\n" + "".join(f"- вещь {i} — описание\n" for i in range(20))
    people, things = parse_reply(many)

    assert len(people) == MAX_PEOPLE
    assert len(things) == MAX_THINGS


def test_prompt_carries_the_material() -> None:
    """В запрос попадают и план, и закадровый текст."""
    prompt = build_prompt("план про рачков", "закадровый текст")
    assert "план про рачков" in prompt
    assert "закадровый текст" in prompt


def test_user_template_with_braces_does_not_crash() -> None:
    """Промт пользователя может содержать фигурные скобки.

    Подстановка идёт заменой известных меток, а не `str.format`: иначе любая
    скобка в тексте промта роняла бы шаг на KeyError — причём только у того,
    кто её написал, то есть при правке промта из интерфейса.
    """
    template = 'Верни JSON вида {"люди": []}. Материал: {plan}'
    prompt = build_prompt("мой план", "мой текст", template)

    assert "мой план" in prompt
    assert '{"люди": []}' in prompt


def test_template_without_placeholders_still_gets_the_material() -> None:
    """Промт без меток — материал дописывается в конец.

    Иначе модель получила бы одни инструкции и отвечала бы наугад, а человек
    видел бы пустой список, не понимая почему.
    """
    prompt = build_prompt("мой план", "мой текст", "Просто выпиши персонажей.")

    assert "мой план" in prompt
    assert "мой текст" in prompt


@pytest.mark.asyncio
async def test_model_failure_does_not_break_the_step() -> None:
    """Модель упала — возвращаем пустые списки, а не исключение.

    Вывод состава — удобство поверх шага, а не его обязанность. Падение здесь
    остановило бы конвейер там, где раньше он спокойно шёл дальше.
    """
    from types import SimpleNamespace

    class Boom:
        async def ask_fresh(self, *a, **kw):
            raise RuntimeError("сеть отвалилась")

    project = SimpleNamespace(id=1, general_plan="план", script_text="текст")
    assert await cast_extract.extract_cast(project, Boom()) == ([], [])


@pytest.mark.asyncio
async def test_nothing_to_work_from_skips_the_call() -> None:
    """Без плана и текста модель не дёргается — платить не за что."""
    from types import SimpleNamespace

    called = False

    class Spy:
        async def ask_fresh(self, *a, **kw):
            nonlocal called
            called = True
            return ""

    project = SimpleNamespace(id=1, general_plan="", script_text="")
    assert await cast_extract.extract_cast(project, Spy()) == ([], [])
    assert called is False, "запрос к модели без материала — плата ни за что"


@pytest.mark.asyncio
async def test_happy_path_returns_both_lists() -> None:
    from types import SimpleNamespace

    class Fake:
        async def ask_fresh(self, prompt, **kw):
            assert "план" in prompt
            return "ПЕРСОНАЖИ\n- герой — описание\nПРЕДМЕТЫ\n- вещь — описание\n"

    project = SimpleNamespace(id=1, general_plan="план", script_text="")
    people, things = await cast_extract.extract_cast(project, Fake())

    assert people == ["герой — описание"]
    assert things == ["вещь — описание"]


def test_prompt_forbids_grouping_characters() -> None:
    """Промт обязан требовать «одна строка — один персонаж».

    Найдено живой пробой на настоящем плане 2026-08-26: без этого правила
    MiniMax свернул четверых в строку «Группа из четырёх «агентов»». Одна
    строка — один референс, то есть четверо получили бы общий портрет вместо
    четырёх лиц, и дальше по конвейеру это уже не разделить.

    Проверка смотрит на текст промта, а не на ответ модели: ответ зависит от
    погоды, а правило либо есть, либо его случайно вычистили при правке.
    """
    prompt = build_prompt("план", "текст")
    low = prompt.lower()
    assert "одна строка" in low and "один персонаж" in low, (
        "из промта пропало правило про разбивку групп — модель снова начнёт "
        "склеивать нескольких героев в одну строку"
    )
