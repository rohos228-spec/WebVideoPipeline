# Delta: checks-cascade (stage-4-checks-cascade)

## MODIFIED Requirements

### Requirement: Лимит регенераций из конфига, исчерпание = громкая пауза

Уточнение механики (target-спека остаётся в силе): сквозной счётчик —
`meta["vision_rounds_total"]` по ключу check-ноды; при исчерпании
`clear_vision_check_meta` НЕ вызывается — regen-цели, unverified-перечень
и счётчик сохраняются для решения оператора. Решения оператора v1 —
явный endpoint `POST /api/projects/{id}/vision-decision`
(`more_rounds` | `accept_pending` →
`vision_accepted_by_operator`, отдельный список с kind-namespace
токенов, не `vision_check_passed`). Ручной перезапуск check-ноды
решением НЕ считается и счётчик не сбрасывает: recheck выполняется, но
regen-круг заблокирован лимитом до явного решения.

#### Scenario: Перезапуск check-ноды без решения
- **WHEN** проект в pause по vision-лимиту, оператор запускает check-ноду
  без vision-decision
- **THEN** recheck выполняется; при fail regen-круг не стартует
  (лимит), проект снова в pause с той же причиной; счётчик не обнулён

#### Scenario: Рестарт процесса между кругами
- **WHEN** процесс перезапущен при активной петле (round k < limit)
- **THEN** `vision_rounds_total` в meta сохранён; следующий круг — k+1,
  не 1

### Requirement: Принятие кадра пересматриваемо

Уточнение трёхзначной модели: **accepted** (`vision_check_passed` из
непротиворечивого pass-гейта ∪ `vision_accepted_by_operator`) /
**pending-regen** (critical- и media_probe-цели) / **unverified** (все
остальные, включая неупомянутые и [ok] из fail-отчёта). [ok] в
fail-отчёте даёт soft-ok ТОЛЬКО текущего круга: кадр не регенерится, но
остаётся в recheck. Recheck-набор SHALL быть «все кадры минус accepted»
(не «только regen-цели»).

Инвалидация единицы (этап 2, `drop_vision_passed_for_frame`) SHALL
снимать токены кадра из ОБОИХ accepted-списков; токены
`vision_accepted_by_operator` несут kind-namespace (`scenes:f3` ≠
`videos:f3`).

#### Scenario: Soft-ok не экономит recheck
- **WHEN** отчёт с verdict: fail содержит `[ok] f3`, петля идёт на круг k+1
- **THEN** f3 не в regen-целях круга k+1, но присутствует во входе
  recheck; permanent-отметки у f3 нет

#### Scenario: Принятый оператором кадр перегенерён
- **WHEN** кадр в `vision_accepted_by_operator`, его промпт изменён и
  единица инвалидирована (этап 2)
- **THEN** токен кадра снят и из операторского списка — кадр попадает в
  recheck следующей проверки

### Requirement: Отчёт без оценок не проходит автоматом

Уточнение пути: `LlmContractError(kind="validate")` поднимается ТОЛЬКО в
parse-слое (`parse_check_analysis` с флагом vision-проверки от клиента)
— там его ловит существующий repair-цикл. Пост-фактум вызовы гейта в
петле НЕ бросают: неполный отчёт (нет overall И нет critical) там
трактуется консервативно как `fail`. Generic-поведение
`resolve_vision_check_gate` для не-vision отчётов не меняется.

#### Scenario: severity-only на нестрогом пути
- **WHEN** отчёт без scores и без critical разбирается вызовом без
  strict_contract
- **THEN** гейт = fail (не pass); авто-regen не запускается (нет целей),
  цикл не стартует

### Requirement: Каскад дёшево→дорого (media_probe перед vision)

Уточнение точек врезки по текущему дереву (строки карты уехали после
этапа 2): check-препроцесс — `preflight_media_for_check` в
checkMode-блоке `enrich_xlsx.py` ДО `materialize_video_sheets_for_check`;
дубль probe — `video_sheet._probe_duration_sec` (fallback 8.0 c);
приёмка — `generate_images` / `generate_videos` / `generate_audio` до
записи Artifact. Пороги probe (яркость чёрного кадра, допуск aspect ±5%,
уровень тишины) — константы модуля `media_probe`, не конфиг (v1).

#### Scenario: Все файлы проверки битые
- **WHEN** preflight отбраковал все входные файлы check-ноды
- **THEN** vision-вызов не выполняется вовсе; regen-цели с причинами
  `media_probe:*` запускают регенерацию; круг считается в сквозной лимит

## ADDED Requirements

### Requirement: Регенерация без изменений — громкое событие

Перед regen-кругом промпт каждой цели SHALL сравниваться со снимком
прошлого круга; байт-в-байт совпадение логируется как ошибка петли
(`vision_noop_regen`-счётчик в meta), регенерация при этом выполняется
(блокировка создала бы deadlock петли).

#### Scenario: Ось без билдера фикса
- **WHEN** critical-замечание не дало правки промпта (ось не распознана)
- **THEN** регенерация идёт старым промптом, но `vision_noop_regen`
  инкрементирован и в логе ERROR с перечнем целей
