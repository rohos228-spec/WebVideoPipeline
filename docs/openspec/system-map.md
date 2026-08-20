# Карта системы video-pipeline

Дата: 2026-08-20. HEAD: 3bbf5c3 (история squash-нута, видимая глубина ~8 дней).
Метод: 3 независимых прохода по коду (оркестратор / LLM-агенты и контракты /
проверки и медиа) поверх 4-проходного аудита. Все утверждения — со ссылками
file:line на текущем HEAD.

Deliverable этапа 1 (WORK_PLAN.md): карта логических агентов, фактические
вход/выход, где ломается. Ожидалось 15 агентов — фактически **25 логических
агентов** (A1–A25, §4), из них 9 — ядро P0.

---

## 1. Конвейер одним взглядом

```
topic → planning → scripting → splitting → [scene_designing → scene_assembling]
      → generating_hero → generating_items → enriching_1..5
      → generating_image_prompts → generating_images → generating_animation_prompts
      → generating_videos → generating_audio → generating_music
      → [sfx_planning → generating_sfx] → assembling → publishing
```

Состояние всего проекта — один enum `Project.status` (`app/models.py:19-73`),
пары running/ready на каждый шаг. Канонический порядок достижимости — НЕ enum,
а `_STATUS_ORDER` в `app/telegram/menu.py:99-147`.

Терминальные: `paused` (models.py:72), `failed` (:73 — deprecated, см.
`app/main.py:272-278`).

Ветвления:
- `frames_ready` → `generating_hero` (`auto_advance.py:117-119`) либо
  `scene_designing` при включённом scene_design (:114-116, :266-274).
- `scene_designing` → `scene_agents_ready` только при полном веере агентов;
  иначе откат в `frames_ready` (`steps/scene_design.py:154-166`).
- `hero_ready` → `generating_items`; пустые items → сразу `items_ready`
  (`steps/generate_items.py:93-100`).
- `enrich_N_ready` → `enriching_(N+1)`; кап по `enrich_slots_count` —
  `_next_running_with_enrich_cap` (`auto_advance.py:416`).
- `images_ready` / `hero_ready` / `videos_ready` → возврат на check-ноду через
  `vision_check_loop` (`auto_advance.py:1392-1421`).
- 3 фейла harness-гейта на статусе → `paused` (`auto_advance.py:1270-1282`);
  3 фейла шага → `paused` + sleep 30 мин; 9 → abandon
  (`step_failure_policy.py:29-33, 350-363, 371-403`).

Ортогональные state-машины: `FrameStatus` (models.py:76-85), `NodeRunStatus`
(:151-169), `WorkflowRunStatus` (:172-181), `BatchStatus` (:136-148),
`HITLDecision` (:128-133).

---

## 2. Шаги: статус → handler → вход/выход → гейт

Диспетчер — if/elif в `advance_project`: `app/orchestrator/pipeline.py:132-186`.
Реестр нод: `app/orchestrator/node_registry.py:33-181`. Prerequisites:
`app/telegram/menu.py:212-271`, :311-374.

