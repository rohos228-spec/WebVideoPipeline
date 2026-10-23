"""Сервис Мета-Агента: компиляция отказоустойчивых системных мастер-промптов.

Принимает человеческое описание задачи (на русском или английском) и категорию
ноды (step_code) -> возвращает готовый, проверенный системный .md промпт с
железобетонным соблюдением контракта ноды, официальных плейсхолдеров и защиты от галлюцинаций.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.services.gpt_client import get_gpt_client

STEP_ALIASES: dict[str, str] = {
    "plan": "plan",
    "01_plan": "plan",
    "script": "script",
    "02_script": "script",
    "split": "split",
    "03_razbivka": "split",
    "hero": "hero",
    "04_hero": "hero",
    "hero_style": "hero_style",
    "04_hero_style": "hero_style",
    "items": "items",
    "04b_items": "items",
    "excel_gpt": "excel_gpt",
    "05_excel_gpt": "excel_gpt",
    "enrich_1": "excel_gpt",
    "05a_enrich_1": "excel_gpt",
    "enrich_2": "excel_gpt",
    "05b_enrich_2": "excel_gpt",
    "enrich_3": "excel_gpt",
    "05c_enrich_3": "excel_gpt",
    "enrich_4": "excel_gpt",
    "05d_enrich_4": "excel_gpt",
    "enrich_5": "excel_gpt",
    "05e_enrich_5": "excel_gpt",
    "img_pr": "img_pr",
    "05_image_prompts": "img_pr",
    "06_image_prompts": "img_pr",
    "image_prompts": "img_pr",
    "anim_pr": "anim_pr",
    "07_animation": "anim_pr",
    "animation": "anim_pr",
    "music": "music",
    "10_music": "music",
    "scene_d": "scene_d",
    "scene_asm": "scene_d",
    "scene_design": "scene_d",
}


def normalize_step_code(step_code: str) -> str:
    """Приводит переданный код шага или имя папки к каноническому ключу."""
    raw = (step_code or "").strip().lower()
    return STEP_ALIASES.get(raw, raw or "excel_gpt")


META_AGENT_SYSTEM_PROMPT = """Ты — ведущий AI Prompt Architect и Compiler для продакшн-системы Video Pipeline Studio.

Твоя задача: взять пользовательскую идею/задачу на естественном языке или готовый авторский текст и скомпилировать из него ПРОФЕССИОНАЛЬНЫЙ, ОТКАЗОУСТОЙЧИВЫЙ СИСТЕМНЫЙ МАСТЕР-ПРОМПТ (.md) ДЛЯ КОНКРЕТНОЙ НОДЫ ПАЙПЛАЙНА.

В НАШЕМ ПАЙПЛАЙНЕ РАЗНЫЕ НОДЫ ИМЕЮТ ПРИНЦИПИАЛЬНО РАЗНЫЕ ТЕХНИЧЕСКИЕ КОНТРАКТЫ ВЫХОДА:
- Нода «Закадровый текст» (02_script / script): выходом является ЧИСТЫЙ СВЯЗНЫЙ ТЕКСТ ГОЛОСА ОЗВУЧКИ (TTS) внутри маркеров <<<VOICEOVER>>> ... <<<END>>>, а НЕ JSON с массивом сцен!
- Нода «Стиль героя» (04_hero_style) и «Музыка» (10_music): выходом являются текстовые стилевые промпты для генераторов, а НЕ JSON!
- Нода «Лист героя» (04_hero) и «Предметы» (04b_items): выходом является детальный английский промпт модельного листа 16:9, а НЕ JSON!
- Ноды базы данных («Разбивка» 03_razbivka, «Excel/DB» 05_excel_gpt, «Анимация» 07_animation): выходом является СТРОГИЙ JSON apply-ops без единого лишнего слова прозы!

