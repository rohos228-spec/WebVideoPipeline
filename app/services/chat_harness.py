"""Обвязка общего чата Studio GPT: модель не «голая».

Без обвязки модель в чате выдумывала дату («сегодня 11 марта 2026»), не знала
проектов студии («пришлите скриншот»), отправляла жать несуществующую кнопку
«Сгенерировать» и говорила, что не умеет искать. Здесь:

- контекст к каждому сообщению: настоящие дата и время, проекты студии
  (только своего арендатора — сессия БД сама режет чужие);
- инструменты по простому текстовому протоколу (работает с любым провайдером):
  ``project`` (состояние проекта), ``news`` (свежие новости), ``wiki`` (справка),
  ``open_url`` (прочитать страницу по ссылке пользователя);
- честные границы: запуск и правки проектов делает «ИИ-агент» проекта
  с подтверждением, общий чат только подсказывает, куда написать.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

from loguru import logger

from app.services.chat_humanize import humanize_reply

MAX_TOOL_ROUNDS = 3
_TOOL_RESULT_MAX = 6000
_HTTP_TIMEOUT = 15.0
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

_WIKI_UA = "VIA-Studio-chat/1.0 (video studio assistant; low volume)"

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MSK = timedelta(hours=3)


def _msk_now(now: datetime | None = None) -> datetime:
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return base.astimezone(UTC) + _MSK


def _ru_date(d: datetime) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def date_lines(now: datetime | None = None) -> list[str]:
    """Настоящие дата, время и границы недели — модель своих дат не знает."""
    m = _msk_now(now)
    monday = m - timedelta(days=m.weekday())
    sunday = monday + timedelta(days=6)
    return [
        f"СЕЙЧАС: {_ru_date(m)}, {_WEEKDAYS[m.weekday()]}, {m:%H:%M} по Москве.",
        f"Эта неделя: {_ru_date(monday)} — {_ru_date(sunday)}. Вчера: {_ru_date(m - timedelta(days=1))}.",
    ]


# ── проекты студии ────────────────────────────────────────────────────────


async def _projects(limit: int = 40) -> list[Any]:
    from sqlalchemy import select
    from sqlalchemy.orm import load_only

    import app.db as app_db
    from app.models import Project

    async with app_db.SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Project)
                    .options(
                        load_only(
                            Project.id,
                            Project.slug,
                            Project.title,
                            Project.status,
                            Project.updated_at,
                        )
                    )
                    .order_by(Project.updated_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def _project_stats() -> tuple[int, list[tuple[str, int]]]:
    """Сколько всего проектов и сколько в каждом статусе — по всем, не только по показанным."""
    from sqlalchemy import func, select

    import app.db as app_db
    from app.models import Project

    async with app_db.SessionLocal() as session:
        rows = (await session.execute(select(Project.status, func.count()).group_by(Project.status))).all()
    stats = sorted(((str(getattr(st, "value", st) or ""), int(n)) for st, n in rows), key=lambda x: -x[1])
    return sum(n for _, n in stats), stats


def _status_value(row: Any) -> str:
    st = getattr(row, "status", None)
    return str(getattr(st, "value", st) or "")


def _project_title(row: Any) -> str:
    return str(getattr(row, "title", None) or getattr(row, "slug", None) or f"проект {row.id}")


async def projects_lines(limit: int = 40) -> list[str]:
    from app.services.action_layer import builtin_insight as bi

    try:
        rows = await _projects(limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat_harness: projects list failed: {}", e)
        return ["ПРОЕКТЫ СТУДИИ: список сейчас не прочитался — так и скажи, если спросят."]
    if not rows:
        return ["ПРОЕКТЫ СТУДИИ: проектов пока нет."]
    total, by_status = await _project_stats()
    counts = ", ".join(f"{bi._status_ru(st)} — {n}" for st, n in by_status)
    lines = [
        f"ВСЕГО ПРОЕКТОВ в студии: {total}. По статусам (все проекты): {counts}.",
        f"ПРОЕКТЫ СТУДИИ пользователя (последние изменённые сверху, показано {len(rows)} из {total}):",
    ]
    for r in rows:
        upd = getattr(r, "updated_at", None)
        when = f", изменён {_ru_date(_msk_now(upd))}" if isinstance(upd, datetime) else ""
        lines.append(f"- #{r.id} «{_project_title(r)}» — {bi._status_ru(_status_value(r))}{when}")
    return lines


async def _find_project(arg: Any) -> Any | None:
    rows = await _projects(200)
    s = str(arg or "").strip().lstrip("#")
    if s.isdigit():
        return next((r for r in rows if r.id == int(s)), None)
    low = s.lower()
    if not low:
        return None
    exact = [r for r in rows if _project_title(r).lower() == low]
    if exact:
        return exact[0]
    words = [w for w in re.findall(r"\w+", low) if len(w) >= 3]
    best, score = None, 0
    for r in rows:
        t = _project_title(r).lower()
        sc = sum(1 for w in words if w[:5] in t)
        if sc > score:
            best, score = r, sc
    return best


async def tool_project(args: dict) -> str:
    from sqlalchemy import func, select

    import app.db as app_db
    from app.models import Frame, Project
    from app.services.action_layer import builtin_insight as bi

    row = await _find_project(args.get("id") or args.get("project") or args.get("title") or args.get("query"))
    if row is None:
        return "Проект не найден среди проектов студии пользователя."
    async with app_db.SessionLocal() as session:
        p = await session.get(Project, row.id)
        frames = (
            await session.execute(select(func.count()).select_from(Frame).where(Frame.project_id == row.id))
        ).scalar()
    if p is None:
        return "Проект не найден."
    status = _status_value(p)
    meta = p.meta if isinstance(p.meta, dict) else {}
    steps = bi._pipeline(status, meta)
    cur, nxt = bi._current_next(steps, status)
    done = [s["title"] for s in steps if s.get("state") == "готово"]
    lines = [
        f"Проект #{p.id} «{_project_title(p)}»: {bi._status_ru(status)}.",
        f"Тема: {bi._clip(p.topic, 300)}" if p.topic else "",
        f"Кадров: {frames or 0}.",
        f"Готово шагов: {len(done)} из {len(steps)}" + (f" ({', '.join(done)})." if done else "."),
        f"Текущий шаг: {cur['title']} ({cur['state']})." if cur else "",
        f"Следующий шаг: {nxt['title']}." if nxt else "",
    ]
    fs = bi._step_failure(meta)
    if fs:
        where = f" (шаг «{fs['step']}»)" if fs.get("step") else ""
        lines.append(
            f"Ошибка{where}: {fs.get('error') or 'без текста'}; сбоев подряд: {fs.get('fails') or 0}."
        )
    if p.script_text:
        lines.append(f"Начало закадрового текста: {bi._clip(p.script_text, 600)}")
    return humanize_reply("\n".join(x for x in lines if x), _canvas_keymap(meta))


def _canvas_keymap(meta: dict) -> dict[str, dict]:
    """Ключ ноды → тип и подпись с канваса (для перевода ключей в названия)."""
    cg = meta.get("canvas_graph") if isinstance(meta, dict) else None
    nodes = (cg or {}).get("nodes") if isinstance(cg, dict) else None
    out: dict[str, dict] = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            out[str(n["id"])] = {
                "type": str(n.get("type") or ""),
                "label": str((n.get("data") or {}).get("label") or ""),
            }
    return out


# ── интернет ──────────────────────────────────────────────────────────────


def _get_sync(url: str, params: dict | None) -> tuple[int, str]:
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    # Википедия режет «браузерный» UA без подписи — ей нужна честная подпись клиента.
    # И отвечает 403 на httpx при любом UA, поэтому здесь обычный urllib.
    ua = _WIKI_UA if ".wikipedia.org/" in url else _UA
    req = urllib.request.Request(url, headers={"User-Agent": ua})

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        # Публичная ссылка не должна увести редиректом во внутреннюю сеть.
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
            bad = _public_http_url(newurl)
            if bad:
                raise urllib.error.HTTPError(newurl, 403, f"redirect blocked: {bad}", headers, fp)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_SafeRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(req, timeout=_HTTP_TIMEOUT) as r:
            raw = r.read(600_000)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning("chat_harness: GET {} failed: {}", url[:120], e)
        return 0, ""


def _ssl_context():
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


async def _get(url: str, *, params: dict | None = None) -> tuple[int, str]:
    return await asyncio.to_thread(_get_sync, url, params)


async def tool_news(args: dict) -> str:
    q = str(args.get("query") or args.get("q") or "").strip()
    if not q:
        return "Пустой запрос для новостей."
    days = int(args.get("days") or 7)
    days = max(1, min(days, 60))
    url = f"https://news.google.com/rss/search?q={quote(q)}+when:{days}d&hl=ru&gl=RU&ceid=RU:ru"
    code, body = await _get(url)
    if code >= 400 or not body:
        return f"Новости не получены (код {code})."
    try:
        root = ElementTree.fromstring(body.encode("utf-8"))
    except ElementTree.ParseError:
        return "Новости не разобрались."
    items = root.findall(".//item")[:8]
    if not items:
        return f"По запросу «{q}» за {days} дн. новостей не нашлось."
    out = [f"Новости по запросу «{q}» за последние {days} дн. (Google News):"]
    for it in items:
        title = html.unescape(it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        out.append(f"- {title} | {pub} | {link}")
    return "\n".join(out)


async def tool_wiki(args: dict) -> str:
    q = str(args.get("query") or args.get("q") or "").strip()
    if not q:
        return "Пустой запрос для справки."
    for lang in ("ru", "en"):
        code, body = await _get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": q, "limit": "1", "format": "json"},
        )
        if code >= 400:
            continue
        try:
            titles = json.loads(body)[1]
        except (ValueError, IndexError, TypeError):
            continue
        if not titles:
            continue
        code, body = await _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(titles[0])}")
        if code < 400:
            try:
                d = json.loads(body)
            except ValueError:
                continue
            page = ((d.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
            return f"Википедия «{d.get('title')}»: {d.get('extract') or ''}\n{page}"
    return f"В Википедии по «{q}» ничего не нашлось."


def _public_http_url(url: str) -> str | None:
    """Только http(s) и только публичные адреса — чат не ходит во внутреннюю сеть."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return "кривая ссылка"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "нужна ссылка http(s)"
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError:
        return "адрес не находится"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "внутренние адреса закрыты"
    return None


