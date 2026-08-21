# Tasks: stage-4-checks-cascade

Порядок исполнения: D → B → A → C (D меняет семантику разбора, B строит
доставку фиксов на ней, A оборачивает петлю, C независим и идёт последним
блоком). Нумерация — по блокам WORK_PLAN (A-D). Правило: коммит на блок,
после блока — прогон затронутых тестов; полный прогон — сверка СПИСКА
фейлов с baseline (69 предсуществующих), после прогона
`git restore ai-pack/`.

## D. Семантика принятия (первым — фундамент)

- [x] D.1 Гейт-конфликт → fail: перевернуть ветку «keep gate=pass»
      (`vision_check_loop.py:546-554`) — при расхождении сохранённого
      gateStatus и свежего resolve побеждает fail, расхождение в лог.
      Тесты старой семантики обновить («Этап 4: конфликт → fail»).
- [x] D.2 [ok] не навсегда: `mark_ok_tokens_from_reply` → только при
      resolved gate = pass. В fail-отчёте [ok] → `vision_check_soft_ok`
      (meta круга, чистится на каждом новом отчёте): не регенерим в этом
      круге, но кадр остаётся в recheck.
- [x] D.3 Убрать `mark_passed_except_regen` (неупомянутый = принят):
      неупомянутые кадры = unverified. `vision_check_passed` пишется
      только из pass-гейта; оператор — в `vision_accepted_by_operator`
      с kind-namespace токенов (`scenes:f3`/`videos:f3`/`hero:c01` —
      коллизия f3 между scenes и videos [панель 1/3]).
      `drop_vision_passed_for_frame` РАСШИРЯЕТСЯ: чистит кадр в обоих
      списках [панель 2/3, блокер].
- [x] D.4 `filter_image_paths_for_recheck`: recheck-набор = все кадры
      минус accepted (`vision_check_passed` ∪
      `vision_accepted_by_operator`); regen-цели — подмножество recheck.
      Ветка «шлём только regen-цели» удаляется.
- [x] D.5 Severity-only отчёт ≠ pass, два слоя [панель 3/3]:
      (1) parse-слой: флаг vision-проверки в `parse_check_analysis`
      (клиент: checkMode + изображения) → vision-отчёт без scores/overall
      = `LlmContractError(kind="validate")` ВНУТРИ parse → существующий
      repair-цикл `gpt_operator_client.py:492-525`; (2) петля:
      не-бросающая консервативная трактовка fail при «нет overall и нет
      critical». Generic-дефолт `resolve_vision_check_gate` НЕ менять
      (под севериti-теги попадают обычные отчёты). as-is
      `check_analysis.py:822-824`.
- [x] D.6 Regen-цели без ложных срабатываний: заменить голый
      `\b(\d{1,4})\b` (`check_analysis.py:907`) на явные токены
      (fN/NsM/frame_NNN/имена файлов; голое число — только вплотную к
      «кадр»/«frame»). Тест: «на фоне 3 фигуры» для f7 → цель только f7.
      Critical без единой адресуемой цели [панель 1/3, gap]: петля
      активна → повтор текущих pending; не активна → WARNING + гейт
      fail, цикл не стартует (молчаливого pass нет).
- [x] D.7 Тесты блока: конфликт гейта; [ok] в fail-отчёте попадает в
      recheck; неупомянутый кадр не принят; severity-only → repair/fail;
      regex-цели.

## B. Все оси → промпт регенерации

- [x] B.1 Шаблон: ось `logic` в `## scores`
      (`VISION_CHECK_REPORT_HINT`); issues в формате
      `- [critical] f7: (axis) текст`; требование явных токенов кадров.
- [x] B.2 Axis-атрибуция issue: явный тег `(axis)` + fallback-словарь
      ключевых слов по 10 осям — расширение `extract_vision_issues`
      (поле `axis`), не новый парсер.
- [x] B.3 `vision_regen_fix`: `AXIS_FIX_BUILDERS` (ось → блок в
      `[VISION_FIX]`); character/clones сохраняют текущие билдеры;
      несколько осей одного кадра — агрегация в один блок (reasons
      копятся, не last-write-wins [панель 1/3]); keyword-fallback —
      первое совпадение по порядку специфичности; снять фильтр
      `shot != 1` (`vision_regen_fix.py:214`).
- [x] B.4 Доставка по kind: scenes shot2 → поле shot2-промпта; videos →
      `промт_анимации` (снять `kind == "scenes"`-фильтр,
      `vision_check_loop.py:602`); hero → `meta["vision_fix_hero"]`
      + подмешивание в промпт в `generate_hero` (карточку персонажа не
      мутируем), очистка при pass.
- [x] B.5 Noop-regen детект: снимок промптов regen-целей в meta петли;
      байт-в-байт совпадение на следующем круге → `logger.error` +
      `vision_noop_regen` счётчик (регенерация выполняется, событие
      громкое).
- [x] B.6 Тесты: каждая ось рожает правку промпта; shot2/hero/videos
      получают фикс; noop-regen ловится. Shot2: инвалидация PNG
      фиксируется deletion-путём (`_delete_scene_pngs` shot==2), НЕ
      hash'ем — `img_input_hash` хэширует только shot1-промпт
      [панель 3/3, вопрос закрыт].

## A. Лимит из конфига + громкая пауза

- [x] A.1 `settings.vision_check_max_rounds` (env
      `VISION_CHECK_MAX_ROUNDS`, default 2); `MAX_VISION_CHECK_ROUNDS`
      хардкод удалить (`vision_check_loop.py:39`).