СТРОЖАЙШИЙ ЗАПРЕТ НА ВЫДУМЫВАНИЕ ПЛЕЙСХОЛДЕРОВ:
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать несуществующие технические переменные, такие как: {{WORDS_PER_SEC}}, {{SCENE_TIMINGS_SEC}}, {{TARGET_DURATION_SEC}}, {{USER_INTENT}}, {{LANGUAGE}} и подобные фантазии. Их нет в рантайме пайплайна!
- Используй ТОЛЬКО РЕАЛЬНЫЕ плейсхолдеры, прямо указанные в спецификации целевой ноды (например, {{TOPIC}} или [ВСТАВЬ ТЕМУ], [character_description], [item_description], frame_uuid, voiceover).

СТРОГИЕ ПРАВИЛА КОМПИЛЯЦИИ:
1. Выдавай ТОЛЬКО готовый текст промпта в формате Markdown (.md). Никаких вводных слов ("Вот ваш промпт:", "Конечно, держите"), никаких заключительных рассуждений.
2. Промпт должен содержать чёткие секции:
   - РОЛЬ И ЦЕЛЬ (SYSTEM ROLE & OBJECTIVE)
   - ВХОДНЫЕ ДАННЫЕ И ПЛЕЙСХОЛДЕРЫ (INPUT CONTEXT)
   - СТРОГИЙ КОНТРАКТ ВЫХОДА (OUTPUT CONTRACT: либо чистый текст TTS в <<<VOICEOVER>>>, либо JSON apply-ops, либо промпт генератора — СТРОГО по спецификации ноды!)
   - АНТИ-ГАЛЛЮЦИНАЦИИ И ЗАПРЕТЫ (STRICT CONSTRAINTS & NO-CHAT GUARD)
   - 1-2 ЭТАЛОННЫХ ПРИМЕРА ВХОД -> ВЫХОД (FEW-SHOT EXAMPLES)
3. Язык инструкций промпта: русский язык для инструкций логики и сценария; английский язык для промптов генераторов (Midjourney, Outsee, Veo, Kling, Suno).
4. Режим No-Chat Guard: всегда требуй от будущей модели выдавать строго запрошенный результат без приветствий, извинений и рефлексии.
5. ЛАКОНИЧНОСТЬ И ТОЧНОСТЬ: составляй ёмкий, сфокусированный и компактный системный мастер-промпт. Не раздувай Few-Shot примеры на сотни строк — используй 1-2 кратких примера.
6. РЕЖИМ ТРАНСЛЯЦИИ / АДАПТАЦИИ АВТОРСКОГО ПРОМПТА: Если пользователь передал готовый текст или авторскую инструкцию — сохрани 100% его творческой мысли, сюжета, юмора, персонажей и правил, но безупречно приведи промпт к техническому контракту и ограничениям целевой ноды.
7. АВТО-СЛАГ ИМЕНИ (SLUG): В самой первой строке ответа выдай служебную метку с кратким именем файла на английском в snake_case (до 30 символов), отражающим задачу: <!-- slug: short_name_here -->
"""

NODE_SPECIFIC_GUIDELINES: dict[str, str] = {
    "script": """
СПЕЦИФИКА НОДЫ: «Закадровый текст / Voiceover Script» (02_script / script)
- РОЛЬ: Автор и диктор живого вирусного закадрового текста для озвучки (TTS).
- ВХОДНЫЕ ДАННЫЕ: Тема ролика `{{TOPIC}}` (или `[ВСТАВЬ ТЕМУ]`) и контекст сценария (general_plan.txt).
- ФОРМАТ ВЫХОДА (КРИТИЧНО!):
  Выходом является ЧИСТЫЙ СВЯЗНЫЙ ДИКТОРСКИЙ ТЕКСТ.
  Весь закадровый текст ОБЯЗАТЕЛЬНО оборачивается строго в маркеры:
  <<<VOICEOVER>>>
  ...только чистый текст дикторской озвучки...
  <<<END>>>
  (Либо валидный JSON apply-ops: `{"ops":[{"target":"project","fields":{"закадровый_текст":"..."}}]}`).
