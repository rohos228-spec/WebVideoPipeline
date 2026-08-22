# AGENTS.md

> **Карта системы (промпты, оркестратор, GPT-чат, Create, mass, xlsx):** [`docs/AGENT_MAP.md`](docs/AGENT_MAP.md). Операторская шпаргалка: [`docs/OPERATOR_BIBLE.md`](docs/OPERATOR_BIBLE.md).
> **БД v2 (карточки кадров, дробный порядок, связи, кнопка «База»):** [`docs/DB_V2.md`](docs/DB_V2.md).

## Cursor Cloud specific instructions

### Git: push to this PC's branch (mandatory)

PC branches: **`housepc`**, **`tompc`**, **`strangepc`**, **`workpc`**, plus **`main`**.
Local target = `ORCHESTRATOR_GIT_BRANCH` in `.env` (read it — do not assume `housepc`).

Agent workflow after any code change:

1. `git checkout <ORCHESTRATOR_GIT_BRANCH>` and pull.
2. Commit + **`git push origin <branch>`**.
3. Tell the user **`git HEAD: <sha>` уже в `<branch>`**.

Do **not** push to a different branch than `ORCHESTRATOR_GIT_BRANCH` unless the user explicitly asks.

### Overview

**video-pipeline** is a single Python 3.11+ application (no Docker) that automates short video generation (60–75 sec, 9:16 vertical) with a Telegram bot for HITL approvals. Entrypoint: `python -m app.main`.

### Dev commands

| Task | Command |
|------|---------|
| Install deps | `pip install -e ".[dev]"` |
| ASR NVIDIA (монтаж) | `pip install -e ".[nvidia]"` + `ASR_BACKEND=nvidia` |
| Предзагрузка Parakeet | `python3 scripts/download_nvidia_asr.py` (если WinError 32) |
| Lint | `ruff check .` |
| Tests | `python3 -m pytest tests/ -v` |
| Type check | `mypy app/ --ignore-missing-imports` |
| Seed pilot project | `python3 -m app.seed_pilot` |
| Run application | `STUDIO.cmd` (Windows) or `python3 -m app.main` from repo root |

### Key caveats

