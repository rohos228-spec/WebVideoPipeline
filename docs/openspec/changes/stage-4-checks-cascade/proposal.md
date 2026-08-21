# Change: stage-4-checks-cascade

**Дата:** 2026-08-21
**Основание:** WORK_PLAN.md Этап 4 (блоки A-D); спека
`specs/checks-cascade/spec.md` (Req 2-8); карта `system-map.md` §5, §9
(находки 5-11, 19); инфраструктура этапов 5 (`CheckReport`,
`strict_contract`, repair-политика) и 2 (`drop_vision_passed_for_frame`,
input-hash кадра от актуального промпта, `stale/`).

## Why

Петля vision-проверки не сходится и не сдаётся честно: лимит 20 кругов
хардкодом (`vision_check_loop.py:39`) с тихим `return False` и обнулением
счёта (§9#11); до промпта регенерации доезжают 2 оси брака из 10 — и только
для scenes/shot 1 (§9#7, `vision_regen_fix.py:214`,
`vision_check_loop.py:602`); [ok] фиксируется навсегда даже из fail-отчёта,
неупомянутый кадр «принят», гейт перебивает свежий fail старым pass
(§9#5-6); severity-only отчёт без scores проходит автоматом (§9#9);
битый mp4 превращается в сетку стиллов «как будто 8 секунд» и уходит в
платный vision (§9#10, `video_sheet.py:64-86`) — дешёвых пре-чеков перед
LLM нет вообще. Итог для заказчика: платные круги vision, которые не чинят
брак, и брак, который проезжает в финальный ролик принятым.

## Ключевые решения дизайна

### 1. Лимит и сквозной счётчик (блок A)

- Лимит — `settings.vision_check_max_rounds` (env
  `VISION_CHECK_MAX_ROUNDS`, default **2** — запрет владельца из HANDOVER;
  калибровка живыми прогонами — решение заказчика, не код).
- Счётчиков два: `vision_check_round` (текущая петля, как сейчас) и новый
  **сквозной** `meta["vision_rounds_total"]` — dict по ключу check-ноды,
  инкремент на каждом круге, НЕ чистится `clear_vision_check_meta` при
  исчерпании; сбрасывается только при полном pass этой ноды и при явном
  решении оператора. Закрывает «счёт со следующего захода с нуля».
- Исчерпание = **громкая пауза**: `project.status = paused`,
  `meta["pause_reason"] = {code: "vision_rounds_exhausted", node, kind,
  rounds, regen_pending: [...], unverified: [...]}`, уведомление в
  Telegram (тот же канал, что pause у `step_failure_policy`).
  `clear_vision_check_meta` при исчерпании НЕ вызывается — состояние петли
  (regen-цели, счётчик) сохраняется для решения оператора.
- Решение оператора, v1 — ЯВНАЯ точка, не побочный эффект [панель 2/3]:
  обычный ручной перезапуск check-ноды решением НЕ считается и счётчик НЕ
  сбрасывает (безопасная ловушка: recheck выполнится, но regen-круг
  заблокирован лимитом → при fail повторная пауза без платных кругов).
  Решения — web-endpoint `POST /api/projects/{id}/vision-decision`
  (рядом с run_step):
  - `{"action": "more_rounds"}` — сброс `vision_rounds_total[node]`,
    очистка pause_reason, лог решения;
  - `{"action": "accept_pending"}` — `accept_vision_pending_as_operator`:
    текущие regen-цели и unverified → `meta["vision_accepted_by_operator"]`
    (ОТДЕЛЬНЫЙ список, не `vision_check_passed`; токены с namespace по
    kind — `scenes:f3`/`videos:f3`/`hero:c01`, иначе принятие на scenes
    исключило бы одноимённые клипы из recheck videos [панель 1/3]),
    очистка петли и pause_reason, лог.
  Кнопки Telegram/UI — вне сметы; endpoint достаточен для v1.

### 2. Вердикт всех осей → промпт регенерации (блок B)

- Ось `logic` добавляется в шаблон `## scores`
  (`VISION_CHECK_REPORT_HINT`, `check_analysis.py:236-245`) — состав
  усреднения overall перестаёт плавать (§9#8).
- Шаблон issues получает **обязательную привязку к оси и явный токен
  кадра**: `- [critical] f7: (hands) шесть пальцев…`. Парсер осей —
  двухступенчатый: явный тег `(axis)` из шаблона, fallback —
  словарь ключевых слов по осям (руки/пальцы→hands, надпись/watermark→text,
  стиль→style, …) поверх текста issue. НЕ второй парсер отчёта:
  расширяются `extract_vision_issues`/`CheckReport`-слой (issue получает
  поле `axis`), правило этапа 5 соблюдено.
- `vision_regen_fix` обобщается: вместо пары regex (`_MISSING_RE` /
  `_CLONE_RE`) — таблица `AXIS_FIX_BUILDERS`: ось → корректирующий блок в
  `[VISION_FIX]` (характер инструкции — константы в коде + текст issue как
  Reason). character/clones сохраняют текущие, более специфичные билдеры
  (MUST show/NO CLONES) — они богаче генеричных.
- Доставка по kind/shot (снятие трёх ограничений §9#7):
  - scenes shot 1 — как сейчас: ops в `промт_картинки` (hash кадра от
    актуального промпта — этап 2 — инвалидирует PNG автоматически);
  - scenes shot 2 — ops в поле shot2-промпта (`plan_shot2`-атрибут, точное
    имя поля — по факту врезки), фильтр `shot != 1` убирается;
  - videos — ops в `промт_анимации` (`Frame.animation_prompt`;
    `video_input_hash` этапа 2 инвалидирует клип);
  - hero — фикс НЕ пишется в карточку персонажа (данные оператора):
    кладётся в `meta["vision_fix_hero"] = {cid: fix_text}`,
    `generate_hero` подмешивает блок в промпт генерации референса
    (`merge_prompt_with_fix` при сборке), очистка — при pass ноды.
- Агрегация осей per-frame [панель 1/3]: несколько critical-осей одного
  кадра собираются в ОДИН `[VISION_FIX]`-блок (дисциплина текущего
  `_fix_block`: reasons копятся, не last-write-wins); keyword-fallback
  осей — первое совпадение по фиксированному порядку специфичности
  (hands перед pose, text перед style, …).
- Shot2-инвалидация [панель 3/3, вопрос]: `img_input_hash` этапа 2
  хэширует только shot1-промпт — на shot2 «hash инвалидирует PNG
  автоматически» НЕ распространяется; инвалидация shot2-PNG идёт
  deletion-путём петли (`_delete_scene_pngs`, shot==2), тесты B.6
  фиксируют именно его. s2-hash — кандидат роадмапа, не этап 4.
- «Регенерация без изменений запрещена»: перед запуском regen-круга промпт
  каждой цели сравнивается с тем, что было на прошлом круге
  (снимок в meta петли). Байт-в-байт совпадение → `logger.error` +
  счётчик `vision_noop_regen` в meta (метрика несходимости); регенерация
  выполняется (блокировать нельзя — deadlock петли), но событие громкое.
  Perceptual hash результата — роадмап, не этап 4 (per спека).

### 3. Каскад дёшево→дорого (блок C)

`media_probe.py` расширяется (это смета этапа, в модуле сейчас только
`probe_video_size`/`probe_duration`):

- `probe_image(path, expect_aspect)` — PIL: файл читается, ненулевой
  размер, aspect в допуске (±5%), не-чёрный кадр;
- `probe_video(path, expect_aspect)` — ffprobe: читается, duration > 0,
  разрешение/aspect; чёрный клип — по стиллу из СЕРЕДИНЫ клипа (не
  первого кадра — fade-in легален; дешевле blackdetect);
- Порог «чёрного» — почти-абсолютный [панель 3/3, риск ложных
  отбраковок]: mean и максимум яркости у порога кодек-брака (реально
  чёрный вывод), НЕ «тёмная сцена» — легитимная ночная сцена имеет
  света и порог не пробивает. Пороги — константы модуля (v1), при
  ложных срабатываниях на живом прогоне выносятся в конфиг;
- `probe_audio_silence(path)` — ffmpeg volumedetect: mean_volume ниже
  порога = тишина.

Точки врезки (карта §5.3, строки уточнены по текущему дереву):

1. **Check-препроцесс** (`enrich_xlsx.py`, checkMode-блок перед
   `filter_image_paths_for_recheck`/`materialize_video_sheets_for_check`):
   новый `preflight_media_for_check(project, paths, kind)` → пары
   (годные пути, regen-цели с причиной `media_probe:<код>`). Брак не
   уходит в vision: цели вливаются в `scene_check_regen` тем же
   механизмом, что critical из отчёта; если годных файлов нет — vision-вызов
   не делается вовсе, сразу regen. Причина `media_probe:*` пишется в лог и
   в meta петли.
2. **`video_sheet.py`**: `_probe_duration_sec` удаляется, длительность —
   `media_probe.probe_duration`; ошибка пробы = исключение, НЕ 8.0 c
   (§9#10). До нарезки в стиллы клип прогоняется через `probe_video` —
   битый mp4 не превращается в сетку.
3. **Приёмка `generate_images`**: PNG после генерации прогоняется через
   `probe_image` ДО записи Artifact/attrs; провал = провал попытки
   генерации (существующая retry-лестница). Отбракованный файл
   ПЕРЕНОСИТСЯ в `stale/` (инфраструктура `_stash_stale_frame_images`
   этапа 2), НЕ остаётся на месте — иначе «диск = истина» скипнет кадр
   и брак будет принят recovery-путём [панель 1/3, блокер, подтверждён
   docstring `_delete_scene_pngs`].
4. **Приёмка `generate_videos`**: mp4 через `probe_video` ДО Artifact;
   провал = провал попытки (лестница Veo→Kling уже есть); файл — в
   `stale/` (тот же блокер).
5. **Аудио**: клип через `probe_audio_silence` в приёмке
   `generate_audio`; тишина = провал попытки.

Пре-чеки — секунды локального CPU против платного vision-вызова; порядок
каскада гарантирован тем, что probe стоит в приёмке генерации И в
check-препроцессе (двойная сетка, оба дешёвые).

### 4. Семантика принятия (блок D)

Модель принятия становится трёхзначной: **accepted / pending-regen /
unverified** (сейчас двузначная passed/не-passed с fail-open дырами).

- **Конфликт гейта → fail** (§9#5): ветка «keep gate=pass»
  (`vision_check_loop.py:546-554`) переворачивается — при meta pass +
  свежий resolve fail работаем по fail, расхождение логируется. Причина
  существования ветки (schлопывание warn→error при пересборке отчёта)
  устранена этапом 5 (`_finding_tag_for_check` теги не схлопывает).
- **[ok] не навсегда** (§9#6): `mark_ok_tokens_from_reply` вызывается
  ТОЛЬКО при resolved gate = pass (непротиворечивый гейт). В fail-отчёте
  [ok]-строки дают **soft-ok текущего круга**: кадр не регенерится в этом
  круге, но остаётся в recheck. `mark_passed_except_regen` (неупомянутый =
  принят) удаляется: неупомянутые кадры — unverified, идут в следующий
  recheck.
- **Recheck-набор** = все кадры минус accepted (`vision_check_passed` ∪
  `vision_accepted_by_operator`); regen-набор = critical-цели ∪
  `media_probe`-цели. `filter_image_paths_for_recheck` переписывается под
  это правило (сейчас шлёт только regen-цели — модель физически не может
  пересмотреть остальное, §5.2 карты).
- Повторные recheck unverified считаются в тот же сквозной лимит (Req 6
  спеки) — вечного платного recheck нет; на исчерпании pause перечисляет
  unverified отдельно от regen_pending.
- **Severity-only отчёт ≠ pass** (§9#9), два слоя [панель 3/3 —
  raise только в parse-слое]:
  1. Parse-слой: `parse_check_analysis` получает флаг vision-проверки
     (клиент знает: checkMode + изображения во входе). Vision-отчёт без
     `## scores`/overall → `LlmContractError(kind="validate")` внутри
     parse — попадает в существующий repair-цикл
     (`gpt_operator_client.py:492-525`). НЕ generic-путь: обычные
     check-ноды без scores легитимны, их флаг не трогает.
  2. Петля (defense in depth, НЕ бросает): в
     `maybe_start_vision_check_loop_after_check` отчёт без overall и без
     critical → гейт трактуется fail (лог «severity-only — консервативный
     fail»), не pass. Вызов `resolve_vision_check_gate` в петле остаётся
     не-бросающим — raise там вылетел бы мимо repair и уронил петлю.
  Generic-дефолт `resolve_vision_check_gate` (overall None + нет critical
  → pass) НЕ меняется: под _VISION_SEV_TAG_RE попадают и обычные
  отчёты с [error]/[ok] — глобальный флип уронил бы все check-ноды.
- **Инвалидация принятого** [панель 2/3, блокер]:
  `drop_vision_passed_for_frame` (этап 2) РАСШИРЯЕТСЯ — чистит токены
  кадра и в `vision_check_passed`, и в `vision_accepted_by_operator`
  (оба kind-namespace). Иначе: оператор принял кадр → промпт изменён →
  hash снят → кадр перегенерён, но исключён из recheck навсегда.
- **Critical без адресуемых целей** [панель 1/3, gap]: если после
  ужесточения regex у critical-issues не извлеклось ни одной цели (и
  prose-fallback пуст): петля активна → повтор текущих pending
  (существующая ветка scored_no_critical); петля не активна → цикл не
  стартует, WARNING «critical без адресуемых целей», гейт остаётся fail
  (оператор видит failed gate). Молчаливого pass нет.
- **Regen-цели без ложных срабатываний** (Req 8, §9#12): голый
  `\b(\d{1,4})\b` (`check_analysis.py:907`) заменяется на явные токены:
  `f7`/`7s2`/`frame_007`/имена файлов; голое число — только вплотную к
  слову кадр/frame («кадр 7»), не «в радиусе строки». Сценарий спеки
  «на фоне 3 фигуры» перестаёт рожать цель «кадр 3».
- Этап 2 совместимость: `Frame.attrs[*_input_hash]` не трогается;
  `drop_vision_passed_for_frame` расширяется (см. выше), сигнатура и
  вызовы этапа 2 сохраняются.

### 5. §9#19 — минимально, без унификации К2/К3

Унификация К2/К3 — вне сметы (спека as-is). Этап 4 делает только гигиену:
warning на старте, если каталоги промптов К2/К3 отсутствуют (чистый клон),
чтобы «мёртвый контур» был виден, а не молчал. `auto_review.review_image`
не реанимируется.

## Границы (что НЕ делаем)

- `check_analysis.py` не перерабатывается (1587 строк, 52 регулярки —
  роадмап): врезки точечные — шаблон HINT, `extract_vision_issues` (+axis),
  regen-regex, `resolve_vision_check_gate`.
- Контуры К2 (`auto_review`) и К3 (`gpt_verdict_review`), xlsx-слой,
  `post_step_validate`-валидаторы (К4) — не переписываются; probe встаёт
  рядом с существующей приёмкой, не вместо неё.
- Perceptual hash результата регенерации — роадмап.
- Промпты под .gitignore в git не заносятся (собственность заказчика).

## What Changes

- **A. Лимит/пауза**: `app/settings.py` (+`VISION_CHECK_MAX_ROUNDS`),
  `vision_check_loop.py` (счётчики, пауза с причиной, снятие «тихого
  return False»), уведомление Telegram, операторские решения v1.
- **B. Оси**: `check_analysis.py` (шаблон + axis у issues),
  `vision_regen_fix.py` (AXIS_FIX_BUILDERS, снятие shot1/scenes-фильтров),
  `vision_check_loop.py` (авто-фикс для hero/videos/shot2),
  `generate_hero` (подмешивание `vision_fix_hero`), детект noop-regen.
- **C. Каскад**: `media_probe.py` (+3 пробы), `video_sheet.py` (дубль
  probe удалён), `enrich_xlsx.py` (preflight перед vision),
  `generate_images.py` / `generate_videos.py` / `generate_audio.py`
  (probe в приёмке).
- **D. Принятие**: `vision_check_loop.py` (гейт-конфликт, recheck-набор,
  удаление mark_passed_except_regen), `check_analysis.py` (severity-only,
  regen-regex), strict-опт-ин на vision-вызове К1.
- Тесты: обновление фиксирующих старое поведение (с комментарием
  «Этап 4: …»), новые юниты на каждую семантику.

## Impact

- Affected specs: `checks-cascade` (дельта: MODIFIED Req 2/6/7 —
  уточнение механики счётчика, soft-ok, strict-пути; Req 5 — уточнение
  точек врезки).
- Affected code: `app/services/{vision_check_loop, check_analysis,
  vision_regen_fix, media_probe, video_sheet}.py`,
  `app/orchestrator/steps/{enrich_xlsx, generate_images, generate_videos,
  generate_audio, generate_hero}.py`, `app/settings.py`, тесты.
- Поведенческий риск 1: recheck-набор расширяется (unverified и soft-ok
  кадры пересматриваются) — vision-вызовы за круг дороже, но кругов ≤2 и
  брак отсеивается probe'ом до vision; честная цена против fail-open.
- Поведенческий риск 2: лимит 20→2 — проекты, которые «сходились» на
  5-м круге, начнут паузиться; это ожидаемо (запрет владельца), порог —
  конфиг.
- Поведенческий риск 3: probe в приёмке генерации начнёт отбраковывать
  файлы, которые раньше принимались (чёрные кадры, битые клипы) —
  больше ретраев генерации, меньше брака ниже по конвейеру.
- Поведенческий риск 4 [панель 3/3 — требует решения заказчика]: при
  «accepted только из полного pass» recheck-набор не сжимается на
  fail-кругах → при лимите 2 **pause становится штатным исходом
  несходимости**, а «принять как есть» отдаёт оператору все unverified
  скопом. Это осознанная цена спеки (галлюцинация [ok] больше не
  принимает брак навсегда); смягчения: блок B реально меняет промпты
  (шанс сходимости растёт), probe отсекает брак до vision, порог —
  конфиг (заказчик может поднять после живых прогонов). Вопрос вынесен
  в приёмку этапа.

## Панель-ревью (2026-08-21)

3 голоса из 4 (kimi-k2.6 — http 000 на таймауте); сырые рецензии —
`~/.agents/var/panel/2026-08-21__18-58-42/`. 2 блокера закрыты правками
этого proposal (маркеры [панель N/3]): инвалидация
`vision_accepted_by_operator` через расширение
`drop_vision_passed_for_frame`; отбракованный probe'ом файл — в `stale/`.
Риски врезаны: raise severity-only только в parse-слое; почти-абсолютный
порог чёрного + стилл из середины; kind-namespace токенов accepted;
явная точка операторского решения (endpoint, рестарт ≠ решение); gap
«critical без целей» — определено поведение. Риск «pause — штатный
исход» — поведенческий риск 4, решение заказчика. Отброшено панелью и
проверкой по коду: «фикс hero не применится» (петля форсирует regen по
regen_ids — `generate_hero.py:976-1011`), «этапов 5/2 не существует»,
претензии к ссылкам/деталировке задач.

## Приёмка (критерий этапа, WORK_PLAN)

Живой прогон (Chattiq): (1) регенерация сходится за ≤2 круга ЛИБО проект
в paused с машиночитаемой причиной и перечнем кадров; после паузы счёт не
обнуляется сам. (2) Подсунутый битый mp4 / чёрный PNG отсекается
probe-каскадом ДО vision-вызова (по логам: `media_probe:*` есть,
vision-вызова по этому файлу нет). (3) critical по оси hands/text/style
меняет промпт регенерации кадра (диф промпта в логах), включая shot 2,
hero и videos. Зелёные юнит-тесты — необходимое, не достаточное
(harness-гейт в тестах выключен).
