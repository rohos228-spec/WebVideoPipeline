"""Строковые константы кода помещаются в объявленные колонки.

**Зачем это отдельный тест.** SQLite не проверяет длину `VARCHAR` вообще:
строка в двадцать один символ спокойно ложится в `String(20)`. Postgres
проверяет и отказывает:

    asyncpg.exceptions.StringDataRightTruncationError:
    value too long for type character varying(20)

Получается класс ошибок, который зелен на всей суите и падает только в бою.
Ровно так и вышло 2026-08-25: `step_billing` писал в
`credit_entries.ref_table` строку `llm_calls+media_calls` — 21 символ при
объявленных двадцати. На каждом первом шаге нового проекта проводка
бесплатного уровня не вставлялась, шаг падал, и через три попытки проект
уходил в паузу на полчаса. Снаружи: «нажал «Сгенерировать» — ничего не
произошло».

**Что делает проверка.** Обходит исходники, находит присваивания строковых
литералов именованным аргументам (`ref_table="…"`) и сверяет длину с колонкой
того же имени.

**Только ОДНОЗНАЧНЫЕ имена.** Проверяются лишь те имена, которые встречаются
ровно в одной таблице (47 из 62). Причина не в аккуратности, а в том, что иначе
проверка врёт: `kind` объявлен в восьми таблицах шириной от 12 до 40, и по
имени аргумента невозможно понять, в какую из них он идёт. Первая редакция
брала самую узкую и выдала четырнадцать ложных срабатываний подряд —
`_log_chatgpt_error(kind="upload_timeout")`, `update_sidecar(status="processing")`
и прочие обычные функции, к базе отношения не имеющие. Гейт, который так
шумит, отключают в первый же день.

Соответственно, проверка НЕ ловит всё: значение через переменную, через `**kwargs`
или в колонку с неоднозначным именем пройдёт мимо. Это дешёвый предохранитель
против конкретной ошибки, а не доказательство корректности.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import String

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

#: Имена, которые совпадают с колонкой, но как аргумент почти всегда значат
#: другое — свободный текст, путь, сообщение. Отсев дополняет правило
#: однозначности: `memo` объявлен один раз, но в вызовах это произвольная
#: строка.
IGNORED = frozenset(
    {
        "text",  # везде: тексты промтов, ответы моделей
        "content",
        "message",
        "prompt",
        "value",
        "path",  # пути длиннее любой колонки и живут в Text
        "url",
        "description",
        "reason",
        "detail",
        "error",
        "note",
        "memo",  # свободная строка проводки, в модели Text
        "label",
        "title",
        "topic",
        "name",  # слишком общее: имя чего угодно
        "key",
        "data",
        "meta",
    }
)


def _string_columns() -> dict[str, int]:
    """{имя колонки: длина} — только для имён, встречающихся в ОДНОЙ таблице.

    Имя из нескольких таблиц (`kind`, `status`) по аргументу вызова к колонке
    не привязать, и любая догадка тут даёт ложные срабатывания, а не находки.
    """
    from collections import defaultdict

    import app.models as models

    seen: dict[str, list[int]] = defaultdict(list)
    for mapper in models.Base.registry.mappers:
        for col in mapper.local_table.columns:
            length = getattr(col.type, "length", None)
            if isinstance(col.type, String) and length:
                seen[col.name].append(length)
    return {name: lengths[0] for name, lengths in seen.items() if len(lengths) == 1}


def _literal_kwargs() -> list[tuple[Path, int, str, str]]:
    """Все `имя="строка"` в вызовах внутри app/."""
    found: list[tuple[Path, int, str, str]] = []
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — не наш файл
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg is None or kw.arg in IGNORED:
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.append((path, kw.lineno, kw.arg, kw.value.value))
    return found


COLUMNS = _string_columns()


def test_there_are_string_columns_to_check() -> None:
    """Если моделей не видно, проверка ниже зелена и бессмысленна."""
    assert len(COLUMNS) > 30, (
        f"нашлось всего {len(COLUMNS)} однозначных строковых колонок — модели не загрузились?"
    )


def test_no_literal_is_wider_than_its_column() -> None:
    """Ни один литерал в коде не длиннее колонки того же имени."""
    problems: list[str] = []
    for path, lineno, arg, value in _literal_kwargs():
        limit = COLUMNS.get(arg)
        if limit is None or len(value) <= limit:
            continue
        rel = path.relative_to(ROOT)
        problems.append(
            f"  {rel}:{lineno}  {arg}={value!r} — {len(value)} символов при колонке {arg} на {limit}"
        )

    assert not problems, (
        "литералы не помещаются в объявленные колонки. На SQLite это пройдёт, "
        "на Postgres — StringDataRightTruncationError и вставший шаг:\n" + "\n".join(problems)
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        # Именно этот случай и стоил вставшего конвейера. Отдельной строкой,
        # чтобы при сужении колонки обратно тест назвал причину сразу.
        ("ref_table", "llm_calls+media_calls"),
    ],
)
def test_known_values_fit(column: str, value: str) -> None:
    limit = COLUMNS.get(column)
    assert limit is not None, f"колонки {column} больше нет — проверку надо обновить"
    assert len(value) <= limit, (
        f"{column} объявлена на {limit} символов, а код пишет {value!r} ({len(value)})"
    )