| running | handler | вход | выход | гейт → ready |
|---|---|---|---|---|
| planning | steps/make_plan.py:18 | topic; xsr.run_plan_xlsx :28 | general_plan :33, apply_ops :38, xlsx «Общий план» :65 | harness(plan) :78 → plan_ready → HITL approve_plan :81 |
| scripting | steps/make_script.py:15 | general_plan; run_script_xlsx :25 | script_text :30, apply_ops :38 | БЕЗ harness → script_ready :40 → HITL approve_script :61 |
| splitting | steps/split_frames.py:15 | Frame + meta.split_completed (idempotent skip :28-36) | replace_frames :47, export xlsx :66; чистит meta :109-117 | ≥2 кадров :103; harness(split) :135 → frames_ready |
| scene_designing | steps/scene_design.py:88 | Frame (uuid обязателен :109), чекпоинты в meta | SceneDesignCell staging :147, reply-файлы :151 | agents_all_done :154 → scene_agents_ready, иначе frames_ready :161; harness(scene_d) :191 |
| scene_assembling | steps/scene_design.py:195 | staging-ячейки, sd_assembler, sd_chronology | scene_registry в meta + attrs кадров | harness(scene_asm) :398 → scene_design_ready :363 |
| generating_hero | steps/generate_hero.py:423 | meta.excel_hero / лист «Персонажи» :371 | Artifact(hero_reference); провал → откат frames_ready (:782,:1033,:1090,:1164,:1324) | все пары одобрены → hero_ready :506/:840 |
| generating_items | steps/generate_items.py:86 | item_descriptions :90 | PNG items/, Artifact(item_reference) :182 | ошибка → откат hero_ready :173; иначе items_ready :205 |
| enriching_1..5 | steps/enrich_xlsx.py:386 | слот из статуса :371, project.xlsx :431, конфиг ноды :441 | apply_ops в БД, xlsx round-trip, reply-файл :113 | _harness_before_enrich_ready(excel_gpt) :177-185; auto-chain :273 |
| generating_image_prompts | steps/generate_image_prompts.py:82 | backfill_project_v2 :89, voiceover_text, uuid_map :107 | Frame.image_prompt через apply_ops(img_pr) :162 | все кадры заполнены (RuntimeError :52); harness(img_pr) :60 → image_prompts_ready |
| generating_images | steps/generate_images.py:462 | image_prompt, PNG scenes/ (диск = истина :542-568), refs :292/:346 | PNG, Artifact(scene_image), attrs.fail_reason :530 | finalize_or_retry(images) :880 → images_ready :889 → vision_check_loop :900 |
| generating_animation_prompts | steps/make_animation_prompts.py:136 | image_prompt, image-strip (vision batch :64), локальный fallback :106 | Frame.animation_prompt :481 | harness(anim_pr) :46 → animation_prompts_ready |
| generating_videos | steps/generate_videos.py:752 | animation_prompt, PNG, recover_scene_videos :766 | mp4, Artifact(scene_video), attrs inflight/fail :139-175 | finalize_or_retry(video) :1013 → videos_ready → vision_check_loop :1031 |
| generating_audio | steps/generate_audio.py:190 | voiceover_text :246, voice_full_* :222, кэш whisper :262 | клипы audio/, Artifact(audio, whisper_words), AsrWord :63 | finalize_or_retry(audio) :176 → audio_ready |
| generating_music | steps/generate_music.py:36 | music/ :43, voiceover.txt :74, topic :81 | mp3, Artifact(music) :129 | finalize_or_retry(music) :141 → music_ready |
| sfx_planning | steps/plan_sfx.py:18 | Frame :33, settings.sfx_enabled :26 | sfx_plan.json :47 | pass-through если выкл :28 → sfx_plan_ready |
| generating_sfx | steps/generate_sfx.py:18 | load_sfx_plan :34 | звуковые файлы :38 | → sfx_ready |
| assembling | steps/assemble.py:114 | recover_before_assemble :127, Frame+клипы :54, audio/words | финальный mp4, субтитры, Artifact(final_video) :577 | откат при пустых входах :132/:358; успех → assembled :581 + HITL approve_final :588 |
| publishing | steps/publish.py:20 | Artifact(final_video) :29, meta.published_platforms :43 | meta.published_platforms :55 | все 5 площадок → published :74, иначе остаётся :77-81 |

Обвязка каждого такта (`pipeline.py:84-196`): register_advance_task :97 →
abort_if_cancelled :101 → acquire_step_lock :114 (глобальный лок только split,
`step_global_lock.py:17`) → _prepare_node_run_for_status :121 →
bind_project_llm :132 → handler → _sync_storage_after_advance :189.

**Три уровня гейтов:**
1. `harness_gate_or_raise` — внутри шага, ДО смены статуса
   (`agent_harness.py:750-773`): plan, split, scene_d, scene_asm, excel_gpt,
   img_pr, anim_pr.
2. `finalize_or_retry` — media-шаги, откат в running при недоборе
   (`post_step_validate.py:446-487`; валидаторы :128/:193/:245/:272).
3. Центральный `_harness_gate` перед выходом из `*_ready`
   (`auto_advance.py:1236-1291`).

auto_advance (`maybe_auto_advance`, `auto_advance.py:1294-1598`), порядок
проверок: user_stop :1316 → montage_job :1328 → auto_awaits_manual_start :1337
→ gen_queue :1359 → clamp_status_to_data :1376 → vision_check_loop :1392 →
harness :1424 → gen_queue hold :1439/:1457 → is_generation_active :1470 →
ready_status_confirmed_by_data :1478 → HITL :1498-1530 → verdict-review :1549
→ _apply_approve :1597.

---

## 3. Воркер: точки входа, poll-цикл, startup