def _html_text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


async def tool_open_url(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    bad = await asyncio.to_thread(_public_http_url, url)
    if bad:
        return f"Ссылку не открыл: {bad}."
    code, body = await _get(url)
    if code >= 400:
        return f"Страница не открылась (код {code})."
    text = _html_text(body)
    return f"Текст страницы {url}:\n{text[:5000]}" if text else "Страница пустая."


TOOLS = {
    "project": (tool_project, "Ищу проект в студии…"),
    "news": (tool_news, "Ищу свежие новости…"),
    "wiki": (tool_wiki, "Смотрю справку…"),
    "open_url": (tool_open_url, "Открываю ссылку…"),
}

_TOOL_START_RE = re.compile(r'\{\s*"tool"\s*:')
_TOOL_NAME_RE = re.compile(r'"tool"\s*:\s*"(\w+)"')
_TOOL_ARGS_RE = re.compile(r'"args"\s*:\s*(\{[^{}]*\})')


def parse_tool_call(reply: str) -> tuple[str, dict] | None:
    """Есть ли в ответе вызов инструмента ``{"tool": "...", "args": {...}}``.

    Модель пишет его по-разному: одним JSON, после «Проверяю…», в начале и тут же
    «сама отвечает» дальше, иногда забывает закрывающую скобку. Вызов — первый
    объект с ключом "tool" и известным именем; всё, что модель дописала сама,
    не показываем, а спрашиваем её заново уже с результатом.
    """
    text = reply or ""
    dec = json.JSONDecoder()
    for m in _TOOL_START_RE.finditer(text):
        try:
            obj, _end = dec.raw_decode(text, m.start())
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("tool") in TOOLS:
            args = obj.get("args")
            return str(obj["tool"]), args if isinstance(args, dict) else {}
        # Кривой JSON: имя и плоские args вытаскиваем по отдельности.
        chunk = text[m.start() : m.start() + 800]
        name = _TOOL_NAME_RE.search(chunk)
        if name and name.group(1) in TOOLS:
            args_m = _TOOL_ARGS_RE.search(chunk)
            try:
                args = json.loads(args_m.group(1)) if args_m else {}
            except ValueError:
                args = {}
            return name.group(1), args if isinstance(args, dict) else {}
    return None


async def run_tool(name: str, args: dict) -> str:
    fn = TOOLS[name][0]
    try:
        res = await fn(args)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat_harness: tool {} failed: {}", name, e)
        res = f"Инструмент {name} не сработал: {str(e)[:200]}"
    return res[:_TOOL_RESULT_MAX]


HARNESS_RULES = (
    "ОБВЯЗКА STUDIO (важнее твоих догадок):\n"
    "- Дата и время — ТОЛЬКО из строки СЕЙЧАС ниже. Свою дату не придумывай.\n"
    "- Про проекты пользователя отвечай по списку ПРОЕКТЫ СТУДИИ ниже. Не проси "
    "скриншот списка — он у тебя есть. Подробности проекта (шаги, кадры, ошибка, текст) — "
    "инструментом project.\n"
    "- ИНСТРУМЕНТЫ. Чтобы вызвать, ответь ТОЛЬКО одним JSON без другого текста: "
    '{"tool":"<имя>","args":{...}}. Studio выполнит и пришлёт результат, потом ответь человеку. '
    f"Не больше {MAX_TOOL_ROUNDS} вызовов на вопрос. Перед вызовом НЕ пиши «проверяю/ищу» — только JSON.\n"
    '  · project {"id":26} или {"title":"ежи"} — состояние проекта студии;\n'
    '  · news {"query":"…","days":7} — свежие новости (Google News), для «что нового», '
    "«новости», событий после твоих знаний;\n"
    '  · wiki {"query":"…"} — справка о людях, местах, понятиях, фактах;\n'
    '  · open_url {"url":"https://…"} — прочитать страницу по ссылке пользователя.\n'
    "- НЕ пиши «нет доступа к интернету / поиску» — сначала вызови news/wiki. Если "
    "инструмент ничего не дал — так и скажи, без выдуманных фактов и ссылок.\n"
    "- ЗАПУСК И ПРАВКИ ПРОЕКТОВ (генерация, перезапуск шага, правка кадров, удаление) этот "
    "чат НЕ делает. Скажи прямо: откройте проект «название» и напишите ту же просьбу "
    "«ИИ-агенту» (кнопка «ИИ-агент» в левой панели) — он покажет, что сделает, и спросит "
    "подтверждение. Не выдумывай кнопки вроде «Сгенерировать». Не делай вид, что запустил.\n"
    "- Факты о состоянии проектов бери только из списка и инструмента project, не угадывай."
)


async def harness_system(base_system: str) -> str:
    lines = [base_system, "", HARNESS_RULES, "", *date_lines(), "", *(await projects_lines())]
    return "\n".join(lines)


class ToolGate:
    """Поток модели: вызов инструмента (JSON) человеку не показываем.

    Пока не ясно, текст это или вызов (ответ начинается с «{» или «```»),
    держим его у себя; обычный текст сразу пропускаем дальше.
    """

    def __init__(self, on_delta: Any | None) -> None:
        self._on = on_delta
        self._held = ""
        self._mode: str | None = None  # None — ещё не ясно, "pass", "hold"

    async def feed(self, delta: str) -> None:
        if not delta or self._on is None:
            return
        if self._mode == "hold":
            self._held += delta
            return
        if self._mode == "pass":
            # Вызов может прийти хвостом после текста: с первой «{» держим до конца.
            i = delta.find("{")
            if i < 0:
                await self._on(delta)
                return
            if i:
                await self._on(delta[:i])
            self._held, self._mode = delta[i:], "hold"
            return
        self._held += delta
        head = self._held.lstrip()
        if not head:
            return
        if head[0] in "{`":
            self._mode = "hold"
            return
        self._mode = "pass"
        held, self._held = self._held, ""
        await self.feed(held)

    async def finish(self, is_tool: bool) -> None:
        if self._on is None or not self._held:
            self._held = ""
            return
        if is_tool:
            # Текст до вызова (если был) уже ушёл; сам JSON не показываем.
            self._held = ""
            return
        held, self._held = self._held, ""
        await self._on(held)


async def ask_with_tools(
    gpt: Any,
    text: str,
    files: list,
    *,
    history: list[dict],
    system: str,
    on_delta: Any | None = None,
    on_phase: Any | None = None,
    **kwargs: Any,
) -> tuple[str, list[dict]]:
    """Вопрос модели с инструментами. Возвращает (ответ, журнал вызовов)."""
    calls: list[dict] = []
    prompt = text
    hist = list(history)
    reply = ""
    for rnd in range(MAX_TOOL_ROUNDS + 1):
        gate = ToolGate(on_delta)
        try:
            reply = await gpt.ask_with_files(
                prompt,
                files,
                history=hist,
                system=system,
                on_delta=gate.feed if on_delta else None,
                **kwargs,
            )
        except Exception as e:
            if "пустой output" not in str(e).lower():
                raise
            # Провайдер иногда отдаёт пустой поток — один повтор, потом честная ошибка.
            logger.warning("chat_harness: empty output, retry once: {}", e)
            gate = ToolGate(on_delta)
            reply = await gpt.ask_with_files(
                prompt,
                files,
                history=hist,
                system=system,
                on_delta=gate.feed if on_delta else None,
                **kwargs,
            )
        call = parse_tool_call(reply or "")
        if call is None or rnd == MAX_TOOL_ROUNDS:
            if call is not None:
                reply = "Не успел собрать ответ за несколько шагов поиска — уточните вопрос, пожалуйста."
                if on_delta:
                    await on_delta(reply)
            else:
                await gate.finish(False)
            break
        await gate.finish(True)
        name, args = call
        if on_phase:
            await on_phase(TOOLS[name][1])
        result = await run_tool(name, args)
        calls.append({"tool": name, "args": args, "result": result[:500]})
        logger.info("chat_harness: tool {} args={} → {} chars", name, args, len(result))
        hist = [*hist, {"role": "user", "content": prompt}, {"role": "assistant", "content": reply}]
        prompt = (
            f"РЕЗУЛЬТАТ ИНСТРУМЕНТА {name}:\n{result}\n\n"
            f"Если данных хватает — сразу дай человеку готовый ответ на его вопрос: «{text}». "
            "Если нужно ещё — ответь ТОЛЬКО JSON следующего вызова, без текста. "
            "Не пиши «проверяю», «ищу», «уточняю запрос» — человек видит только итог."
        )
    return (reply or "").strip(), calls