- **Telegram optional**: set `TELEGRAM_ENABLED=false` (and leave `TELEGRAM_BOT_TOKEN` empty) for web-only mode — worker + FastAPI on `:8765`, HITL via web UI. Use `STUDIO.cmd` → пункт 1 on Windows. With a valid token, `python -m app.main` in `.venv` runs bot + worker + web.
- **SQLite DB** is at `data/state.db` (auto-created on first run). Delete it to reset state: `rm -f data/state.db`.
- **No `python` alias** — use `python3` on Linux. The system has Python 3.12 which satisfies the `>=3.11,<3.13` constraint.
- **PATH**: Dev tools (`ruff`, `mypy`, `pytest`, `playwright`) install to `~/.local/bin` — ensure it's on `PATH`.
- **Paths** resolve from repo root (`pyproject.toml`), not shell CWD — safe to run `python -m app.main` even after `cd web`. On Windows use `STUDIO.cmd` or `scripts\run-backend.ps1`.
- The app connects to Chrome via CDP on `localhost:29229` for browser automation (Outsee CDP fallback, ElevenLabs). GPT **text** is API-only; default provider is **kie** (`text_llm_provider="kie"` в `app/settings.py`; `GPT_API_KEY` + `GPT_BASE_URL=https://api.kie.ai`). Alternatives — vibecode (`VIBECODE_API_KEY`, `app/services/vibecode_catalog.py`), tokenrouter (`TOKENROUTER_API_KEY`), grsai; переключение — `TEXT_LLM_PROVIDER` или Studio UI (`data/text_llm_choice.json`). kie-ключ на vibecode не подставлять (`gpt_api.py:186-196`). **Outsee media** via Developer API: `OUTSEE_API_KEY` (profile outsee.io) → Bearer `https://outsee.io/api/v1/...` (`app/bots/outsee_http.py`). Set `IMAGE_PROVIDER=outsee` / `VIDEO_PROVIDER=outsee`. Fallback CDP: `OUTSEE_HTTP_FALLBACK_CDP=true`. Alternative: `IMAGE_PROVIDER=grsai` / `VIDEO_PROVIDER=grsai` (`GRSAI_API_KEY` — другой ключ).
- **MiniMax** (`MINIMAX_API_KEY`, `app/bots/minimax.py`) — текст, картинки и видео на одном ключе: `TEXT_LLM_PROVIDER=minimax` (OpenAI-совместимый `/v1/chat/completions`, модель `MiniMax-M3`), `IMAGE_PROVIDER=minimax` (`image-01`, 9:16 → 720×1280), `VIDEO_PROVIDER=minimax` (Hailuo, submit→poll→retrieve). Стартовый кадр и реф уходят **base64 data URL** — публиковать кадры наружу не нужно, файлохостинги и S3 на этом провайдере не участвуют. **MiniMax не поддерживает structured outputs**: `json_schema` и `json_object` принимает молча и игнорирует, поэтому в `GPT_STRUCTURED_RELAYS` его добавлять нельзя — формат держат инструкция в промте + контракты `app/contracts/` + repair-retry. Обязателен `reasoning_split` (иначе `<think>` приезжает в `content`) — ставится автоматически.
- **Стартовый кадр для Outsee** публикуется только через Yandex Object Storage (`YANDEX_STORAGE_*`). Без него `ensure_public_image_url` падает `OutseeApiError` — это не баг. Анонимные файлохостинги (litterbox/catbox/uguu/0x0) отключены; опт-ин `OUTSEE_ALLOW_PUBLIC_HOSTS=true` кладёт кадр в публичный доступ без авторизации.
- Studio Create: provider **outsee** → `POST /api/outsee/generate`; provider **grsai** → `/api/grsai/generate`.
- **Lint/type — держим в нуле**: `ruff check .` и `mypy app/ --ignore-missing-imports` оба чистые (было 888 / 409). Правка, которая их ломает, в main не едет. Гейт — `.claude/verify.json` + `.pre-commit-config.yaml`, ловушка: `ruff --fix --unsafe-fixes` умеет снести импорт, который тесты monkeypatch-ят через модуль (см. noqa в `frame_timeline_sync`).
- Tests use in-memory SQLite and don't require external services or a `.env` file. **НО** им нужна промт-библиотека: без `prompts/` десятки тестов падают на данных, а не на коде — `python3 scripts/check_prompts.py` перед разбором красноты (см. `HANDOVER.md` §2.5).
- **Parallel projects**: `WORKER_MAX_PARALLEL` (default `1`) — top-N window in `app/services/gen_queue.py` + concurrent advances in `_run_worker_loop`. `1` keeps legacy serial behavior; `paused`/`user_stop` on an earlier slot still hard-blocks the rest of the queue.
- **API xlsx write-back — DEPRECATED fallback** (см. `docs/PROMPT_CONTRACT.md`): контракт — apply-ops в DB; `xlsx_text_writeback.py` (downloaded `.xlsx` / TSV `# Лист:`) остаётся только как fallback-ветка в `gpt_operator_client`, не учить модель TSV.
- **Vision checks**: image inputs (`png/jpg/webp/gif`) go to the model as multimodal parts in `app/services/gpt_api.py` (chat `image_url` / Responses `input_image`), up to 8 images / 4MB each.
- **Browser ChatGPT retired for text**: all text/xlsx/check/verdict/anim_pr/hero-prompt/music-prompt go through `app/services/gpt_client.py` → `gpt_api`. CDP remains only for Outsee (image/video/music gen) and ElevenLabs TTS. Default text provider — kie (`GPT_API_KEY` + `GPT_BASE_URL=https://api.kie.ai`, path `/codex/v1/responses`, model `gpt-5-6-sol`); vibecode — `VIBECODE_API_KEY`, model `gpt-5.6-sol`.

### Studio UI version badge

- Build counter lives in `web/STUDIO_VERSION` (line 1 = number, line 2 = git short sha).
- **Before every commit that touches web UI**, run: `python3 scripts/bump_studio_version.py` (bumps version and rebuilds `web/out/`).
- **`web/out/` в git НЕ хранится** (0 отслеживаемых файлов) — сборка локальная: `npm run build` в `web/` или меню студии, пункт [6]. FastAPI отдаёт `web/out/`; если папки нет, вместо UI показывается страница-подсказка (`app/web/api.py:436`).
- Bottom-left badge shows baked version; `/api/studio-version` reports `ui_stale` if `web/out` does not match `STUDIO_VERSION`.