Основной цикл `_run_worker_loop` — `app/main.py:269-763`, тик
`asyncio.sleep(5)` :763. Порядок тика: mass_pause :490-496 →
gen_queue_reconcile/tick :499-506 → resume_expired_error_sleeps :523 →
select активных :531-533 (список running-статусов :300-321) → фильтры
(user_stopped :539, gen_queue :546/:553, stop :560-580, generation_active
:581, sleeping :597, слоты worker_max_parallel :534/:607) → запуск (await либо
fire-and-forget :626-648; тело `_handle_one_advance` :335-480 с retry
«database is locked» ×3 :345-360 и record_step_failure :429) → auto_mode-тик
:653-733 → serial_tick_batches :740 → gen_queue_tick :754.

**Точки поднятия воркера (4):**
- `pipeline_worker.py:12-21` — singleton на процесс;
- `app/main.py:944-946` — `python -m app.main`;
- `app/web/api.py:171-188` — FastAPI lifespan (тот же процесс);
- `app/worker.py:94-120` — legacy-воркер со СВОИМ урезанным
  `ACTIVE_STATUSES` :29-47 (без scene_*, music).

Фоновые петли: `background_sync_loop` (2.5 c) и
`background_node_run_reconcile_loop` (60 c) — `main.py:981`,
`run_sync.py:468/1581`. Ручной запуск шага: `project_steps.py:69`,
`POST /api/projects/{id}/steps/{code}/run` (`web/routers/projects.py:450-480`).

**startup_guard** (`startup_guard.py:70-187`): на старте процесса все running
откатываются к `step.requires` (:97-132) с метками в meta :101-105; NodeRun
running/queued того же типа → pending (:24-67); auto_mode не гасится —
`arm_auto_await_manual_start` :109; батчи running → paused + mass-pause
:162-175. Это и есть «рестарт = откат назад» из аудита §2.

---

## 4. LLM-транспорт и логические агенты

### 4.1 Транспорт (слои)

| Слой | Точка | Суть |
|---|---|---|
| L1 сырой | `gpt_api.py:2052` `chat()` | HTTP к kie/TokenRouter/vibecode; ретраи `gpt_max_retries`=4 (`settings.py:130`), backoff min(2·2^n, 30) (`gpt_api.py:2419`) |
| L1a авто-дробление | `gpt_api.py:1938` `_chat_adaptive_1_2_4` | обрез/ошибка → нарезка db_frames.json пополам → рекурсия → склейка `_merge_packed_apply_ops` :1844 |
| L1b докрутка объёма | `gpt_api.py:1781` → `volume_batches.py:285` | добор недостающих uuid; ошибка добора → warning + break — ТИХАЯ ПОТЕРЯ (`volume_batches.py:296-305`) |
| L1c continuation | `gpt_api.py:2261-2320` | только vibecode: до 2 «продолжи», склейка `stitch_llm_continuation` :1274 |
| L1d PDF | `gpt_api.py:2442` `chat_pdf_in_chunks` | |
| L2 клиентский | `gpt_client.py:67` `ApiGptClient.ask_with_files` (chat на :181) | первый .txt/.md вложения = мастер-промт |
| L3 операторский | `gpt_operator_client.py:132` → `:293` `_run_operator_api_real` | 4 точки chat(): :348, :379, :440, :561; vision-батчи :631 |

Вложенные ретраи (аудит §3): chat ×5 → adaptive split 1→2→4 → CF-continue ×2
→ batch-outage ×3 → verdict ×3 — слои друг о друге не знают; худший случай —
десятки полноразмерных платных запросов на шаг. Самая дорогая строка: контекст
до 900 000 символов пересылается на каждой попытке (`gpt_api.py:66`).

### 4.2 Логические агенты (A1–A25)

Сводная таблица; P0 = ядро миграции на контракты (этап 5).

