# Change: Port Gen Assistant (Generation Assistant & Style Analyzer)

## Why
On `origin/housepc` (commits `4ce3fe1b`, `6147706e`, `ee83d684`, `f6c84ea8`, `b0eb7106`), the generation creation wizard was upgraded with an intelligent Prompt & Style Assistant ("Помощник генерации"):
1. Multi-modal style analyzer (`app/services/style_analyzer.py`): analyzes reference image batches, generates structured style cards, categorizes visual aesthetics.
2. Prompt generation assistant (`app/services/gen_assistant.py`): creates LLM-guided visual prompts with strict JSON contracts, fills style slots, enforces negative bans, and handles draft jobs.
3. Fast API router (`app/web/routers/gen_assistant.py`) for style analysis, agent building, custom styles persistence, and style cover serving.
4. Rich interactive frontend UI (`web/src/components/outsee/gen-assistant-panel.tsx`, `gen-style-art.tsx`, `web/src/lib/gen-assistant-styles.ts`) integrated into `outsee-create-workspace.tsx` with cover presets and live preview.

## What Changes
1. **Backend Services & Routers:**
   - Add `app/services/gen_assistant.py`.
   - Add `app/services/style_analyzer.py`.
   - Add `app/web/routers/gen_assistant.py`.
   - Add `scripts/analyze_style_folder.py`.
   - Add `templates/gen_assistant_styles.json`.
   - Register `gen_assistant` router and `gen-styles/` cover handler in `app/web/api.py`.
2. **Frontend UI & Components:**
   - Add `web/src/lib/gen-assistant-styles.ts`.
   - Add `web/src/components/outsee/gen-style-art.tsx`.
   - Add `web/src/components/outsee/gen-assistant-panel.tsx`.
   - Update `web/src/components/outsee/outsee-create-workspace.tsx` with assistant toggle, draft jobs, and KIE catalog integration.
   - Add default style cover images in `web/public/gen-styles/`.
3. **Tests:**
   - Add `tests/test_gen_assistant.py`.
   - Add `tests/test_style_analyzer.py`.

## Validation
- `pnpm run typecheck`: 0 TypeScript errors.
- `ruff check`: 0 errors.
- `python scripts/policy_check.py`: PASS.
- `pytest tests/test_gen_assistant.py tests/test_style_analyzer.py`: 43/43 passed.
