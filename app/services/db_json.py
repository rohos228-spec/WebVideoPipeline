"""Чтение поля внутри JSON-колонки — одинаково на обоих диалектах.

`func.json_extract(col, "$.key")` — функция SQLite. В Postgres её нет вовсе:
там оператор ``->>``. Запрос с ней не деградирует и не возвращает пустое —
он падает с `function json_extract does not exist`, и падает в рантайме,
в трёх местах, которые считают дочерние проекты массовой фабрики.

Ровно тот случай, ради которого стоит один хелпер: диалект спрашивается у
живого соединения, а вызывающему всё равно.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, cast, func
from sqlalchemy.sql.elements import ColumnElement


def json_field(column: Any, key: str) -> ColumnElement[Any]:
    """Значение ``column[key]`` как текст, на любом диалекте.

    ``key`` — имя поля верхнего уровня, без синтаксиса пути: вложенность
    здесь никому не понадобилась, а поддержка `$.a.b` на двух диалектах
    стоила бы разбора пути.
    """
    from app.settings import settings

    if settings.is_postgres:
        # `->>` отдаёт текст; для jsonb и json работает одинаково.
        return column.op("->>")(key)
    return func.json_extract(column, f"$.{key}")


def json_field_int(column: Any, key: str) -> ColumnElement[Any]:
    """То же, но приведённое к целому — для сравнения с id."""
    return cast(json_field(column, key), Integer)
