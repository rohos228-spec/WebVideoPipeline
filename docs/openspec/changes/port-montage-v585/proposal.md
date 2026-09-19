# Change: Port Montage Board Decomposition & Ref Strips (v585)

## Why
In `origin/housepc` commit `3af95696`, the monolithic `assemble-montage-board.tsx` (~3500+ lines) was decomposed into clean, modular components:
- `montage-scene-cells.tsx` for scene cell rendering, scene group headers, actions, and ladder display.
- `montage-frame-refs.tsx` (`FrameRefsStrip`) for managing and previewing frame references, linking project assets, uploading custom image refs, and unlinking.
- Helper script `scripts/dev_check_montage_row_order.py` for verifying row order invariants.
- Documentation in `docs/MONTAGE_BOARD_IMPROVEMENTS.md`.

## What Changes
1. **Components:**
   - Add `web/src/components/canvas/montage-scene-cells.tsx`.
   - Add `web/src/components/canvas/montage-frame-refs.tsx`.
   - Update `web/src/components/canvas/assemble-montage-board.tsx` to integrate `FrameRefsStrip` and modular scene cell helpers.
2. **API & Types:**
   - Update `web/src/lib/types.ts` imports in `api.ts` (include `MontageRefAsset`).
   - Add `SceneAnchorRow`, `SceneTemplateChoice`, `SceneTemplateLadderRow`, `SceneShotRow`, `SceneEditorState`, `SceneVariantKind`, `SceneVariant` interfaces to `web/src/lib/api.ts`.
   - Add API methods: `getSceneEditor`, `getSceneVariants`, `montageRefAssets`, `linkMontageFrameRef`, `addMontageFrameRef`, `deleteMontageFrameRef`.
3. **Scripts & Docs:**
   - Add `scripts/dev_check_montage_row_order.py`.
   - Add `docs/MONTAGE_BOARD_IMPROVEMENTS.md`.

## Validation
- `pnpm run typecheck`: 0 TypeScript errors.
- `ruff check scripts/dev_check_montage_row_order.py`: 0 errors.
- `python scripts/policy_check.py`: PASS.
- Regression tests pass.