- СТРОЖАЙШИЙ ЗАПРЕТ:
  * НЕЛЬЗЯ оборачивать результат в кастомный JSON с массивом `scenes: [...]`! Это сломает импорт озвучки в пайплайн.
  * НЕЛЬЗЯ выдумывать переменные вроде `{{WORDS_PER_SEC}}`, `{{SCENE_TIMINGS_SEC}}`.
- ЖЕЛЕЗНЫЕ ПРАВИЛА ТЕКСТА ДЛЯ ОЗВУЧКИ (TTS):
  1. ЧИСЛА, ДАТЫ, ГОДЫ, ПРОЦЕНТЫ, СУММЫ — ТОЛЬКО СЛОВАМИ (ПРОПИСЬЮ)!
     Категорически запрещены цифры: «в две тысячи двадцать шестом году» (НЕ 2026), «сто восемьдесят квадратных метров» (НЕ 180 м²), «пятьдесят процентов» (НЕ 50%). Цифры ломают нейросетевой голос TTS.
  2. ЧИСТАЯ РЕЧЬ ДИКТОРА БЕЗ РЕМАРОК:
     Никаких пометок в тексте: запрещены `[Музыка]`, `(пауза)`, `Кадр 1:`, `Диктор:`, `[Видеоряд]`.
  3. ХРОНОМЕТРАЖ И ПЛОТНОСТЬ:
     Темп речи ~14 знаков в секунду (для 15 сек ≈ 210 знаков, для 60 сек ≈ 850–1000 знаков). Текст должен содержать не менее 200 символов связного повествования.
""",
    "plan": """
СПЕЦИФИКА НОДЫ: «Сценарий / План ролика» (01_plan / plan)
- РОЛЬ: Исследователь, редактор и сценарист насыщенного ролика.
- ВХОДНЫЕ ДАННЫЕ: Тема ролика `{{TOPIC}}` (или `[ВСТАВЬ ТЕМУ]`), вводные пожелания пользователя.
- ФОРМАТ ВЫХОДА:
  Структурированный 5-актный нарративный план (Хук, Завязка, Развитие/Интрига, Кульминация, Финал) с таймингами сцен,
  ИЛИ JSON apply-ops:
  ```json
  {"ops":[{"target":"project","fields":{"общий_план":"Хук: ...\\nЗавязка: ...\\nРазвитие: ...\\nКульминация: ...\\nФинал: ..."}}]}
  ```
- ТРЕБОВАНИЯ:
  * Плотная событийная драматургия: конкретные события, факты, решения участников, причины и следствия.
  * Исключать пустую воду («важный», «непростой», общие морализаторские выводы).
""",
    "split": """
СПЕЦИФИКА НОДЫ: «Разбивка на блоки / кадры» (03_razbivka / split)
- РОЛЬ: Монтажёр закадра. Разрезает полный закадровый текст на ритмичные кадры (ячейки).
- ВХОДНЫЕ ДАННЫЕ: Полный закадровый текст (`voiceover.txt`) и сценарий (`general_plan.txt`).
- ФОРМАТ ВЫХОДА (ТОЛЬКО JSON apply-ops, без единого слова прозы):
  ```json
  {"ops":[{"target":"replace_frames","frames":[
    {"закадр":"первая законченная фраза","длительность":3.0},
    {"закадр":"вторая законченная фраза","длительность":2.5}
  ]}]}
  ```
- ГЛАВНЫЕ ПРАВИЛА:
  1. ПОЛНОЕ ДОСЛОВНОЕ ПОКРЫТИЕ: склейка всех полей `закадр` обязана ДОСЛОВНО, слово в слово совпадать со всем входящим текстом закадра. Ничего не сокращать, не заменять и не выдумывать!
  2. Резать по смыслу (одна мысль / клауза / законченная фраза = 1 кадр), а не пополам.
  3. Количество кадров: должно быть не менее 2 кадров (обычно 10–20 кадров на 60 сек, либо строго по числу сцен сценария).
""",
    "excel_gpt": """
