# Change: Port Production Prompt Library & Node Group Templates

## Why
The production prompt library on `origin/housepc` consists of 85 files (~1.45 MB) including combat-tested prompts for all pipeline steps (`01_plan`, `02_script`, `03_razbivka`, `04_hero`, `05_excel_gpt`, `07_animation`, `active_variants.json`, `camera_sets.md`) and node group templates (`templates/node_groups/script_frames_qc`).
In `video-pipeline-web`, `.gitignore` previously ignored `prompts/*`, leaving only empty stubs.

## What Changes
1. Un-ignore `prompts/*` in `.gitignore` (keep only `prompts/**/.history/`).
2. Port full prompt library (85 files) from `origin/housepc:prompts/`.
3. Provide default documentary script plan prompt for `prompts/01_plan/default.md`.
4. Port `templates/node_groups/script_frames_qc/` (7 files: script writer, scenes to frames, QC, continuity).
5. Fix Windows UTF-8 stdout encoding in `scripts/check_prompts.py`.

## Validation
- `python scripts/check_prompts.py`: PASS (Core required prompts present).
- `ruff check scripts/check_prompts.py`: 0 errors.
- `python scripts/policy_check.py`: PASS.
- `pnpm run typecheck`: 0 errors.
- `pytest tests/test_prompt_*.py tests/test_stage_prompts_contract.py`: 70/70 passed.
