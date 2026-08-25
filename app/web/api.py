"""FastAPI app: REST + WS + static frontend serving.

Подключается к существующей SQLite-БД через `app.db.session_scope`. Ничего
не создаёт сам — миграции/таблицы делает `app.main._init_db()`.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.services.event_bus import get_bus
from app.web.identity import IdentityMiddleware
from app.web.routers import (
    artifacts as artifacts_router,
)
from app.web.routers import (
    auth as auth_router,
)
from app.web.routers import (
    billing as billing_router,
)
from app.web.routers import (
    bug_reports as bug_reports_router,
)
from app.web.routers import (
    config_presets as config_presets_router,
)
from app.web.routers import (
    create_queue as create_queue_router,
)
from app.web.routers import (
    db_browser as db_browser_router,
)
from app.web.routers import (
    fleet as fleet_router,
)
from app.web.routers import (
    frames as frames_router,
)
from app.web.routers import (
    generation_options as generation_options_router,
)
from app.web.routers import (
    gpt_workspace as gpt_workspace_router,
)
from app.web.routers import (
    grsai as grsai_router,
)
from app.web.routers import (
    hitl as hitl_router,
)
from app.web.routers import (
    knowledge as knowledge_router,
)
from app.web.routers import (
    library as library_router,
)
from app.web.routers import (
    llm_costs as llm_costs_router,
)
from app.web.routers import (
    me as me_router,
)
from app.web.routers import (
    my_prompts as my_prompts_router,
)
from app.web.routers import (
    node_groups as node_groups_router,
)
from app.web.routers import (
    outsee_create as outsee_create_router,
)
from app.web.routers import (
    outsee_http as outsee_http_router,
)
from app.web.routers import (
    project_ops as project_ops_router,
)
from app.web.routers import (
    projects as projects_router,
)
from app.web.routers import (
    prompt_files as prompt_files_router,
)
from app.web.routers import (
    prompt_studio as prompt_studio_router,
)
from app.web.routers import (
    prompts as prompts_router,
)
from app.web.routers import (
    runs as runs_router,
)
from app.web.routers import (
    runtime_streams as runtime_streams_router,
)
from app.web.routers import (
    sidebar_layout as sidebar_layout_router,
)
from app.web.routers import (
    stages as stages_router,
)
from app.web.routers import (
    studio_chat as studio_chat_router,
)
from app.web.routers import (
    text_llm as text_llm_router,
)
from app.web.routers import (
    workflows as workflows_router,
)
from app.web.settings_default import seed_default_workflow

API_PREFIX = "/api"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Идемпотентная инициализация:
    1) миграции до head (схема, в т.ч. новые таблицы и колонки);
    2) seed дефолтного Workflow.

    Безопасно повторно вызывается. В дев-режиме (uvicorn `--reload` без app.main)
    этот lifespan единственный гарантирует актуальную схему.
    """
    # ── Конфигурация проверяется ПЕРВОЙ, до миграций и до подключения.
    #
    # Порядок не косметический. Миграции на живой базе идут минутами, а
    # подключение к Postgres может не состояться по десятку причин — и его
    # ошибка накрывает собой все остальные. Слабый секрет обязан быть назван
    # слабым секретом сразу, а не после трёх минут ожидания и не под видом
    # «could not connect to server»: иначе чинить будут не то.
    from app.settings import settings

    # Секрет короче 32 байт для HS256 — отказ, а не предупреждение. RFC 7518
    # §3.2 требует ключ не короче размера хеша, и раньше здесь стояло
    # предупреждение по единственной причине: секрет был общим с биллингом, и
    # сменить его в одиночку было нельзя. Общего секрета больше нет — студия
    # подписывает свои токены сама, и слабый ключ на входной двери это не
    # «ниже нормы», а подделываемый токен админа.
    if settings.accounts_enabled and len(settings.studio_session_secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "STUDIO_SESSION_SECRET короче 32 байт — для HS256 это ниже нормы "
            "RFC 7518 §3.2. Сгенерировать: python3 -c "
            "'import secrets; print(secrets.token_urlsafe(48))'"
        )

    # Открытый порт без учётных записей. До 2026-08-24 его закрывала пара
    # WEB_AUTH_USER/WEB_AUTH_PASSWORD — пароль открытым текстом в окружении,
    # сравниваемый оператором `!=`. Пара удалена вместе с этим способом
    # защиты, и молча остаться с открытым API нельзя: `/api/fleet` запускает
    # команды на машинах парка, `/api/db` листает базу.
    if not settings.accounts_enabled and settings.web_host.strip() in ("0.0.0.0", "::", "*"):
        raise RuntimeError(
            f"WEB_HOST={settings.web_host} без учётных записей: API открыт всей сети без "
            "какой-либо проверки. Либо задайте STUDIO_SESSION_SECRET и заведите "
            "учётки (python3 -m app.seed_admin), либо верните WEB_HOST=127.0.0.1."
        )

    # Учётные записи на SQLite — не «пока не переехали», а работа с
    # арендаторами там, где политик нет физически. `require_isolation` поймает
    # это на первом же запросе, но лучше не подняться: упавший старт видно, а
    # 500 на одной ручке из тридцати можно не заметить неделю. Проверка тоже
    # чисто конфигурационная — гнать ради неё миграции незачем.
    if settings.accounts_enabled and not settings.is_postgres:
        raise RuntimeError(
            "STUDIO_SESSION_SECRET задан, а база — SQLite: row-level security "
            "в этом движке не существует, изоляция арендаторов не обеспечена. "
            "Задайте DATABASE_URL на Postgres (docs/SAAS-PIVOT.md §11)."
        )

    try:
        from app.db_migrations import upgrade_to_head

        await upgrade_to_head()
    except Exception:  # noqa: BLE001
        # Не глушим смысл: без схемы приложение всё равно нерабочее, но
        # падать в lifespan значит не показать пользователю ни одной страницы
        # с причиной. Логируем и продолжаем — роуты отдадут ошибку сами.
        logger.exception("миграции не прошли — схема может быть неактуальной")

    # Изоляция арендаторов — единственная проверка, после которой не
    # продолжают. Все три способа её потерять бесшумны: таблица без политики
    # видна всем, политика без FORCE не действует на владельца, роль с
    # BYPASSRLS игнорирует политики. Ни один не даст ошибки в логе — он даст
    # утечку чужого ролика. Падение на старте чинится за минуту, утечка не
    # чинится вовсе.
    if settings.is_postgres:
        from app.db import session_scope as _scope
        from app.services.rls_check import assert_rls_or_die

        async with _scope() as s:
            await assert_rls_or_die(s)

    try:
        from app.db import session_scope
        from app.services.local_library import ensure_library_dirs, import_existing_prompts

        ensure_library_dirs()
        async with session_scope() as s:
            info = await import_existing_prompts(s)
            logger.info("web lifespan: local library import {}", info)

        # Промт-библиотека поднимается в память один раз за старт: чтение
        # промта синхронно и идёт из глубины шагов, а запрос к базе оттуда
        # означал бы переписать половину конвейера на async ради десятка
        # килобайт, которые между шагами не меняются (§9.4).
        from app.services import prompt_store

        async with session_scope() as s:
            loaded = await prompt_store.refresh(s)
            if not loaded:
                # Пустая база — наполняем системный уровень с диска. Уже
                # загруженное не трогаем: файл мог остаться от прошлой
                # версии, а в базе промт могли править.
                stats = await prompt_store.import_from_disk(s)
                await prompt_store.refresh(s)
                logger.info("web lifespan: промт-библиотека с диска {}", stats)
            else:
                logger.info("web lifespan: промт-библиотека из базы, {} строк", loaded)

            # Встроенные промты — в базу, если их там нет. Именно в базу, а не
            # на диск: на сервере диск только для чтения, и шаг без файла
            # иначе оставался бы невидим для редактора.
            from app.services.builtin_prompts import seed_builtin_prompts

            await seed_builtin_prompts(s)

        # Контракт промтов проверяется по тому, что реально уйдёт в модель —
        # по базе, не по диску. Промт, который просит приложить xlsx, здесь
        # не блокируется (правят люди, и ошибку надо увидеть, а не спрятать),
        # но и молчать о нём нельзя: снаружи это «шаг вернул мусор».
        from app.services.prompt_contract import audit_store

        for step, name, bad in audit_store():
            logger.warning(
                "промт {}/{} расходится с PROMPT_CONTRACT ({} мест), первое — {}",
                step,
                name,
                len(bad),
                bad[0],
            )
    except Exception:  # noqa: BLE001
        logger.exception("local library import failed (non-fatal)")
    try:
        await seed_default_workflow()
    except Exception:  # noqa: BLE001
        logger.exception("seed_default_workflow failed (non-fatal)")

    try:
        from app.db import session_scope
        from app.services.node_groups import backfill_group_stamps

        async with session_scope() as s:
            await backfill_group_stamps(s)
    except Exception:  # noqa: BLE001
        logger.exception("node_groups backfill failed (non-fatal)")

    try:
        from app.db import session_scope
        from app.services.startup_guard import block_pipeline_autorun_on_startup

        async with session_scope() as s:
            guard_stats = await block_pipeline_autorun_on_startup(s)
            logger.warning("web lifespan startup autorun guard: {}", guard_stats)
    except Exception:  # noqa: BLE001
        logger.exception("startup autorun guard failed (non-fatal)")

    try:
        from app.services.gpt_workspace import reset_running_sessions_on_startup

        gpt_reset = reset_running_sessions_on_startup()
        if gpt_reset.get("reset"):
            logger.warning("web lifespan gpt_workspace orphan reset: {}", gpt_reset)
    except Exception:  # noqa: BLE001
        logger.exception("gpt_workspace orphan reset failed (non-fatal)")

    from app.services.pipeline_worker import ensure_pipeline_worker_started
    from app.settings import settings
    from app.telegram.noop_bot import get_worker_bot

    live_log_path = settings.data_dir / "studio-live.log"
    live_log_path.parent.mkdir(parents=True, exist_ok=True)
    live_log_sink = logger.add(
        str(live_log_path),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function} | {message}",
        level="DEBUG",
        enqueue=True,
        rotation="30 MB",
        retention=2,
    )
    logger.info("studio live log → {}", live_log_path)

    ensure_pipeline_worker_started(get_worker_bot(None))
    logger.info("web lifespan: pipeline worker ensured (same process as API)")

    try:
        from app.fleet.agent_loop import start_fleet_agent
        from app.fleet.montage_queue import start_montage_queue_loop
        from app.fleet.pull_loop import start_fleet_pull_loop
        from app.fleet.self_node import ensure_self_fleet_node

        await ensure_self_fleet_node()
        start_fleet_agent()
        start_fleet_pull_loop()
        start_montage_queue_loop()
    except Exception:  # noqa: BLE001
        logger.exception("fleet init failed (non-fatal)")

    try:
        from app.services.reconciler import reconcile

        await reconcile(scope="startup")
    except Exception:  # noqa: BLE001
        logger.exception("startup reconcile failed (non-fatal)")

    try:
        yield
    finally:
        logger.remove(live_log_sink)


