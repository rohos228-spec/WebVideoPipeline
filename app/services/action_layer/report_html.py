"""HTML отчётов по нодам и по проектам в виде отчёта «Кадры» (shots_report.py).

Тот же вид: чёрный фон, белые карточки, таблица прогонов нод (Нода | Когда | Итог | Вызовов GPT |
Что сделала программа | Папка прогона), жёлтый блок, дневник «кто пишет одно поле (последний
побеждает)», по ячейкам таблицы с полями. Значения полей — целиком, без сокращений.
Все тексты из данных экранируются; схем и скриптов в отчёте нет.
"""

from __future__ import annotations

import html
from typing import Any

from app.services.action_layer import report_template as tpl
from app.services.action_layer.report_costs import NO_DATA, SOURCE_RU, cost_text

try:
    from app.services.shots_report import REPORT_CSS, _counts_line, _diary_block, _esc, _rules_block
except ImportError:
    REPORT_CSS = "body{font-family:sans-serif;background:#111;color:#eee}"

    def _esc(val: Any) -> str:
        return html.escape(str(val if val is not None else ""))

    def _counts_line(counts: Any) -> str:
        if not isinstance(counts, dict):
            return str(counts or "")
        return ", ".join(f"{k}: {v}" for k, v in counts.items())

    def _diary_block(diary: Any, title: str = "", with_cell: bool = False) -> str:
        return ""

    def _rules_block() -> str:
        return ""

# Поверх общего вида: значения полей с переносами строк как есть.
_EXTRA_CSS = """.val{white-space:pre-wrap;word-break:break-word}
.num{white-space:nowrap}
.muted{color:#888}
.over{color:#8a4b00;font-size:12px}
"""


def _raw(text: Any) -> str:
    """Экранирование без схлопывания пробелов: значение ячейки показываем как записано."""
    return html.escape(str(text if text is not None else ""))


def _money(value: Any, status_ru: Any = "") -> str:
    return cost_text(value, status_ru)


def _usd(value: Any, status_ru: Any = "") -> str:
    """Сумма в тексте: «$0.04», а без суммы — «нет данных» или прочерк без знака доллара."""
    return f"${_money(value, status_ru)}" if value is not None else _money(value, status_ru)


def _anchor(text: Any) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(text or "x"))


