# Батч-порт E1+E2 — fw_frames/excel покрытие и батчинг (net-эффект 10 коммитов)

Источники: `05377978`, `bf2653fd`, `70993b7e`, `74c0480f`, `312b9682`,
`95c294fc`, `1dce5a31`, `e17e7554`, `d0c8e744`, `477b3a89`.
Портируем union покоммитных диффов (коммиты разбросаны во времени).

## Портировано (код)

1. `scene_design/camera_expand.py` — нарезка по смыслу: `_sentence_units`,
   `_clause_units`, `_balance_units`, `vo_chunk_is_dangling`,
   `_SENTENCE/_CLAUSE/_DANGLING_TAIL_RE`; `split_text_into_parts` переписан
   (хвост пустой вместо нарезки «вместе с»).
2. `vo_shot_expand.py` — `is_coverage_child`, `merge_parent_scene_refs`,
   `with_parent_scene_lock` (+константы), `_is_pipeline_frame`,
   `_norm_words`, `flattened_coverage_groups`,
   `merge_flattened_coverage_members`, `_vo_parts_without_empty`,
   `bits_from_attrs`, `kadry_from_bits`, `_kadry_rows`, `kadry_vo_partition`;
   `resolve_shot_plan` строгий (только кадры[], без эвристики);
   `inherit_camera_on_children` с сиротами; `apply_shot_voiceover_to_cells`
   через `_cell_full_text` (без scene_chain-ветки — нет shot_templates).
3. `db_frames_context.py` — биты/кадры в `_pick_attrs` (JSON),
   `_NO_CLIP_ATTRS`, `vo_shot`/`camera_subdivide` строки,
   `image_prompt`/`animation_prompt` строки, `_coverage_parent_snapshot`,
   `build_img_pr_db_context(all_frames)` + `coverage_role`/`coverage_parent`.
4. `img_pr_batches.py` — блок `=== COVERAGE K2/K3 ===` + строка followup.
5. `xlsx_step_runners.py` — coverage-абзац хинта, 4-tuple `_load_img_pr_context`,
   `all_frames` до `db_frames.json` батча.
6. `prompt_library.py` — 2 маркера staleness; шаблоны
   `frame_prompts_continuity_ru.md`, `prompts_qc_continuity_ru.md`
   (все K2+ в `промты_детей`, запрет `_2`).
7. `orchestrator/graph/planner.py` — overflow BFS: старт с QC slot 0,
   а не с чужого slot 1.
8. `excel_gpt_node.py` — `safe_upload_node_key` (+`upload_dir` через него);
   `gpt_operator_client.py` ×3 точки и `enrich_xlsx.py` ×2 точки — через него.

## Портировано (тесты)

`split×4` (старый заменён; одно ожидание поправлено под tip — тест протух
ещё на housepc), `resolve fallback need==1`, `writes_cell_parts n==3`,
`inherit×2`, `kadry×2`, `partition×2`, `clauses`, `coverage_child`,
`merge_flattened`, `distribute_all_children`, `img_pr coverage_parent`,
`overflow_qc` (новый файл), `safe_upload`, `needs_upgrade` (адаптирован).

## Осознанно пропущено

- fw-enrich цепочка (`collapse_*`, `reseed`, `expand`, `force_full`,
  `strip_output_keys`, `four_node_markup`, `target_batches/chunk`,
  `FW_FRAMES_PARALLEL_BATCHES`, `prompt_chunk`) — в вебе нет fw-ветки.
- `bf2653fd` целиком (camera-menu контракт: нет kind `excel_gpt_prompts`).
- `generate_images` PNG-родителя (`_coverage_parent_png` — другая модель).
- `apply_ops_batches` QC-валидаторы/`split_vo_units` (конфликт с adaptive) —
  отдельная большая работа, не в этом батче.
- `templates/node_groups/*`, enrich/footer-тексты fw, `partition_planned_by_scene_chain`.

## Предсущее (не моё, падает и на чистом дереве)

- `test_excel_gpt_upload_preview.py::test_resolve_operator_upload_replaces_edge_xlsx`
- `test_excel_gpt_slot_chain.py::test_auto_chain_uses_edge_key_not_slot_resolve_collision`