def create_app() -> FastAPI:
    app = FastAPI(
        title="video-pipeline web",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    # Личность и арендатор. Порядок здесь имеет значение и он обратный
    # интуиции: `add_middleware` вставляет слой в НАЧАЛО списка, поэтому
    # добавленный ПОСЛЕДНИМ оказывается снаружи всех. Слой личности нужен
    # внутри CORS, а не снаружи: браузерный preflight (`OPTIONS`) не несёт
    # заголовка `Authorization` — снаружи он получал бы 401 без единого
    # CORS-заголовка, и кросс-доменный фронт ложился бы целиком, ещё не
    # успев отправить токен. Проверено `test_cors_preflight_is_not_refused`.
    # В режиме владельца слой не делает ничего (`settings.accounts_enabled`).
    app.add_middleware(IdentityMiddleware)

    # Локальный фронт ходит с localhost:3000 в dev — открываем CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(workflows_router.router, prefix=API_PREFIX)
    app.include_router(projects_router.router, prefix=API_PREFIX)
    app.include_router(project_ops_router.router, prefix=API_PREFIX)
    app.include_router(generation_options_router.router, prefix=API_PREFIX)
    app.include_router(config_presets_router.router, prefix=API_PREFIX)
    app.include_router(outsee_create_router.router, prefix=API_PREFIX)
    app.include_router(outsee_http_router.router, prefix=API_PREFIX)
    app.include_router(create_queue_router.router, prefix=API_PREFIX)
    app.include_router(gpt_workspace_router.router, prefix=API_PREFIX)
    app.include_router(text_llm_router.router, prefix=API_PREFIX)
    app.include_router(grsai_router.router, prefix=API_PREFIX)
    app.include_router(sidebar_layout_router.router, prefix=API_PREFIX)
    app.include_router(runtime_streams_router.router, prefix=API_PREFIX)
    app.include_router(runs_router.router, prefix=API_PREFIX)
    app.include_router(llm_costs_router.router, prefix=API_PREFIX)
    app.include_router(prompts_router.router, prefix=API_PREFIX)
    app.include_router(prompt_studio_router.router, prefix=API_PREFIX)
    app.include_router(prompt_files_router.router, prefix=API_PREFIX)
    app.include_router(library_router.router, prefix=API_PREFIX)
    app.include_router(hitl_router.router, prefix=API_PREFIX)
    app.include_router(knowledge_router.router, prefix=API_PREFIX)
    app.include_router(frames_router.router, prefix=API_PREFIX)
    app.include_router(artifacts_router.router, prefix=API_PREFIX)
    app.include_router(artifacts_router.files_router, prefix=API_PREFIX)
    app.include_router(bug_reports_router.router, prefix=API_PREFIX)
    app.include_router(fleet_router.router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(me_router.router, prefix=API_PREFIX)
    app.include_router(billing_router.router, prefix=API_PREFIX)
    app.include_router(my_prompts_router.router, prefix=API_PREFIX)
    app.include_router(studio_chat_router.router, prefix=API_PREFIX)
    app.include_router(db_browser_router.router, prefix=API_PREFIX)
    app.include_router(node_groups_router.router, prefix=API_PREFIX)
    app.include_router(stages_router.router, prefix=API_PREFIX)

    @app.api_route(f"{API_PREFIX}/{{rest:path}}", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def api_write_not_found(rest: str) -> None:
        """Не даём GET catch-all отвечать 405 на неизвестные POST /api/*."""
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="API route not found — перезапустите Studio (STUDIO.cmd → [1])",
        )

    # ── WebSocket: live-стрим событий выбранного канала ──
    # Лимит: утечки на клиенте (до WS-hub) иначе копят 200+ сокетов и
    # душат event loop — anim_pr / GPT API выглядят «зависшими».
    _ws_active = {"n": 0}
    _WS_MAX = 48

    @app.websocket("/ws/{channel:path}")
    async def ws_channel(ws: WebSocket, channel: str) -> None:
        """Клиент подписывается на канал (например, `runs.42`, `global`,
        `hitl.7`). Сервер шлёт JSON-сообщения каждое полученное событие.
        """
        if _ws_active["n"] >= _WS_MAX:
            await ws.accept()
            with suppress(Exception):
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "detail": f"too many websocket connections (max {_WS_MAX})",
                        }
                    )
                )
                await ws.close(code=1013)
            return
        await ws.accept()
        _ws_active["n"] += 1
        bus = get_bus()
        try:
            async with bus.subscribe(channel) as queue:
                # Сразу шлём «hello», чтобы клиент знал, что подписка живая.
                await ws.send_text(json.dumps({"type": "subscribed", "channel": channel}))
                while True:
                    # Параллельно ждём сообщения из bus и пинг от клиента.
                    get_event_task = asyncio.create_task(queue.get())
                    recv_task = asyncio.create_task(ws.receive_text())
                    done, pending = await asyncio.wait(
                        {get_event_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if get_event_task in done:
                        try:
                            evt = get_event_task.result()
                        except Exception:  # noqa: BLE001
                            break
                        await ws.send_text(json.dumps(evt, default=str))
                    if recv_task in done:
                        # Игнорируем содержимое (пока что), но обработка нужна
                        # для детекта disconnect.
                        try:
                            recv_task.result()
                        except WebSocketDisconnect:
                            break
                        except Exception:
                            break
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001
            logger.exception("ws channel={} crashed", channel)
            with suppress(Exception):
                await ws.close(code=1011)
        finally:
            _ws_active["n"] = max(0, _ws_active["n"] - 1)

    # ── Health ──
    @app.get(f"{API_PREFIX}/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/studio-version")
    async def studio_version() -> dict[str, str | int]:
        from app.web.studio_version import read_studio_version

        return read_studio_version()

    # ── Статика Next.js (export → ./web/out) ──
    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Если есть собранный Next.js (`web/out` после `next build && next export`
    либо `web/.next` через middleware), отдаём как статику.

    В dev режиме фронт работает отдельно на http://localhost:3000 → CORS
    разрешён, эту функцию можно игнорировать.
    """
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root / "web" / "out"
    if not out_dir.is_dir():
        logger.warning(
            "web frontend bundle not found ({}). Run: cd web && npm run build",
            out_dir,
        )
        _mount_frontend_missing_help(app, out_dir)
        return

    # Mount всю папку (включая _next/*).
    app.mount(
        "/_next",
        StaticFiles(directory=out_dir / "_next", check_dir=True),
        name="next-static",
    )

    def _patch_index_html_version(html: str) -> str:
        """Подменяет v102 и др. в отданном index.html на web/STUDIO_VERSION (git pull)."""
        from app.web.studio_version import read_studio_version_label

        label = read_studio_version_label()
        html = re.sub(
            r'(title="UI:\s*)v\d+[^"]*(")',
            rf"\1{label}\2",
            html,
            count=1,
        )
        html = re.sub(
            r"(>)\s*v\d+\s*·\s*[0-9a-fA-F]{4,}\s*(<)",
            rf"\1{label}\2",
            html,
        )
        return html

    def _html_response(path: Path) -> HTMLResponse:
        body = path.read_text(encoding="utf-8")
        body = _patch_index_html_version(body)
        return HTMLResponse(
            content=body,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/", response_model=None)
    async def root_index():
        return _html_response(out_dir / "index.html")

    @app.get("/{full_path:path}", response_model=None)
    async def catch_all(full_path: str):
        # /api/* обслуживают FastAPI-роутеры — не отдаём index.html (иначе в браузере
        # «открывается проект» вместо JSON на /api/studio-version).
        if full_path == "api" or full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        # Next static export — все маршруты как .html-файлы.
        candidate = out_dir / full_path
        if candidate.is_file():
            if candidate.suffix.lower() in {".html", ".htm"}:
                return _html_response(candidate)
            return FileResponse(candidate)
        html_variant = out_dir / f"{full_path}.html"
        if html_variant.is_file():
            return _html_response(html_variant)
        return _html_response(out_dir / "index.html")  # SPA fallback


def _mount_frontend_missing_help(app: FastAPI, out_dir: Path) -> None:
    """Показываем понятную страницу, если web/out ещё не собран."""
    from fastapi.responses import HTMLResponse

    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Video Pipeline</title>
<style>
body{{font-family:Segoe UI,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.5}}
code{{background:#f0f0f0;padding:2px 6px;border-radius:4px}}
ol{{padding-left:1.2rem}}
</style></head><body>
<h1>API работает, UI не собран</h1>
<p>Бэкенд запущен, но папки <code>{out_dir}</code> нет.</p>
<h2>Windows (из корня video-pipeline)</h2>
<ol>
<li>Меню: <strong>6. Build Web UI</strong> или <strong>* Quick start</strong></li>
<li>Или в PowerShell:<br>
<code>cd web; npm install; npm run build; cd ..</code></li>
<li>Запустите Studio: <strong>2. Start Studio</strong></li>
<li>Откройте <a href="http://127.0.0.1:8765">http://127.0.0.1:8765</a></li>
</ol>
<p>API health: <a href="/api/health">/api/health</a></p>
</body></html>"""

    @app.get("/")
    async def frontend_missing_root() -> HTMLResponse:
        return HTMLResponse(html)

    @app.get("/{full_path:path}")
    async def frontend_missing_catch(full_path: str) -> HTMLResponse:
        return HTMLResponse(html)