def _page(title: str, h1: str, meta_line: str, toc: list[tuple[str, str]], body: str) -> str:
    links = "".join(f'<a href="#{_esc(a)}">{_esc(t)}</a>' for a, t in toc)
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>{_esc(title)}</title>
<style>
{REPORT_CSS}{_EXTRA_CSS}</style></head><body>
<h1>{_esc(h1)}</h1>
<p class=meta>{_esc(meta_line)}</p>
<nav class=toc>{links}</nav>
{body}
</body></html>
"""


def _table(head: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f"<p>{_esc(empty)}</p>"
    ths = "".join(f"<th>{_esc(h)}</th>" for h in head)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def runs_table(runs: list[dict], labels: dict[str, str]) -> str:
    """Последний прогон каждой ноды: те же колонки, что в отчёте «Кадры»."""
    latest: dict[str, dict] = {}
    for run in runs:
        latest[str(run.get("node_key") or "")] = run
    rows = [
        "<tr>"
        f"<td>{_esc(labels.get(nk) or nk)}</td>"
        f"<td>{_esc(run.get('finished') or run.get('started') or '—')}</td>"
        f"<td class=st-{_esc(run.get('status') or '')}>{_esc(run.get('status') or '—')}</td>"
        f"<td>{_esc(run.get('calls') if run.get('calls') is not None else '—')}</td>"
        f"<td>{_esc(_counts_line(run.get('counts') or {}))}</td>"
        f"<td class=path>{_esc(run.get('dir') or '')}</td>"
        "</tr>"
        for nk, run in latest.items()
    ]
    return _table(
        ["Нода", "Когда", "Итог", "Вызовов GPT", "Что сделала программа", "Папка прогона"],
        rows,
        "Дневника прогонов ещё нет: он появится после следующего прогона нод.",
    )


def _box(title: str, inner: str, anchor: str = "") -> str:
    ident = f' id="{_esc(anchor)}"' if anchor else ""
    return f"<section class=runs-box{ident}><h2>{_esc(title)}</h2>{inner}</section>"


def _field_writers(gpt: list[dict]) -> list[tuple[str, list[str]]]:
    """Поле кадра → ноды, которые его пишут, по порядку канваса (последняя побеждает)."""
    order: dict[str, list[str]] = {}
    for n in gpt:
        for name in n.get("fields") or []:
            who = order.setdefault(name, [])
            if n["label"] not in who:
                who.append(n["label"])
    return sorted(order.items(), key=lambda kv: (-len(kv[1]), tpl.field_label(kv[0])))


def _writers_block(gpt: list[dict], with_rules: bool) -> str:
    writers = _field_writers(gpt)
    rows = "".join(
        f"<tr><td>{_esc(tpl.field_label(name))}</td><td>{_esc(' → '.join(who))}</td></tr>"
        for name, who in writers
    )
    table = (
        f"<table><tbody>{rows}</tbody></table>"
        if rows
        else "<p>Ноды «Работа с GPT» в поля кадров ещё не писали.</p>"
    )
    out = (
        "<section class=runs-box><details open><summary>"
        f"{_esc(tpl.HTML_WRITERS_TITLE)}</summary>{table}</details></section>"
    )
    return out + (_rules_block() if with_rules else "")


def _overwrites(gpt: list[dict]) -> dict[str, dict[tuple[str, str], str]]:
    """Нода → (ячейка, поле) → какая нода потом записала то же поле (её значение и осталось)."""
    later: dict[tuple[str, str], str] = {}
    out: dict[str, dict[tuple[str, str], str]] = {}
    for n in reversed(gpt):
        mine = out.setdefault(n["node_key"], {})
        for frame, names in (n.get("frames") or {}).items():
            for name in names:
                key = (str(frame), name)
                if key in later:
                    mine[key] = f"да, потом «{later[key]}»"
        for frame, names in (n.get("frames") or {}).items():
            for name in names:
                later[(str(frame), name)] = n["label"]
    return out


def _gpt_article(
    n: dict, diary: list[dict], overwritten: dict[tuple[str, str], str], show_costs: bool = True
) -> str:
    rows = []
    for frame, names in (n.get("frames") or {}).items():
        values = (n.get("values") or {}).get(str(frame)) or {}
        for name in names:
            rows.append(
                "<tr>"
                f"<td>{_esc(frame)}</td><td>{_esc(tpl.field_label(name))}</td>"
                f"<td class=val>{_raw(values.get(name, ''))}</td>"
                f"<td class=over>{_esc(overwritten.get((str(frame), name), ''))}</td>"
                "</tr>"
            )
    lists = [f"<li>персонаж: {_esc(c)}</li>" for c in n.get("characters") or []]
    lists += [f"<li>сцена: {_esc(s)}</li>" for s in n.get("scenes") or []]
    lists_html = f"<h3>Персонажи и сцены</h3><ul>{''.join(lists)}</ul>" if lists else ""
    what = f"заполнила кадров {n.get('frames_written') or 0}"
    if n.get("fields_ru"):
        what += f": {n['fields_ru']}"
    chain = " · ".join(
        x
        for x in (
            f"модель {n['model']}" if n.get("model") else "",
            f"прочитано: {n['source']}" if n.get("source") else "",
            (
                f"стоимость {_usd(n.get('cost_usd'), n.get('status_ru'))}"
                + (f" ({n['cost_how']})" if n.get("cost_how") else "")
            )
            if show_costs
            else "",
            n.get("note") or "",
        )
        if x
    )
    return (
        f'<article class=scene id="gpt-{_anchor(n["node_key"])}">'
        f"<h2>{_esc(n['label'])} · {_esc(n.get('status_ru') or '—')}</h2>"
        "<div class=scene-action>"
        "<div class=scene-action-label>Промт</div>"
        f"<p>{_esc(n.get('prompt_title') or 'промт по умолчанию')}</p>"
        "<div class=scene-action-label>Что записала</div>"
        f"<p>{_esc(what)}</p>"
        f"<div class=scene-action-chain>{_esc(chain)}</div>"
        "</div>"
        f"{_diary_block(diary, title='Что поменяла программа после ответа GPT', with_cell=True)}"
        f"{lists_html}"
        "<h3>Кадры</h3>"
        + _table(["Ячейка", "Поле", "Что записала", "Перезаписано"], rows, "в кадры нода не писала")
        + "</article>"
    )


def _cell_article(frame: dict, gpt: list[dict]) -> str:
    number = str(frame.get("number"))
    fields: dict[str, list[tuple[str, str]]] = {}
    for n in gpt:
        values = (n.get("values") or {}).get(number) or {}
        for name in (n.get("frames") or {}).get(number) or []:
            fields.setdefault(name, []).append((n["label"], values.get(name, "")))
    rows = []
    for name, chain in fields.items():
        winner, value = chain[-1]
        earlier = "".join(
            f"<div class=over>до этого {_esc(who)}: <span class=val>{_raw(v)}</span></div>"
            for who, v in chain[:-1]
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(tpl.field_label(name))}</td>"
            f"<td class=val>{_raw(value)}{earlier}</td>"
            f"<td>{_esc(' → '.join(who for who, _v in chain))}</td>"
            "</tr>"
        )
    place = frame.get("place") or ""
    return (
        f'<article class=scene id="cell-{_esc(number)}">'
        f"<h2>Ячейка {_esc(number)}" + (f" · {_esc(place)}" if place else "") + "</h2>"
        f"<p class=vo-full><b>Закадр.</b> {_esc(frame.get('voiceover_text') or '—')}</p>"
        + (
            f"<p class=vo-full><b>Промт картинки.</b> {_esc(frame['image_prompt'])}</p>"
            if frame.get("image_prompt")
            else ""
        )
        + (
            f"<p class=vo-full><b>Промт анимации.</b> {_esc(frame['animation_prompt'])}</p>"
            if frame.get("animation_prompt")
            else ""
        )
        + _table(
            ["Поле", "Значение (последняя запись)", "Кто писал по очереди"],
            rows,
            "ноды «Работа с GPT» в эту ячейку не писали",
        )
        + "</article>"
    )


def _cost_rows(costs: dict) -> str:
    by_source = costs.get("by_source") or {}
    rows = [
        f"<tr><td>{_esc(SOURCE_RU.get(src, src or 'без источника'))}</td><td class=num>{_money(usd)}</td></tr>"
        for src, usd in sorted(by_source.items(), key=lambda kv: -abs(kv[1]))
    ]
    total = costs.get("total")
    line = (
        f"<p>Всего по проекту ${_money(total)} (оценка). Разнесено по нодам ${_money(costs.get('attributed'))}, "
        f"не разнесено ${_money(costs.get('unattributed'))}. У ноды без связанных записей стоимость "
        f"«{NO_DATA}»: это не ноль, а отсутствие связи записи трат с нодой.</p>"
        if total is not None
        else "<p>Записей трат по проекту нет.</p>"
    )
    return line + _table(["Откуда траты", "$ (оценка)"], rows, "")


def render_nodes_html(model: dict) -> str:
    """``show_costs=False`` (участник студии, `plan.member_view`): без колонок и раздела трат."""
    pid = model["project_id"]
    show_costs = bool(model.get("show_costs", True))
    gpt = list(model.get("gpt") or [])
    cards = list(model.get("rows") or [])
    frames = list(model.get("frames") or [])
    labels = {r["node_key"]: r["title"] for r in cards}
    labels.update({n["node_key"]: n["label"] for n in gpt})
    node_rows = [
        "<tr>"
        f"<td>{i}</td><td><b>{_esc(r['title'])}</b><div class=muted>{_esc(r['node_key'])}</div></td>"
        f"<td>{_esc(r.get('type_ru'))}</td><td>{_esc(r.get('status_ru'))}</td>"
        f"<td>{_esc(r.get('attempts'))}</td><td>{_esc(r.get('model') or '—')}</td>"
        f"<td>{_esc(r.get('prompt') or '—')}</td><td>{_esc(r.get('checks') or '—')}</td>"
        f"<td>{_esc(r.get('result') or '—')}</td>"
        + (
            f"<td class=num>{_money(r.get('cost_usd'), r.get('status_ru'))}</td>"
            f"<td>{_esc(r.get('cost_how') or '—')}</td>"
            if show_costs
            else ""
        )
        + f"<td>{_esc(r.get('errors') or '—')}</td>"
        "</tr>"
        for i, r in enumerate(cards, start=1)
    ]
    diary = list(model.get("diary") or [])
    cost_head = ["Стоимость, $ (оценка)", "Как посчитана"] if show_costs else []
    body = [
        _box(tpl.HTML_RUNS_TITLE, runs_table(list(model.get("runs") or []), labels), "runs"),
        _box(
            tpl.HTML_NODES_TITLE,
            _table(
                [
                    "№",
                    "Нода",
                    "Тип",
                    "Итог",
                    "Попыток",
                    "Модель",
                    "Промт",
                    "Проверки",
                    "Результат",
                    *cost_head,
                    "Ошибки",
                ],
                node_rows,
                "нод нет",
            ),
            "nodes",
        ),
        _box(tpl.HTML_SPEND_TITLE, _cost_rows(model.get("costs") or {}), "spend") if show_costs else "",
        _writers_block(gpt, bool(model.get("with_rules"))),
    ]
    over = _overwrites(gpt)
    body += [
        _gpt_article(
            n,
            [d for d in diary if d.get("node_key") == n["node_key"]],
            over.get(n["node_key"], {}),
            show_costs,
        )
        for n in gpt
    ]
    if frames:
        body.append(f'<h1 id="cells">{_esc(tpl.HTML_CELLS_TITLE)}</h1>')
        body += [_cell_article(f, gpt) for f in frames]
    toc = [("runs", "Прогоны"), ("nodes", "Ноды")] + ([("spend", "Траты")] if show_costs else [])
    toc += [(f"gpt-{_anchor(n['node_key'])}", n["label"]) for n in gpt]
    toc += [("cells", "Ячейки")] if frames else []
    archived = int(model.get("archived_frames") or 0)
    meta_line = (
        f"проект #{pid} {model.get('slug') or ''} · {model.get('stamp')} · нод {len(cards)} · "
        f"нод «Работа с GPT» {len(gpt)} · ячеек {len(frames)}"
        + (f" (архивных не показано {archived})" if archived else "")
        + "."
    )
    return _page(
        f"{tpl.HTML_NODES_H1} · {model.get('title') or pid}",
        f"{tpl.HTML_NODES_H1} · #{pid} {model.get('title') or ''}".strip(),
        meta_line,
        toc,
        "".join(body),
    )


def _project_article(r: dict, show_costs: bool = True) -> str:
    pid = r["project_id"]
    gpt_rows = [
        "<tr>"
        f"<td>{_esc(n.get('label'))}</td><td>{_esc(n.get('prompt_title') or 'промт по умолчанию')}</td>"
        f"<td>{_esc(n.get('status_ru'))}</td><td>{_esc(n.get('frames_written'))}</td>"
        f"<td>{_esc(n.get('characters_count'))}</td><td>{_esc(n.get('scenes_count'))}</td>"
        + (f"<td class=num>{_money(n.get('cost_usd'), n.get('status_ru'))}</td>" if show_costs else "")
        + f"<td>{_esc(n.get('note') or '—')}</td>"
        "</tr>"
        for n in r.get("gpt_nodes") or []
    ]
    spend = "; ".join(
        f"{SOURCE_RU.get(src, src or 'без источника')} ${_money(usd)}"
        for src, usd in sorted((r.get("spent_by_source") or {}).items(), key=lambda kv: -abs(kv[1]))
    )
    return (
        f'<article class=scene id="project-{_esc(pid)}">'
        f"<h2>#{_esc(pid)} · {_esc(r.get('title'))}</h2>"
        "<div class=scene-action>"
        "<div class=scene-action-label>Статус</div>"
        f"<p>{_esc(r.get('status_ru'))}, шагов готово {_esc(r.get('steps'))}</p>"
        f"<div class=scene-action-chain>ячеек {_esc(r.get('frames'))}, картинок {_esc(r.get('images'))}, "
        f"видео {_esc(r.get('videos'))}"
        + (
            f", траты {_esc(_usd(r.get('spent_usd')))} (оценка)" + (f" · {_esc(spend)}" if spend else "")
            if show_costs
            else ""
        )
        + "</div>"
        "</div>"
        "<h3>Прогоны нод</h3>"
        + runs_table(list(r.get("runs") or []), dict(r.get("labels") or {}))
        + "<h3>Работа с GPT</h3>"
        + _table(
            [
                "Нода",
                "Промт",
                "Итог",
                "Кадров заполнено",
                "Персонажей",
                "Сцен",
                *(["Стоимость, $ (оценка)"] if show_costs else []),
                "Примечание",
            ],
            gpt_rows,
            "нод «Работа с GPT» нет",
        )
        + "</article>"
    )


def render_projects_html(model: dict) -> str:
    """``show_costs=False`` (участник студии, `plan.member_view`): без колонок трат."""
    show_costs = bool(model.get("show_costs", True))
    rows = [r for r in model.get("rows") or [] if r.get("found")]
    summary_rows = [
        "<tr>"
        f'<td>{_esc(r["project_id"])}</td><td><a href="#project-{_esc(r["project_id"])}">{_esc(r["title"])}</a></td>'
        f"<td>{_esc(r['status_ru'])}</td><td>{_esc(r['steps'])}</td><td>{_esc(r['frames'])}</td>"
        f"<td>{_esc(r['images'])}</td><td>{_esc(r['videos'])}</td>"
        + (f"<td class=num>{_money(r.get('spent_usd'))}</td>" if show_costs else "")
        + f"<td>{_esc(r.get('updated_at') or '—')}</td>"
        "</tr>"
        for r in rows
    ]
    gpt_rows = [
        "<tr>"
        f"<td>{_esc(r['project_id'])}</td><td>{_esc(n.get('label'))}</td>"
        f"<td>{_esc(n.get('prompt_title') or 'промт по умолчанию')}</td><td>{_esc(n.get('status_ru'))}</td>"
        f"<td>{_esc(n.get('frames_written'))}</td><td>{_esc(n.get('characters_count'))}</td>"
        f"<td>{_esc(n.get('scenes_count'))}</td>"
        + (f"<td class=num>{_money(n.get('cost_usd'), n.get('status_ru'))}</td>" if show_costs else "")
        + "</tr>"
        for r in rows
        for n in r.get("gpt_nodes") or []
    ]
    body = [
        _box(
            tpl.HTML_PROJECTS_SUMMARY_TITLE,
            _table(
                [
                    "№",
                    "Проект",
                    "Статус",
                    "Шагов готово",
                    "Ячеек",
                    "Картинок",
                    "Видео",
                    *(["Траты, $ (оценка)"] if show_costs else []),
                    "Обновлён",
                ],
                summary_rows,
                "проектов нет",
            ),
            "summary",
        ),
        _box(
            tpl.GPT_SECTION_TITLE,
            _table(
                [
                    "№",
                    "Нода",
                    "Промт",
                    "Итог",
                    "Кадров заполнено",
                    "Персонажей",
                    "Сцен",
                    *(["Стоимость, $ (оценка)"] if show_costs else []),
                ],
                gpt_rows,
                "нод «Работа с GPT» нет",
            ),
            "gpt",
        ),
    ]
    body += [_project_article(r, show_costs) for r in rows]
    s = model.get("summary") or {}
    missing = s.get("missing") or []
    meta_line = (
        f"{model.get('stamp')} · проектов {len(rows)} · ячеек {s.get('frames', 0)} · картинок "
        f"{s.get('images', 0)} · видео {s.get('videos', 0)}"
        + (f" · траты {_esc(_usd(s.get('spent_usd_estimate')))} (оценка)" if show_costs else "")
        + (". Не найдены: " + ", ".join(f"#{p}" for p in missing) if missing else "")
        + "."
    )
    toc = [("summary", "Сводка"), ("gpt", tpl.GPT_SECTION_TITLE)]
    toc += [(f"project-{r['project_id']}", f"#{r['project_id']} {r['title']}") for r in rows]
    return _page(tpl.PROJECTS_TITLE, tpl.HTML_PROJECTS_H1, meta_line, toc, "".join(body))