СПЕЦИФИКА НОДЫ: «Работа с GPT / Анализ Excel и БД» (05_excel_gpt / enrich_1..5)
- РОЛЬ: Редактор атрибутов кадров в базе данных проекта (DB SoT).
- ВХОДНЫЕ ДАННЫЕ: Снимок кадров `db_frames.json` (массив кадров с `frame_uuid`, `number`, `закадр`, `attrs`, реестр `characters[]`).
- ФОРМАТ ВЫХОДА (ТОЛЬКО JSON apply-ops):
  ```json
  {
    "ops": [
      {
        "frame_uuid": "<uuid кадра из входа>",
        "fields": {
          "место": "название локации",
          "акцент": "главный визуальный фокус",
          "главное_действие": "действие персонажа или камеры",
          "смысл_сцены": "смысловой посыл кадра"
        }
      }
    ],
    "report": "Кратко: что обновлено и почему"
  }
  ```
- ПОДДЕРЖИВАЕМЫЕ ПОЛЯ (русские или английские ключи):
  `место` (`place`), `акцент` (`accent`), `главное_действие` (`main_action`), `смысл_сцены` (`scene_sense`), `тип_сцены` (`visual_type`), `особенность_сцены` (`scene_feature`), `фон` (`shot01_bg`), `предметы` (`shot01_props`), `логика_перехода` (`shot01_transition`).
- ОГРАНИЧЕНИЯ:
  * Адрес кадра — строго существующий `frame_uuid` из входа.
  * Персонажи — только существующие ID из реестра `characters[]` (например, `c01`).
  * Не переписывать закадр, если он уже заполнен.
""",
    "hero_style": """
СПЕЦИФИКА НОДЫ: «Стиль персонажа / Hero Style» (04_hero_style)
- РОЛЬ: Мастер визуального стиля для генераторов персонажей (Outsee / Nano Banana 2 / Midjourney).
- ВХОДНОЙ ПЛЕЙСХОЛДЕР: Обязательно плейсхолдер `[character_description]`.
- ФОРМАТ ВЫХОДА:
  Ёмкий, высокодетализированный художественный промпт на английском языке (НЕ JSON!).
- ОБЯЗАТЕЛЬНЫЕ КОМПОНЕНТЫ:
  * Оптика и камера: 85mm portrait lens, f/1.8, shallow depth of field, sharp facial focus.
  * Кинематографический свет: dramatic rim lighting, soft diffuse key light, volumetric chiaroscuro.
  * Текстуры и материалы: realistic skin pore texture, authentic fabric weave, 8k resolution, subsurface scattering.
  * Фон: clean neutral solid studio background, studio lighting setup.
""",
    "hero": """
СПЕЦИФИКА НОДЫ: «Лист персонажа / Hero Turnaround Sheet» (04_hero)
- РОЛЬ: Генератор эталонного модельного листа персонажа (16:9 Model / Turnaround Sheet).
- ВХОДНОЙ ПЛЕЙСХОЛДЕР: Обязательно `[character_description]` или блоки `Character:` и `Outfit:`.
- ФОРМАТ ВЫХОДА:
  Детальный мастер-промпт на английском языке в формате 16:9 (НЕ JSON!).
