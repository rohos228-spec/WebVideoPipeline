# Батч-порт E3 — img/hero (net-эффект 6 коммитов)

Источники: `945502a4`, `e7fc0351`, `bc972d40`, `95ef8dcc`, `35e91ef5`, `57b8810f`.

## Портировано (img)

1. `project_steps.py` — явный ▶ `img` теперь тоже force_wipe
   (`in ("img_pr", "img")`), PNG на диске не скип.
2. `generate_images.py`:
   - `_claim_shot1_batch` двухпроходный: сначала K1, дети — только когда
     PNG родителя на диске (без INFLIGHT_ATTR — у веба lease в D.3);
   - новая `_coverage_parent_png` (гейт `is_shot_child`, не
     `is_coverage_child` — K1 с X1-parent_id чужой still не получает);
   - shot1: `merge_parent_scene_refs` + `with_parent_scene_lock` в
     `_generate_and_send` (layout-lock K2/K3 на K1 ячейки);
   - блок «все кадры на диске — outsee не нужен» удалён.
3. `reset_step.py` — `_resume_images` без отката `old/scenes`
   (`recover_scene_images_from_disk` + `sync_frames_with_disk_images`);
   новый `_resume_hero` в `_STEP_RERUN_BY_CODE`.
4. `artifact_recovery.py` — `recover_hero_references_from_disk`
   (регистрирует `characters/*.png` без копирования old/).
5. `vo_shot_expand.py` — `_IDENTITY_LOCK_PREFIX` + `with_character_sheet_lock`
   (Image N = какой cXX, анти-twin для 2+ листов).

## Портировано (hero)

6. `excel_characters.py` — `character_blocks_hero` (имя-маркер при ref_ids —
   норма) + `_field_for_prompt`; `changes_text/en` не несут маркер.
7. `generate_hero.py` — `_excel_disk_png`/`_excel_png_for_id` (реф = артефакт
   или файл на диске), вызов `recover_hero_references_from_disk`, heal-ветка
   через `character_blocks_hero`.
8. `hero_quality.py` + `agent_harness.py` — pollution-чек через
   `character_blocks_hero`.
9. `img_pr_batches.py` — «2 cXX = 2 разных лица; клоны = одно лицо на два тела».

## Осознанно пропущено

- Wipe перед прогоном (`945502a4`核心): у веба на этом месте C.6-логика
  input_hash/stale-очередь — безусловный wipe сжёг бы готовые PNG при каждом
  ▶ (живые траты). Философия «▶ img = перегенерация» достигается force_wipe
  из п.1: wipe идёт только при явном ▶ ноды, не при soft-resume.
- `character_sheet_ref.py`/`hero_ref_prompt.py` (новые файлы 57b8810f) и
  их связка в claim-контур: тянет кроп шитов и реф-контура, отдельная задача.
- `_skip_legacy_shot2`, `scan_frames` skip-children, `persons_override`,
  `montage_board` is_shot_child — потребители новых файлов, следующий батч.
- `expire_all` фикс (e7fc0351) — неактуален, wipe-блока нет.

## Тесты портированы (10)

`variation_marker` ×2, `fields_sane`, `disk_png`, `img_force_wipe`,
`waits_for_cell_parent_png` (адаптирован под lease: без INFLIGHT-assert),
`shot_child_locks_to_vo_parent`, `vo_parent_x1_not_shot_child`.

## Риски

- ▶ img теперь wipe: подтверди намерение (SOURCE-поведение). Soft-resume
  (кнопка без ▶ ноды) готовое не трогает.
- `_coverage_parent_png` кладёт still родителя ПЕРВЫМ рефом — промты K2/K3
  получат layout-lock префикс.
