# Proposal

## Why

DroneAnim currently supports LED keyframes and one-shot UV image sampling, but lacks Skybrush's dynamically evaluated Light Effects stack. Migrating that functionality into DroneAnim enables procedural lighting for its single formation without installing Skybrush or importing its internal modules.

## What Changes

- Add a Light Effects panel alongside the existing LED controls in DroneAnim's lighting sidebar, following the existing Chinese UI convention.
- Support a global enable switch and named, individually enabled effects with create, remove, duplicate, and reorder operations.
- Migrate color-ramp, image, and custom Python color functions; absolute start/duration/end timing; fades; output X/Y functions and mapping; all source built-in presets; mesh/group targeting; inversion; blend modes; influence; and deterministic randomness.
- Preserve drone-index and formation-index outputs using DroneAnim-owned drone identification and single-formation vertex indices. Do not add a storyboard, transition model, or storyboard attachment controls, as confirmed by the user.
- Evaluate effects over existing base LED colors without inserting or modifying keyframes; use the same final colors for materials, markers, and MAT export, including subframe sampling.
- Add self-contained JSON import/export and image-cache refresh. Support the transferable fields of Skybrush JSON with explicit warnings for omitted storyboard fields, missing references, and unsupported entries.
- Keep all runtime implementation and required helpers inside `dronesAnim`, using package-relative imports and only Blender-provided Python libraries, including NumPy. External user-selected custom function files remain an explicit opt-in feature.
- Support Blender 4.2 and 5.x, aligning the conflicting add-on version declarations and ensuring clean registration, reload, and teardown.

## Capabilities

### New Capabilities

- `droneanim-light-effects`: Owns the standalone procedural lighting stack, UI and lifecycle, source/output evaluation, single-formation targeting, presets, persistence, JSON interchange, and integration with existing LED visualization and export.

### Modified Capabilities

None. The existing OpenSpec capability inventory is empty.

## Impact

- New implementation package under `dronesAnim/light_effects/`, with local model, evaluator, operators, UI, IO, caching, compatibility helpers, and preset modules.
- Integration changes to `dronesAnim/led_animation.py`, `led_control.py`, `mat_export.py`, and, where needed, `auto_load.py`; minimum-version alignment in `__init__.py` and file-access permission in `blender_manifest.toml`.
- New pure numerical tests and Blender smoke/integration tests; source behavior comes from `src/modules/sbstudio/plugin/{panels,model,operators,tasks,presets}/light_effects*` and supporting blend/image/random helpers.
- No runtime `sbstudio` imports, Skybrush registration, server/network dependencies, pip-installed dependencies, or changes to the Skybrush implementation. Preserve GPL-3.0-or-later notices and adaptation attribution.
- Out of scope: multi-formation/show orchestration, storyboard timing, automatic conversion of Skybrush `.blend` scene properties, general external-function dependency rewriting, or unrelated safety/animation changes.