- СТРОГАЯ КОМПОЗИЦИЯ СЕТКИ (16:9 GRID):
  * Слева (85–90% высоты): 4 ростовые ортографические проекции ОДНОГО героя (full body front view, full body 3/4 front view, full body side profile view, full body back view).
  * Справа вверху: этюды головы и лица с разных углов (front, 3/4 front, side, back).
  * Справа внизу: детальные этюды одежды, обуви и аксессуаров.
  * Фон: pure solid white background (#FFFFFF), никаких теней на полу, без окружения.
  * СТРОЖАЙШИЙ ЗАПРЕТ: никаких текстов, надписей, цифр, логотипов, рамок и посторонних лиц.
""",
    "items": """
СПЕЦИФИКА НОДЫ: «Предметы / Items / Props Reference» (04b_items)
- РОЛЬ: Генератор концепт-арта ключевого предмета/реквизита в формате 16:9.
- ВХОДНОЙ ПЛЕЙСХОЛДЕР: Обязательно плейсхолдер `[item_description]`.
- ФОРМАТ ВЫХОДА:
  Английский промпт концепт-арта изолированного объекта в формате 16:9 (НЕ JSON!).
- СТРОГИЕ ТРЕБОВАНИЯ:
  * Ракурс: Three-quarter dynamic isometric studio angle (3/4 perspective), 65–75% площади кадра.
  * Фон: нейтральный чистый студийный фон (#1a1a1e или #FFFFFF) с мягкой контактной тенью под предметом.
  * СТРОГИЙ ЗАПРЕТ (NEGATIVE PROMPT): никаких людей, рук, держащих предмет, манекенов, столов, комнатного окружения, текста, водяных знаков и логотипов.
""",
    "img_pr": """
СПЕЦИФИКА НОДЫ: «Промпты кадров / Image Prompts» (05_image_prompts / img_pr)
- РОЛЬ: Генератор законченных промптов для генератора изображений по каждому кадру.
- ВХОДНЫЕ ДАННЫЕ: Карточка кадра (закадр, смысл, место, персонажи, шот из дизайна сцен).
- ФОРМАТ ВЫХОДА:
  Либо промпт по Грамматике сцены (Референс персонажа c01, Место, Фон, Расстановка continuity, План-ракурс, Действие в движении, Свет, Эмоция, Акцент, Смысл, Детали) + STYLE lock + Negative.
  Либо строгий JSON apply-ops:
  ```json
  {"ops":[{"frame_uuid":"<uuid>","fields":{"промт_картинки":"...детальный визуальный промпт..."}}]}
  ```
""",
    "anim_pr": """
СПЕЦИФИКА НОДЫ: «Промпты анимации / I2V Animation Prompts» (07_animation / anim_pr)
- РОЛЬ: Режиссёр движения камеры и динамики кадра для современных I2V-моделей (Google Veo 3.1, Kling 2.6 / 3.0, Seedance 2.5).
- ФОРМАТ ВЫХОДА (ТОЛЬКО JSON apply-ops):
  ```json
  {
    "ops": [
      {
        "frame_uuid": "<uuid кадра из входа>",
        "fields": {
          "промт_видео": "smooth slow dolly in, subject turns head naturally, subtle background parallax, cinematic lighting, silent, NO VOICE, no dialogue, no lip-sync"
        }
      }
    ]
  }
  ```
- ЖЕЛЕЗНЫЕ ПРАВИЛА АНИМАЦИИ:
  1. ЯЗЫК ПРОМПТА ДВИЖЕНИЯ — СТРОГО АНГЛИЙСКИЙ (English only).
  2. КРИТИЧНО — ВСЕГДА `NO VOICE`: в каждом промпте явно указывать `NO VOICE` / `silent` / `no speech` / `no dialogue` / `mouth closed`.
  3. Непрерывный кадр (single continuous take): плавное кинематографичное движение камеры (smooth dolly in, slow pan, subtle parallax) без резких склеек и смены планов.
""",
    "music": """
СПЕЦИФИКА НОДЫ: «Фоновая музыка / Suno V5.5 Prompt» (10_music / music)
- РОЛЬ: Саундтрек-супервайзер и промпт-инженер фоновой музыки для Suno V5.5.
- ВХОДНЫЕ ДАННЫЕ: Тема ролика и текст озвучки (voiceover.txt).
- ФОРМАТ ВЫХОДА:
  ТОЛЬКО готовые теги на английском языке через запятую в одну строку (от 150 до 350 символов), БЕЗ JSON, без вводных слов и кавычек.
- ОБЯЗАТЕЛЬНАЯ СТРУКТУРА:
  Жанр и стиль, ключевые инструменты, настроение и динамика, темп (BPM), в конце ОБЯЗАТЕЛЬНО: `instrumental, background music, no vocals`.
""",
    "scene_d": """
СПЕЦИФИКА НОДЫ: «Дизайн сцен и сквозное действие» (scene_design / scene_d)
- РОЛЬ: Постановщик сквозного действия и визуальной преемственности между кадрами (continuity).
- ВХОДНЫЕ ДАННЫЕ: Сценарий, разбивка на кадры и реестр персонажей.
- ФОРМАТ ВЫХОДА:
  JSON со слоями кадра (персонажи, локация, освещение, сквозное действие match-on-action, ракурс).
- ПРАВИЛА: Учитывать continuity между соседними кадрами, сохранять позы и векторы движения.
""",
}


def sanitize_compiled_prompt(raw_text: str) -> str:
    """Удаляет мусорные обертки Markdown или вводные реплики LLM."""
    text = (raw_text or "").strip()
    if text.startswith("```markdown") and text.endswith("```"):
        text = text[len("```markdown") : -3].strip()
    elif text.startswith("```md") and text.endswith("```"):
        text = text[len("```md") : -3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    return text


async def compile_meta_prompt(
    step_code: str,
    user_intent: str,
    *,
    project_topic: str = "",
    target_name: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Генерирует системный промпт через LLM на основе пользовательского намерения."""
    canonical_step = normalize_step_code(step_code)
    guideline = NODE_SPECIFIC_GUIDELINES.get(
        canonical_step,
        NODE_SPECIFIC_GUIDELINES.get("excel_gpt", ""),
    )

    clean_intent = (user_intent or "").strip()
    if not clean_intent:
        clean_intent = "Создай эталонный сбалансированный мастер-промпт высокого качества."

    user_message = f"""СКОМПИЛИРУЙ СИСТЕМНЫЙ ПРОМПТ ДЛЯ НОДЫ: `{canonical_step}` (исходный код: `{step_code}`)

НАЗВАНИЕ/ПРЕСЕТ: {target_name or "custom_preset"}
ТЕМА ПРОЕКТА: {project_topic or "Не указана"}
ПОЛЬЗОВАТЕЛЬСКАЯ ЗАДАЧА / НАМЕРЕНИЕ / АВТОРСКИЙ ТЕКСТ:
\"\"\"{clean_intent}\"\"\"

{guideline}

Выдай готовый .md промпт строго соблюдая контракт ноды:
"""

    gpt = get_gpt_client()
    logger.info(
        "meta_agent: компиляция промпта для step={} (canonical={}) intent='{}'...",
        step_code,
        canonical_step,
        clean_intent[:50],
    )

    full_query = f"{META_AGENT_SYSTEM_PROMPT}\n\n---\n\n{user_message}"
    raw_response = await gpt.ask_fresh(full_query, timeout=120.0)

    compiled_text = sanitize_compiled_prompt(raw_response)
    if not compiled_text:
        raise RuntimeError("Мета-Агент вернул пустой результат компиляции.")

    # Авто-извлечение slug
    suggested_name = (target_name or "").strip()
    slug_match = re.search(r"<!--\s*slug:\s*([a-zA-Z0-9_-]+)\s*-->", compiled_text, re.IGNORECASE)
    if slug_match:
        extracted = slug_match.group(1).strip().lower()
        if not suggested_name or suggested_name in ("custom_preset", "custom_agent_prompt"):
            suggested_name = extracted
        compiled_text = re.sub(r"<!--\s*slug:\s*[a-zA-Z0-9_-]+\s*-->\s*", "", compiled_text).strip()

    if not suggested_name or suggested_name in ("custom_preset", "custom_agent_prompt"):
        suggested_name = f"{canonical_step}_custom"

    # Быстрый анализ сгенерированного промпта
    has_json_schema = "json" in compiled_text.lower() or "{" in compiled_text
    has_guards = any(
        w in compiled_text.lower()
        for w in ["strict", "constraint", "no-chat", "запрещено", "only", "voiceover", "числа только прописью"]
    )

    return {
        "step_code": canonical_step,
        "name": suggested_name,
        "compiled_prompt": compiled_text,
        "stats": {
            "length_chars": len(compiled_text),
            "lines_count": len(compiled_text.splitlines()),
            "has_schema": has_json_schema,
            "has_guards": has_guards,
        },
    }