| ID | Агент | Call-site | Выход → парсер | Repair-retry | P0 |
|---|---|---|---|---|---|
| A1 | plan | xlsx_step_runners.py:299 | apply-ops → extract_general_plan :242 | нет | |
| A2 | script | xlsx_step_runners.py:370 | 3 каскадных парсера :379-410 | нет | |
| A3 | split | xlsx_step_runners.py:509 | replace_frames → :449; fallback локальная разбивка | нет | ✔ |
| A4 | hero | generate_hero.py:615, :1257 | свободный текст | только по длине | |
| A5 | sd_skeleton | scene_design/skeleton.py:1571 | parse_agent_slice → extract_json_object | есть, с текстом ошибки (runner.py:364-386) | ✔ |
| A6 | sd_skeleton_editor | skeleton.py:1602 | _parse_editor_reply :1408 | эталонный loop с фидбеком, 2 раунда | ✔ |
| A7-A10 | sd_characters/world/action/camera | runner.py:326→:340 | parse_agent_slice :923 | нет | ✔ |
| A11 | sd_assemble | runner.py:803→:830 | parse_assembler_payload :994 | feedback-параметр есть :808 | ✔ |
| A12 | excel_gpt / apply-ops ядро | enrich_xlsx.py:1084; батчи apply_ops_batches.py:251 | extract_apply_ops_json → db_apply.apply_ops:1085 | 1× с контрактом (gpt_operator_client.py:543-568) + salvage | ✔ |
| A13 | scene_grammar | scene_grammar_batches.py:245 | diagnose_apply_ops_text :187 | лучший loop: 3 попытки, типизированный фидбек :268-289 | ✔ |
| A14 | character_registry | enrich_xlsx.py:1084 (ветка :96) | _normalize_character_card → upsert_characters | как A12 | |
| A15 | img_pr | xlsx_step_runners.py:876 | parse_img_pr_ops :316 (+ regex-salvage :261) | 3 попытки свежей сессией, без текста ошибки | ✔ |
| A16 | anim_pr (2 фазы) | make_animation_prompts.py:290, :79 | parse_animation_reply :799 | нет; фаза 1 проглатывается :305-311; фаза 2 → локальный композер :104 | ✔ |
| A17 | check-ноды | enrich_xlsx.py:1084 (check_mode) | parse_check_analysis :1322 | 1-2 узких ретрая; fail-closed без ключа (:204-208) | |
| A18 | verdict-review | gpt_verdict_review.py:601+ | parse_gpt_verdict :161 | до 3 раундов; фикс упал → approved=False без raise :646-661 | |
| A19 | auto_review | auto_review.py:184 | parse_review_json :107 | нет; ошибка парса → warning + пустой dict :190 | |
| A20 | sfx_plan | sfx_plan.py:198 → ai_result_io.text_job:77 | parse_json_object :214 | ЭТАЛОН (см. §4.4) | |
| A21 | music | generate_music.py:99 | сырой текст; len<20 → RuntimeError :107 | нет | |
| A22 | montage ИИзменение | montage_ai_change.py:118 | strip_ai_change_reply | нет | |
| A23 | outsee_retry compress/rewrite | outsee_retry.py:441, :582 | текст | нет; любая ошибка → return None :445/:592 | |
| A24 | orchestrator chat (Web) | db_browser.py:2246 | может нести apply-ops → apply прямо из чата :2257-2270 | нет | |
| A25 | служебные | gpt_workspace.py:1400; test_prompt.py:133; enrich_xlsx.py:1425 legacy; xlsx_text_writeback.py:1015 | разные | разные | |

Детали по каждому агенту (промпт-источники, объёмы контекста, точки поломки):

- **A1 plan** — промпт `prompts/01_plan/` + `_PLAN_DB_HINT`; поломка:
  fallback «любая строка ≥80 симв из target=project» (:257-261) затаскивает
  случайное поле в general_plan.
- **A2 script** — 3-й каскадный парсер «весь ответ целиком, если ≥200 симв»
  может записать в закадр отчёт/извинение модели.
- **A3 split** — тихая подмена LLM-разбивки локальной эвристикой
  (`voiceover_split_local`): шаг зелёный, но это не ответ модели.
- **A5–A11 scene_design** — самые тяжёлые контексты (полный закадр + срезы
  предыдущих агентов, сотни КБ); `extract_json_object` выбирает кандидата
  эвристикой «больше непустых marker-списков» (`agents.py:296-375`).
- **A12 apply-ops** — 110 алиасов `FIELD_ALIASES` (`db_apply.py:115-231`) +
  `PROJECT_FIELD_ALIASES` :240; `normalize_fields:262` fail-closed на любом
  неизвестном ключе (весь батч гибнет из-за одной опечатки); ремонт uuid по
  Хэммингу :290; «номер вместо uuid» :335; salvage обрезанного JSON :367.
- **A15 img_pr** — критичная поломка: `if all_ops: continue`
  (`xlsx_step_runners.py:948`, :967) — провал батча при наличии хоть каких-то
  ops проглатывается молча; regex-salvage в окне 12 000 симв может склеить
  чужой промт (`img_pr_batches.py:275`).
