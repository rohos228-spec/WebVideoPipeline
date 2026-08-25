"""Проверка промта на соответствие контракту `docs/PROMPT_CONTRACT.md`.

Контракт: источник правды — база, ответ шага — JSON apply-ops. Excel —
экспорт для человека, не вход и не выход модели. Промт, который учит модель
«приложи обновлённый project.xlsx», просит невозможного: книги при учётных
записях не существует, а файла в ответ никто не ждёт.

Контракт лежал в документации, и проверять его было нечем. Так библиотека
пережила пивот нетронутой: код выпилили, 171 промт остался написанным под
старый режим. Найдено 2026-08-26 с вопроса «это что за …?!» на дефолтный
промт шага доработки.

**Проверка построчная и со списком исключений.** Промты законно упоминают
Excel в запретах («не проси project.xlsx»), в метафорах («сцена ≠ одна
колонка Excel») и в картах миграции («было Excel → поле»). Строка с
отрицанием, противопоставлением или стрелкой не считается нарушением. Это
не доказательство чистоты, а дешёвый предохранитель против конкретного класса
ошибки — как и остальные гейты в проекте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Что промт не должен требовать от модели. Границы слов — через lookaround,
#: а не `\b`: `xlsx_valid` и `PLAN_XLSX_OUTPUT_FOOTER` — идентификаторы, не
#: инструкции.
_BANNED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project.xlsx как вход/выход", re.compile(r"project\.xlsx", re.I)),
    ("xlsx как формат ответа", re.compile(r"(?<![\w_])xlsx(?![\w_])", re.I)),
    ("Excel-файл", re.compile(r"excel[- ]файл", re.I)),
    ("приложить файл", re.compile(r"приложи(?!\w)|прилож[её]нн\w* файл|файлом в ответ", re.I)),
    ("TSV `# Лист:` как ответ", re.compile(r"^\s*#\s*Лист:", re.I | re.M)),
    ("сохранить как XLSX", re.compile(r"сохрани\w*\s+(результат\s+)?как\s+\w*\s*xlsx", re.I)),
    ("открыть Excel", re.compile(r"откр\w+\s+excel", re.I)),
)

#: Нерешённые плейсхолдеры. `{{BLOCK:…}}` собирает `prompt_composer`, но
#: папки `prompts/blocks/` нет, а ветка enrich читает мастер сырым — модель
#: получала «{{BLOCK:enrich_role}}» буквально. `{{VAR:EXCEL_GPT_*}}` не
#: подставляет никто.
_PLACEHOLDERS = re.compile(r"\{\{(BLOCK:[a-z0-9_]+|VAR:EXCEL_GPT_[A-Z_]+)\}\}")

#: Строка с этим — запрет, метафора или карта миграции, а не инструкция.
_NEGATION = re.compile(
    r"(?<!\w)(не|нет|ни|нельзя|запрещ\w*|вместо|deprecated|было|стоп)(?!\w)|[→≠]|не\s+в\s+excel",
    re.I,
)


@dataclass(frozen=True)
class Violation:
    line: int
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.line}: [{self.kind}] {self.text}"


def violations(text: str) -> list[Violation]:
    """Список нарушений контракта в тексте промта. Пусто — чисто."""
    out: list[Violation] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        for m in _PLACEHOLDERS.finditer(line):
            out.append(Violation(i, f"нерешённый плейсхолдер {m.group(0)}", line[:100]))
        if _NEGATION.search(line):
            continue
        for kind, pat in _BANNED:
            if pat.search(line):
                out.append(Violation(i, kind, line[:100]))
                break
    return out


def audit_store() -> list[tuple[str, str, list[Violation]]]:
    """Нарушения по всем промтам, поднятым в память. Для предупреждения на старте.

    Именно по кэшу, а не по диску: на сервере диск — только семя, а модель
    читает то, что в базе. Проверять диск там значило бы проверять не то.
    """
    from app.services import prompt_store

    found: list[tuple[str, str, list[Violation]]] = []
    seen: set[str] = set()
    for (_tenant, _brand, _project, step, name), text in prompt_store.entries():
        # Папка excel_gpt делится шестью кодами шагов — один и тот же текст
        # шесть раз в отчёте только мешает читать.
        key = f"{name}\0{hash(text)}"
        if key in seen:
            continue
        seen.add(key)
        bad = violations(text)
        if bad:
            found.append((step, name, bad))
    return found
