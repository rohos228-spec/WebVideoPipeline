"""Необработанное исключение попадает в журнал и отвечает внятным 500.

Первый живой 500 на сервере ушёл клиенту как голый «Internal Server Error», а
в `docker logs` не осталось ни строки: журнал ведёт loguru, а стандартный
логгер uvicorn, куда Starlette пишет трейс, в него не заведён. Диагностика
заняла бы минуту с трейсом — и заняла час без него.
"""

from __future__ import annotations

from loguru import logger
from starlette.testclient import TestClient

from app.web.api import create_app


async def _boom() -> None:
    raise RuntimeError("проба необработанного исключения")


def test_unhandled_error_is_logged_and_explained() -> None:
    app = create_app()
    # В начало таблицы маршрутов: `create_app` вешает catch-all статики
    # фронта, и маршрут, добавленный после него, недостижим (404).
    app.add_api_route("/api/_boom", _boom)
    app.router.routes.insert(0, app.router.routes.pop())

    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="ERROR")
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/_boom")
    finally:
        logger.remove(sink_id)

    assert r.status_code == 500
    assert "RuntimeError" in r.json()["detail"], "клиент должен видеть тип ошибки, а не голый 500"

    joined = "\n".join(captured)
    assert "необработанное исключение" in joined
    assert "проба необработанного исключения" in joined, "трейс с текстом ошибки обязан быть в журнале"