- **A16 anim_pr** — санитар legacy-бага `unpack_animation_prompt_blobs:737`;
  нераспакованные blob тихо обнуляются :790-793.
- **A24 orchestrator chat** — apply-ops исполняются прямо из свободного чата.

### 4.3 JSON-экстракторы (8 самописных)

| # | file:line | Отличие |
|---|---|---|
| 1 | db_apply.py:425 extract_apply_ops_json | fence + rfind("{") + ручной баланс скобок со строковым автоматом; единственный с salvage |
| 1a | db_apply.py:367 salvage_ops_from_partial_json | целые объекты из незакрытого ops:[…; флаг _salvaged_partial :485 |
| 2 | scene_design/agents.py:296 extract_json_object | обход влево с балансом, выбор по _score (непустые marker-списки) |
| 3 | check_analysis.py:1011 extract_json_object | каскад: целый → fence → первый {…} → обрезка |
| 4 | check_analysis.py:574 extract_db_patch | только секция ## db_patch |
| 5 | ai_result_io.py:214 parse_json_object | счётчик глубины БЕЗ учёта строк (ломается на { в строке); единственный бросает ValueError с текстом |
| 6 | animation_prompt_gpt.py:645 _loads_json_object | fence + json.loads + обрезка по балансу (без учёта строк) |
| 7 | img_pr_batches.py:261 salvage_img_pr_ops | не парсер: regex по "frame_uuid" в окне 12 000 симв |
| 8 | auto_review.py:107 parse_review_json | список кандидатов; возвращает (dict, error), не бросает |

### 4.4 Матрица обработки ошибок LLM (4 несовместимых поведения)

**raise (fail-closed):** enrich_xlsx.py:1213/:1218/:1237/:1247;
gpt_operator_client.py:604/:165/:205; db_apply.py:1124/:1128/:1165/:273;
xlsx_step_runners.py:307/411/516; apply_ops_batches.py:307;
scene_grammar_batches.py:292; ai_result_io.py:142; runner.py:834;
skeleton.py:1633.

**warning + continue:** xlsx_step_runners.py:948/:967 (батч img_pr потерян
молча); volume_batches.py:296-305 (провал добора); gpt_operator_client.py:88,
:463; make_animation_prompts.py:305; gpt_api.py:2286.

**тихий None/False:** outsee_retry.py:445/:592; gpt_operator_client.py:845-865;
auto_review.py:190; animation_prompt_gpt.py:790-793; img_pr_batches.py:100/107;
gpt_verdict_review.py:646-661.

**fallback без LLM:** xlsx_step_runners.py:591-594 (split → локальная
разбивка); make_animation_prompts.py:104 (anim_pr → локальный композер).

**rollback:** явного нет; транзакционная граница — `db_apply.apply_ops:1085` +
commit после каждого батча (`enrich_xlsx.py:1063`) → падение N-го батча
оставляет применёнными 1..N-1.

**Эталонный контур** (образец для этапа 5) — `ai_result_io.py:87-140`
`text_job`: чекпоинт до вызова :94-97 → цикл попыток :103 → фидбек «# ОШИБКИ
ПРОШЛОЙ ПОПЫТКИ» :104-106 → parse (ValueError) отдельно от validate (список
проблем) :111/:125 → история problems_log :74 → чекпоинт только на успехе
:127-131 → fail-closed RuntimeError :142. Единственный потребитель —
sfx_plan.py:198; остальные 40 call-site переизобретают куски цикла по-своему.
Чего эталону не хватает: типизация payload (Pydantic), сохранение отклонённых
ответов на диск, раздельные лимиты parse-fail/validate-fail.

---

## 5. Контуры проверок (4 несогласованных)

| Контур | Вход/вызов | Вердикт | Лимит |
|---|---|---|---|
| К1 checkMode-нода → vision_check_loop (единственный живой vision) | enrich_xlsx.py:231-248; промпт gpt_operator.py:292/:389/:439 | pass\|fail + scores/[critical] (check_analysis.py:192-274) → meta.gpt_operator_results[node].gateStatus | 20 (vision_check_loop.py:39) |
| К2 auto_review (HITL auto-mode) | auto_advance.py:1683-1712 — ТОЛЬКО текст; review_image не вызывается ниоткуда (auto_review.py:306, докстринг :316-321) | JSON decision approved\|regen\|rejected | 2 (auto_advance.py:88) |
| К3 gpt_verdict_review «Вердикт» | auto_advance.py:1549-1579 + prompt_studio.py:826 | русский текст «Вердикт: Одобрено» (gpt_verdict_review.py:161) | 3 (:38) |
| К4 post_step_validate (без LLM) | generate_images.py:878, generate_videos.py:1011, generate_audio.py:174, generate_music.py:59,139 | булев ValidationResult.ok | без лимита |

Несогласованность: 4 разных словаря вердиктов без общего типа; 2 разных корня
промптов (К1 — `prompts/check_operator/` + `templates/check_agents/` в git;
К2/К3 — `prompts/check_*/` под .gitignore → **К2/К3 не работают на чистом
клоне**); независимые маппинги папок (gpt_verdict_review.py:93 vs
auto_review.py:46); гейт К1 перебивает сам себя — meta `pass` + свежий `fail`
→ берётся `pass` (vision_check_loop.py:515-523). При checkMode-ноде следом К3
скипается в пользу К1 (auto_advance.py:1556-1573).

### 5.1 vision_check_loop — почему петля не сходится

Цикл: vision_check_loop.py:477 → отчёт :509 → гейт :510-523 → фиксация [ok]
:526 → цели regen :540-544 → (только scenes) авто-патч :571-595 → круг+лимит
:613-624 → db_patch :626 → mark_passed_except_regen :629-636 → удаление
файлов и повтор :657-725 → возврат на check :728.

Промпт регенерации = `Frame.image_prompt` из БД. Вердикт влияет на него всего
двумя путями: (A) модельный `## db_patch` (check_analysis.py:574); (B)
авто-`[VISION_FIX]` (vision_regen_fix.py:169 → merge_prompt_with_fix :158).

**До промпта регенерации доезжают только 2 оси из 10** (VISION_SCORE_AXES,
check_analysis.py:193-204): `character` (нет c0X — _MISSING_RE,
vision_regen_fix.py:23-27) и `clones` (_CLONE_RE :28-35 → HARD CONSTRAINT).
Остальные (style/quality/hands/text/angles/format/pose/logic) регенерят
байт-в-байт тем же промптом. Причём авто-фикс работает только для shot 1
(:213) и только kind=="scenes" (vision_check_loop.py:571) — для hero и videos
авто-фикса нет вообще. Ось `logic` есть в кортеже, но отсутствует в шаблоне
`## scores` (:235-245) — состав усреднения overall плавает (:653-656).

**Исчерпание 20 кругов** (:615-624): warning «сдаёмся» →
clear_vision_check_meta → return False. Проект тихо встаёт на `*_ready` без
исключения, уведомления и статуса; метка «сдались» не сохраняется — следующий
запуск check-ноды начинает счёт с нуля.

### 5.2 «[ok] навсегда»

- mark_ok_tokens_from_reply (vision_check_loop.py:891-912) фиксирует все
  [ok]-строки в `meta.vision_check_passed` — вызывается ДО ветвления
  pass/fail (:526), т.е. [ok] из отчёта с verdict:fail тоже фиксируется.
- mark_passed_except_regen (:858-888) — всё, что не в critical-regen,
  помечается passed: кадр, который модель просто НЕ УПОМЯНУЛА, «принят».
- Пересмотр невозможен: filter_image_paths_for_recheck (:802-855) не
  отправляет passed-кадры на recheck → модель их не видит → убрать из passed
  (:882) их нечем. Замкнутый круг. Сброс — только clear_vision_check_meta
  (общий pass :530, исчерпание :623, потеря slot :750).

### 5.3 media_probe: есть, но не подключён перед vision

`app/services/media_probe.py`: probe_video_size :10 (RuntimeError при
неудаче), probe_duration :38. Вызывается только постфактум
(монтаж/тайминги). Точки врезки перед vision (этап 4):

1. enrich_xlsx.py:735 — финальный список файлов в GPT, без единой проверки.
2. enrich_xlsx.py:756-765 — mp4 → 6 стиллов: проверить mp4 ДО нарезки.
3. video_sheet.py:64 `_probe_duration_sec` — ДУБЛЬ probe_duration с молчаливым
   fallback `return 8.0` (:80,83,86): битый клип превращается в сетку стиллов
   «как будто 8 секунд», vision видит артефакты вместо ошибки.
4. generate_videos.py:600-640/:700-740 — mp4 принимается в Artifact без
   проверки длительности/разрешения.
5. generate_images.py:1114 — PNG без проверки aspect/размера.

### 5.4 STYLE-lock: физически выключен

`app/services/img_pr_style.py`: wrap_scene_with_style :229-237 и
wrap_ops_styles :240-248 — no-op (докстринги признают); STYLE_HEAD :15-29,
STYLE_TAIL :31-57 (включая negative `no twins/clones/duplicate faces`),
KNITTED_* :59-76 — никем не читаются; _wrap_with_block :219-226 — мёртвый
код. Живой resolve_project_img_style :185-205 только логируется
(xlsx_step_runners.py:714-726). Единый стиль целиком на совести GPT-ноды
img_pr — код-сайд гарантии нет; negative-строка про клонов жила в STYLE_TAIL
→ прямая связь с осями style/clones из §5.1. Тесты фиксируют no-op как норму
(tests/test_img_pr_style.py:13-29). HEAD 3bbf5c3 — revert фикса; история
squash-нута, археология невозможна.

---

## 6. Медиа-тракт

| Тракт | Точка вызова | Провайдер | Retry | Лизинг |
|---|---|---|---|---|
| img | generate_images.py:1540/:1560 → outsee_retry.py:754 | media_route.py:47: gpt-image-2-vip/nano-banana-2* → Outsee HTTP; иначе IMAGE_PROVIDER (grsai); CDP-UI путь :1526 | 3 на исходном промпте → GPT-rewrite → ещё 3; сжатие :404, moderation-rewrite :540, backoff 45/75/120/180 c | img_gen_inflight (img_streams.py:20) — БЕЗ TTL/owner |
| video | generate_videos.py:582 → outsee_retry.py:1119 | media_route.py:54: veo-3-1-lite → Outsee; kling-2-6 → kie.ai (kie_kling.py:30-31); иначе grsai | лестница фиксирована (max_attempts_per_prompt игнорируется :1136): Veo rewrite+1 → Kling ×3 → skip; ≥5 фейлов кадра → video_gen_skip (:136), снимается вручную | video_gen_inflight (img_streams.py:21) — БЕЗ TTL |
| voiceover | generate_audio.py:311 → ElevenLabsBot | ElevenLabs через БРАУЗЕР/CDP (bots/elevenlabs.py, 843 строки Playwright), не HTTP API | на уровне tts ретраев НЕТ (один вызов, frame_audio.py:512); ретраи только в UI-селекторах | — |
| assemble | assemble.py:110 → assembly.py:152 (ffmpeg) | локальный ffmpeg; движок v2 montage/variant2.py | нет | montage_lane (montage_coexist.py:17-59) — ЕДИНСТВЕННЫЙ с TTL (7200 c) |

Кэш по входу: только ASR (sha256 текста озвучки, Artifact(whisper_words)).
Остальное — «файл существует на диске → пропустить» (идемпотентность по
выходу, не по входу); клип для сборки выбирается «новейший по mtime»
(assemble.py:54).

Сводка лизингов без TTL (этап 2): img_gen_inflight, video_gen_inflight,
video_gen_skip (generate_videos.py:135-170), vision_check_passed
(vision_check_loop.py:31), _SPLIT_LOCK (in-process only,
step_global_lock.py:14-35).

---

## 7. Модели данных

- **Frame** (models.py:324-355): number (uniq), uuid/sort_key (DB v2),
  voiceover_text, image_prompt, animation_prompt, status, attrs (fail_reason,
  inflight-маркеры), start_ts/end_ts, scene_id.
- **Artifact** (models.py:461-475): kind (:88-98), uuid unique, path, meta,
  approved_at. Восстанавливается с диска (`artifact_recovery.py`).
- **NodeRun** (models.py:755-816): статус защищён валидатором :800-816 →
  `node_status_machine.py:67`; переходы только через transition_node_status
  :159 и обёртки. meta/attempts — готовые места под input_hash и счётчики
  (этапы 2-3).
- **Attempt** (models.py:522-534): МЁРТВАЯ модель — 0 использований в app/.
  Ретраи фактически живут в NodeRun.attempts + meta.failure_state.
- **WorkflowRun** (models.py:715-752): snapshot графа нод на момент запуска.

---

## 8. Реконсайлеры и recovery (система регулярно теряет состояние)

Startup (`_startup_maintenance`, main.py:796-848): block_pipeline_autorun
(startup_guard.py:70), _backfill_from_disk (main.py:103-203), recompute_all
(project_state.py:757), reconcile_stale_montage_jobs
(montage_board_job_state.py:53-82), reconcile_stale_node_runs
(run_sync.py:1576), reset_running_sessions, repair_project_xlsx_if_corrupt
(xlsx_versioning.py:353).

Фоновые: node_run_reconcile 60 c (run_sync.py:1382-1421 — включая «heal»
failed→done при доказанном успехе), sync 2.5 c, gen_queue_reconcile
(gen_queue.py:358-386), resume_expired_error_sleeps
(step_failure_policy.py:507), chrome_recovery (chrome_recovery.py:57-69).

Данные↔статус: compute_actual_status (project_state.py:339),
clamp_status_to_data (step_data_guard.py:384-440),
ready_status_confirmed_by_data (auto_advance.py:1478).

Диск→БД: artifact_recovery.py (10 функций recover_*), ensure_frames_from_disk,
finish_missing.py:92/:159/:230, montage_outsee_recover.py:170/:369,
db_apply.repair_near_miss_frame_uuids:290.

---

## 9. Сводка находок с привязкой к этапам

Новое сверх аудита (найдено картографированием):

| # | Находка | Где | Этап |
|---|---|---|---|
| 1 | `sfx_planning`/`generating_sfx` не входят в ACTIVE_STATUSES воркера — шаги никогда не подхватываются poll-циклом, хотя обработчики есть | main.py:300-321 vs pipeline.py:171-178 | сказать заказчику сразу |
| 2 | Второй legacy-воркер со своим урезанным списком статусов | app/worker.py:29-47 | этап 2 (лишняя точка двойного исполнения) |
| 3 | Коллизии ord в _STATUS_ORDER (music_ready=36=sfx_planning и др.) | menu.py:137-142 | этап 2 |
| 4 | Attempt — мёртвая таблица | models.py:522 | этап 3 (не строить учёт на ней) |
| 5 | Гейт К1 перебивает fail на pass при конфликте | vision_check_loop.py:515-523 | этап 4 |
| 6 | [ok] фиксируется даже из отчёта с verdict:fail; неупомянутый кадр = принят | vision_check_loop.py:526, :858-888 | этап 4 |
| 7 | Авто-фикс промпта — только shot 1 и только scenes; hero/videos — никогда | vision_regen_fix.py:213, vision_check_loop.py:571 | этап 4 |
| 8 | Ось logic отсутствует в шаблоне scores — overall плавает | check_analysis.py:235-245, :653-656 | этап 4 |
| 9 | severity-only отчёт без scores проходит гейт автоматом | check_analysis.py:820-824 | этап 4 |
| 10 | _probe_duration_sec — дубль media_probe с fallback 8.0 c на битом клипе | video_sheet.py:64,80-86 | этап 4 |
| 11 | Исчерпание 20 кругов — тихое, без уведомления; счёт начинается заново | vision_check_loop.py:615-624 | этап 4 |
| 12 | Номера кадров из critical — голым \b(\d{1,4})\b: ложные regen-цели | check_analysis.py:875-921 | этап 5 |
| 13 | img_pr: `if all_ops: continue` — провал батча молча проглатывается | xlsx_step_runners.py:948,:967 | этап 5 |
| 14 | script: fallback «весь ответ целиком» пишет мусор в закадр | xlsx_step_runners.py:379-410 | этап 5 |
| 15 | split: тихая подмена LLM-разбивки локальной эвристикой | xlsx_step_runners.py:591-594 | этап 5 |
| 16 | Провал volume-добора — тихая потеря части ops | volume_batches.py:296-305 | этап 5 |
| 17 | apply-ops: коммит после каждого батча — падение N-го оставляет 1..N-1 | enrich_xlsx.py:1063 | этап 5 |
| 18 | orchestrator chat исполняет apply-ops прямо из свободного чата | db_browser.py:2246-2270 | сказать заказчику |
| 19 | auto_review.review_image мёртв; промпты К2/К3 под .gitignore — не работают на чистом клоне | auto_review.py:306, .gitignore:52 | этап 4 |
| 20 | voiceover: ElevenLabs через Playwright-браузер, ретраев tts нет | generate_audio.py:311, frame_audio.py:512 | роадмап |

Подтверждено из аудита: fail-open stub (закрыт этапом 0,
`stage-0-security-hygiene`), лимит 20 регенераций, 2 оси из 9(10) в промпте
регенерации, отсутствие media_probe перед vision, STYLE-lock no-op, 110
алиасов, лизинги без TTL, usage выбрасывается.
