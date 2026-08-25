"""Каждый путь, который зовёт фронт, существует на бэкенде.

**Зачем.** Проверка типов и сборка фронта на путь не смотрят вообще: строка
`/prompts/${step}` компилируется и собирается ровно так же, как правильная. Мимо
проходит целый класс ошибок, который виден только в браузере — и виден плохо,
потому что выглядит как «промт не читается», а не как «адреса такого нет».

Ровно это и случилось 2026-08-26: правка промтов живёт на `/api/prompt-files`,
а рядом есть другой роутер `/api/prompts` про другое. Клиент подключили ко
второму. Всё собиралось.

**Честно про охват: этот тест ту ошибку НЕ поймал бы.** Проверено обратным
ходом: `/prompts/${step}` сводится к `/prompts/{p}`, а такой маршрут
существует — это `GET /prompts/{prompt_id}` из соседнего роутера. Подмена
одной живой ручки на другую живую проходит мимо, потому что сверяется только
существование адреса.

Ловит он опечатки и выдумки: `…/versions` вместо `…/history`, забытый сегмент,
переименованный на бэкенде маршрут. Это дешёвый предохранитель, а не
контрактный тест: ни метод, ни форма тела, ни тип ответа не проверяются.
Настоящую защиту от подмены ручки даст только вызов живого сервера — до него
дело дойдёт, когда появится e2e.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_TS = ROOT / "web" / "src" / "lib" / "api.ts"

#: `${...}` в шаблонной строке → `{param}`: путь FastAPI записан так же.
_INTERP = re.compile(r"\$\{[^}]*\}")

#: Путь берём только из настоящего вызова — `req<T>("/…")`, `post<T>(`/…`)`.
#: Разбор «любая строка со слэша» ловил заодно проверки префиксов вроде
#: `path.startsWith("/auth/")`, то есть ругался на то, что маршрутом и не
#: притворялось.
_CALL = re.compile(r"\b(?:req|post|patch|put|del)\s*<[^>]*>\s*\(\s*[`\"](/[^`\"]*)")


def _frontend_paths() -> set[str]:
    src = API_TS.read_text(encoding="utf-8")
    out: set[str] = set()
    for raw in _CALL.findall(src):
        path = _INTERP.sub("{p}", raw)
        # Обрезаем всё, что к маршруту не относится: строку запроса и хвост
        # вложенной шаблонной вставки (`?project_id=` собирается тернарником
        # прямо внутри пути, и закрывающей кавычки regex там не видит).
        path = path.split("?", 1)[0].split("$", 1)[0].rstrip("/") or "/"
        if path.startswith("/api"):  # сама обёртка req(), не маршрут
            continue
        out.add(path)
    return out


def _backend_paths() -> set[str]:
    """Маршруты приложения без префикса `/api`."""
    from app.web import api as web_api

    out: set[str] = set()
    for module_name in dir(web_api):
        obj = getattr(web_api, module_name)
        router = getattr(obj, "router", None)
        if router is None:
            continue
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", "")
            if path:
                out.add(_INTERP.sub("{p}", re.sub(r"\{[^}]*\}", "{p}", path)))
    return out


def test_frontend_paths_exist_on_the_backend() -> None:
    frontend = _frontend_paths()
    backend = _backend_paths()

    assert len(backend) > 50, (
        f"собрано всего {len(backend)} маршрутов — роутеры не импортировались, "
        "и проверка ниже была бы зелёной впустую"
    )
    assert len(frontend) > 10, f"из api.ts извлечено всего {len(frontend)} путей — разбор сломался"

    missing = sorted(p for p in frontend if p not in backend)
    assert not missing, (
        "фронт зовёт адреса, которых на бэкенде нет:\n  "
        + "\n  ".join(missing)
        + "\nСборка и tsc такое пропускают — в браузере это выглядит как "
        "«не читается», а не как «нет такого адреса»."
    )