- [x] A.2 Сквозной счётчик `meta["vision_rounds_total"][node_key]`:
      инкремент на круге; НЕ чистится при исчерпании; сброс — полный
      pass ноды либо решение оператора.
- [x] A.3 Исчерпание → pause: `status=paused`, `meta["pause_reason"]`
      (code=vision_rounds_exhausted, node, kind, rounds, regen_pending,
      unverified), Telegram-уведомление (канал pause
      `step_failure_policy`); `clear_vision_check_meta` НЕ вызывается —
      состояние петли сохраняется. Тихий `return False`
      (`vision_check_loop.py:644-655`) умирает.
- [x] A.4 Решения оператора v1 — явный endpoint
      `POST /api/projects/{id}/vision-decision` [панель 2/3]:
      `more_rounds` (сброс total, очистка pause_reason, лог) /
      `accept_pending` (`accept_vision_pending_as_operator`: цели +
      unverified → `vision_accepted_by_operator` с kind-namespace,
      очистка петли и pause_reason, лог). Ручной перезапуск check-ноды
      решением НЕ считается: recheck выполнится, но regen заблокирован
      лимитом до явного решения — повторная пауза, не тихий сброс.
- [x] A.5 Тесты: исчерпание паузит с причиной; счётчик переживает
      исчерпание и рестарт (meta); решения оператора сбрасывают/
      принимают корректно.

## C. media_probe перед vision

- [x] C.1 `media_probe.py`: `probe_image(path, expect_aspect)` (PIL:
      читается, ненулевой размер, aspect ±5%, не-чёрный),
      `probe_video(path, expect_aspect)` (ffprobe + чёрный стилл из
      СЕРЕДИНЫ клипа — fade-in легален [панель 3/3]),
      `probe_audio_silence(path)` (volumedetect). Порог чёрного —
      почти-абсолютный (кодек-брак, не «тёмная сцена»); пороги —
      константы модуля. Коды причин `media_probe:<код>`.
- [x] C.2 `video_sheet.py`: `_probe_duration_sec` удалить → 
      `media_probe.probe_duration`; ошибка пробы = исключение (не 8.0);
      `probe_video` до нарезки стиллов (§9#10).
- [x] C.3 Check-препроцесс: `preflight_media_for_check` в checkMode
      (`enrich_xlsx.py`, до materialize/vision) → (годные, regen-цели с
      причиной); брак не доходит до vision; все файлы битые →
      vision-вызов не делается, сразу regen.
- [x] C.4 Приёмка генерации: `probe_image` в `generate_images`,
      `probe_video` в `generate_videos`, `probe_audio_silence` в
      `generate_audio` — ДО Artifact; провал = провал попытки
      (существующие retry-лестницы). Отбракованный файл — в `stale/`
      (`_stash_stale_frame_images` и аналог для видео), НЕ на месте:
      иначе «диск = истина» скипнет кадр и брак примется recovery
      [панель 1/3, блокер].
- [x] C.5 Тесты: битый/чёрный файл → regen-цель без vision; duration-
      ошибка не превращается в 8.0; приёмка отбраковывает.

## Гигиена

- [x] H.1 Warning на старте при отсутствии каталогов промптов К2/К3
      (§9#19) — контур виден, не молчит. Унификация К2/К3 — вне сметы.

## Приёмка

Живой прогон (Chattiq): сходимость ≤2 круга либо pause с причиной и
перечнем кадров (счёт не обнуляется); битый mp4/чёрный PNG отсекается до
vision (лог `media_probe:*`, vision-вызова нет); critical по любой оси
меняет промпт регенерации (включая shot2/hero/videos). Полный прогон
тестов — список фейлов совпадает с baseline. По приёмке — архив change.

## Отклонения реализации от плана (зафиксировано по факту, 2026-08-21)

- **D.0 (добавлено, не было в плане)**: петля читает СЫРОЙ ответ
  (`gpt_reply_raw.txt` первым в `_check_reply_text`) — обнаружено при
  врезке: `check_report.txt`/`gpt_reply.txt` на api-пути пишутся
  рендером из `CheckAnalysis` и теряют `## scores`/`## issues`/
  `## db_patch`/`## regen_frames`; без этого scores и db_patch до петли
  не доезжали вовсе.
- **D.5, нестрогие пути**: план говорил «консервативный дефолт fail»;
  реализовано `resolve_vision_check_gate → None` (не переопределяет
  verdict из parse). Глобальный флип в fail ломал бы обычные check-ноды:
  под severity-теги ([error]/[ok]) попадают и не-vision отчёты. Дыра
  №9 — переопределение fail→pass — закрыта; полный запрет неполных
  vision-отчётов — vision_strict (parse-слой, repair).
- **A.3**: Telegram-уведомление реализовано веткой в `notify_step_done`
  (воркер шлёт после commit) — отдельного канала не заводилось.
- **A.4**: `more_rounds` не меняет `Project.status` — paused снимается
  существующим ▶/run_step; endpoint только сбрасывает счётчик и причину.
- **C.3, частичный брак**: GPT зовётся по годным файлам (vision не видит
  брак), цели проб вливаются в regen на выходе; БЕЗ vision-вызова —
  только сценарий «все файлы битые» (синтетический fail-отчёт).
- **C.4, аудио**: probe на `voice_full_*.mp3` (один файл TTS до
  Whisper/нарезки), не per-clip — клипы режутся из целого файла,
  тишина ловится на источнике.
