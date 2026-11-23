# DroneAnim Light Effects

Light Effects are implemented in DroneAnim as a self-contained feature for
a single formation. The implementation targets Blender 4.2 and 5.x and uses
only Blender's Python environment, including its bundled NumPy. Skybrush and a
network connection are not required.

Each scene stores its own ordered effect stack, timing, settings, and asset
references. An effect owns its ramp texture; duplicating the effect creates an
independent texture and ramp. Images, meshes, and collections selected by users
remain user-owned. Removing an effect deletes only its unreferenced owned
textures, not the selected assets.

The stack uses absolute frame timing and does not attach effects to storyboard
entries or transitions. The runtime, sidebar, interchange, custom-function
evaluator, and visualization/export integration are implemented. The tested
builds and verification limits are recorded below.

## Output and Timing Rules

Output X selects a position on a color ramp or the horizontal axis of an
image. Image effects also use Output Y. Built-in coordinates include first or
last color, time, drone order, formation vertex index, six spatial gradient
orders, and distance from a mesh object's world-space origin. The evaluator also
supports custom functions and all 61 migrated presets.

Ordered mapping distributes sorted drones evenly. Proportional mapping scales
the principal coordinate between its minimum and maximum. For example, X
positions 0, 1, and 4 map to 0, 0.5, and 1 in ordered mode, or 0, 0.25, and 1
in proportional mode. Distance uses squared distance to the mesh origin: at
distances 0, 1, and 2, proportional outputs are 0, 0.25, and 1.

Formation-index mapping ranks distinct nonnegative vertex indices. Indices 2,
7, 7, and 12 map to 0, 0.5, 0.5, and 1; invalid indices do not receive a
formation-index output. Drone order follows the same natural-name sorting as
MAT export.

The end frame is `start + duration - 1`, and an effect is active from its
start through its end. Temporal output varies continuously at subframes:
frames 10 through 14 yield 0.125 at frame 10.5. As in Skybrush, with start 10,
end 14, fade-in 2 and fade-out 2, influence at frames 10, 11, 13 and 14 is
0.5, 1, 0.5 and 0 respectively. This means the end-frame influence is zero
when fade-out is enabled.

## Sources and Targets

The preset catalog includes basic, chasing-tail, filling, generic numbered,
group-range, radar-scan, radial, sweep, and wave effects. Preset IDs follow the
Skybrush catalog. Some original presets produce non-finite coordinates for a
single drone; such coordinates must be treated as inactive by evaluation.

Image coordinates use X for columns and Y for rows, with nearest-pixel
sampling and coordinates clamped to the image edges. Alpha is preserved. sRGB
pixels use Skybrush's `value ** 2.2` linearization; linear and data images are
left unchanged. Unsupported color spaces require a diagnostic. Animated image
frames are sampled separately and cached for the requested frame.

Spatial targets require a usable evaluated mesh. Containment includes the
surface; the front-plane target uses the first face and includes its boundary.
An optional collection includes nested collections. Inversion applies to the
spatial result inside that collection. Missing required references must make
the effect inactive, not expand its target.

## Custom Functions

The legacy color or output function receives six keyword arguments:
`frame`, `time_fraction`, `drone_index`, `formation_index`, `position`, and
`drone_count`. A color function returns RGBA; an output function returns a
scalar. For example:

```python
def legacy_color(frame, time_fraction, drone_index, formation_index, position, drone_count):
    return (time_fraction, 0.0, 1.0 - time_fraction, 1.0)
```

Vectorized functions receive `(effect, context, frame, *, out)`. Color output
has shape `(N, 4)` and scalar output has shape `(N,)`. Write to `out` or return
one common color or scalar:

```python
def vector_output(effect, context, frame, *, out):
    out[:] = effect.get_time_fraction_for_frame(frame)
```

Listing functions reads Python syntax without executing it. Running a function
requires explicit approval for that exact file content in the current session.
Changing the file requires approval again. Approved Python is **not sandboxed**
and can access everything available to Blender.

## Sidebar and JSON

Open the **灯光控制** sidebar tab and its **灯光效果** panel. Create an effect
with `+`; the list also offers remove, duplicate, and reorder actions. Effects
compose in displayed order. Select an entry to edit absolute timing, fades,
the color source, output mappings, targeting, blending, and random offsets.
The start/end buttons use the current integer frame. Image refresh clears
cached samples after pixel edits.

For custom functions, select a Python file, use **列出函数** for syntax-only
discovery, and enter its function name. **批准执行** asks for confirmation of
unrestricted execution; **撤销批准** stops further use. Panel drawing and
discovery do not execute the file.

**导出 JSON** supports all, enabled, or the current selected effect. **导入 JSON**
supports all or enabled entries and creates uniquely named copies. Transfers
use Skybrush's camelCase settings and name-keyed format. Per-entry `droneAnim`
metadata preserves the global random seed without changing the transferable
Skybrush fields.

JSON contains asset names, not image pixels or mesh/collection geometry.
Supply assets separately and make them available under matching names.
Unresolved references are reported and imported entries are disabled, with
their names retained for re-export. A missing required collection remains
inactive even if manually enabled, until a collection is assigned. Unsupported
enums/presets are skipped with a warning; malformed settings reject the import
before committing entries. Interrupted assignment rolls back created textures.
Exports replace an existing destination only after a complete write.

Storyboard linkage is discarded with a warning; absolute timing is retained.
Imported Python paths are data, never consent: importing requires renewed
approval for those files, even if previously approved in the session.

This minimal dependency-free example creates a red-to-blue temporal ramp:

```json
{
  "Temporal ramp": {
    "frameStart": 1,
    "duration": 3,
    "output": "TEMPORAL",
    "texture": {
      "colorRamp": {
        "elements": [
          {"position": 0, "color": [1, 0, 0, 1], "alpha": 1},
          {"position": 1, "color": [0, 0, 1, 1], "alpha": 1}
        ]
      }
    }
  }
}
```

## Visualization and MAT

Material display, marker samples, and MAT export query the same ordered final
RGBA snapshot. MAT always includes enabled effects, including in marker mode
or with visualization disabled. Spatial ordering and formation mapping use
the complete participating formation before any export subset is selected.

The MAT layout remains `r`, `b`, `g`, `x`, `y`, `z`, and zero-based `Qn`.
RGB is clamped to zero through one and multiplied by 255 without adding a new
gamma conversion. For the example above, frames 1, 1.5, 2, and 3 export
red values 255, 191.25, 127.5, and 0, with blue 0, 63.75, 127.5, and 255.
Success and sampling failure both restore the original frame/subframe and
safety scanning state.

## Base Colors and Lifecycle

Original object colors and their animation remain the authoritative base.
Effects do not insert keys or change F-curves, drivers, or NLA. Material
presentation writes only evaluated object colors. Fresh dependency-graph
evaluation discards previous display overrides before sampling a new base, so
repeated updates do not compound the stack. Evaluated objects and graphs are
not retained between updates.

Apply and Fade continue writing original object-color keys. UV sampling remains
a one-shot base-color operation, with its existing constant hold behavior.
These operators suspend procedural publication during the batch, invalidate
the snapshot, and reevaluate the completed edit.

Disabling the stack or its last active effect discards procedural presentation
and refreshes the base. Removing the add-on removes its handlers and caches
without removing user-owned assets or changing original colors. Undo/redo
discards snapshots before Blender replaces scene data. Save/load keeps effect
settings and original bases; custom-file trust is cleared on reopening.

For recovery, disable the stack and update the scene, or disable the add-on.
The evaluated colors then match the current base animation. Re-enabling the
stack recomputes effects over that base. A failed individual effect leaves
prior successful layers intact and reports its name; interrupted publication
discards partial evaluated writes and releases the reentrancy guard.

## Verification

The scene model and texture ownership tests run in Blender background mode:

```text
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_effect_model.py
python -m pytest test/test_drones_anim_light_math.py test/test_drones_anim_light_indexing.py
```

Runtime verification currently covers Blender **4.2.0** (a51f293548ad) and
**5.2.0 LTS** (fbe6228777e7) on Windows. Legacy/layered actions, drivers, NLA,
same-frame updates, subframes, scene isolation, Apply/Fade/real UV keys,
save/reopen, undo/redo, teardown, and failure recovery passed. Blender 4.4
and other 5.x builds have not yet been tested.

The material render probe passed in Cycles CPU and EEVEE on both tested builds:
the temporal effect rendered blue at frame 1 and green at frame 2, matching
the final-color query while original colors stayed red. A viewport-only
override did not reach rendering, confirming that render-graph publication is
required.

Run each integration script in a separate factory-startup Blender process;
their scene fixtures are not designed to be concatenated in one process.

```text
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_runtime.py
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_animation_runtime.py
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_lifecycle.py
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_base_edits.py
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_runtime_failures.py
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_evaluated_render.py -- --runtime
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_evaluated_render.py -- --runtime --eevee
```

### Compatibility Matrix

All checks used factory-startup scenes, separate from the user's open scene.
Copied-package checks used a temporary `BLENDER_USER_SCRIPTS` installation;
the installed add-on did not add repository paths or install dependencies.
Skybrush was absent for standalone checks and staged separately only for
co-installation checks.

| Check | 4.2.0 / a51f293548ad | 5.2.0 LTS / fbe6228777e7 |
| --- | --- | --- |
| Registration, scene storage, resource ownership | PASS | PASS |
| Copied package: ramp, all 61 presets, image, approved function, MAT | PASS | PASS |
| Runtime, animation, drivers, NLA, scene isolation, subframes | PASS | PASS |
| Save/reopen: order, IDs, preset, ramp, asset references, trust reset | PASS | PASS |
| Undo/redo, disable, teardown, registration repeat, fault recovery | PASS | PASS |
| Existing Apply/Fade/UV keys and interpolation | PASS | PASS |
| Evaluated geometry and animated image samples | PASS | PASS |
| UI operators, conditional controls, non-executing discovery | PASS | PASS |
| Actual panel popup, Chinese labels, ramp editor: screenshot review | PASS | PASS |
| JSON fixtures, README example, validation, rollback, atomic writes | PASS | PASS |
| Marker/material/MAT agreement, subset order, failure restoration | PASS | PASS |
| Cycles CPU and EEVEE: still and two-frame animation | PASS | PASS |
| Actual Skybrush co-installation in GPU-initialized GUI process | Not run | PASS |
| 1,000-drone timing, cache bounds, retained memory, teardown | PASS | PASS |

Blender 4.4 and other 5.x builds were unavailable and are **not verified**.
The GUI screenshot check draws the registered panel in a popup; automated UI
tests exercise the operators and conditional drawing branches, not every
mouse-driven file-selector interaction. Co-installation requires a foreground
GPU context because Skybrush's own overlays do not support the background
fixture. The passing GUI run asserted that no callback errors were emitted.

Pure regression verification: **716 passed** on Python 3.11.15, including
existing safety, color, plane, and random-sequence tests. Syntax compilation
and Ruff checks pass for the migration package and its test fixtures. Runtime
dependency inspection found only local modules, standard-library modules,
Blender APIs, and bundled NumPy. The source-parity tests intentionally read
the repository's Skybrush reference modules; shipped DroneAnim does not.

### Benchmark

The fixture creates 1,000 drones and three gradient/image/preset layers,
samples 20 half-frame times per pass, revisits frames repeatedly, and checks
bounded result/image caches and teardown. Timings include `tracemalloc`
instrumentation overhead and are **not** uninstrumented production estimates.

| Build | Median frame ms | Max frame ms | Median cached query ms | Retained growth bytes |
| --- | ---: | ---: | ---: | ---: |
| 4.2.0 | 139.204 | 199.546 | 22.784 | 116 |
| 5.2.0 LTS | 176.999 | 221.628 | 22.883 | 28 |

Traced Python peaks were 996,024 and 983,184 bytes respectively. Cache bounds,
negligible retained-growth assertions, and complete runtime teardown passed.
This does not measure all native Blender allocations or guarantee real-time
playback on other hardware.

```text
blender --background --factory-startup --python-exit-code 1 --python test/blender_light_benchmark.py
```

### Requirement Coverage

Every scenario in the `droneanim-light-effects` specification maps below to
passing checks. Names are abbreviated only in this table; test paths are
relative to `test/`. All listed Blender fixtures passed on both tested builds
unless the compatibility matrix states otherwise.

| Requirement / Scenarios | Passing Evidence |
| --- | --- |
| Standalone: isolated installation; supported animation APIs | `blender_light_standalone.py`, `blender_light_animation_runtime.py`, GUI screenshot review |
| Stack: duplicate/reorder; empty stack | `blender_light_effect_model.py`, `blender_light_ui.py`, `blender_light_runtime.py` |
| Absolute timing: update timing; no storyboard dependency | `test_drones_anim_light_math.py`, `blender_light_ui.py`, RNA/UI source inspection |
| Fades: source endpoints; subframe progression | `test_drones_anim_light_math.py`, `blender_light_mat.py` |
| Sources: ramp endpoints; independent image axes; dependency-free function | `blender_light_effect_model.py`, `blender_light_evaluator.py`, `blender_light_custom_examples.py` |
| Outputs: ordered/proportional gradient; squared-origin distance | `test_drones_anim_light_indexing.py`, `blender_light_evaluator.py` |
| Indexing: sparse/duplicate indices; unrelated/detached objects | `test_drones_anim_light_indexing.py`, `blender_light_effect_model.py` |
| Presets: complete catalog; numerical parity and persistence | `test_drones_anim_light_presets.py` (61 IDs, `rtol=atol=1e-6`), `blender_light_standalone.py`, `blender_light_lifecycle.py` |
| Targets: inverted group limit; animated geometry; missing target | `blender_light_geometry.py`, `blender_light_runtime_failures.py`, `blender_light_json.py` |
| Composition: repeated navigation; disabled effect | `test_drones_anim_light_math.py`, `blender_light_runtime.py`, `blender_light_lifecycle.py` |
| Base ownership: same-frame edit/removal; Apply/Fade; undo/redo | `blender_light_runtime.py`, `blender_light_animation_runtime.py`, `blender_light_base_edits.py`, `blender_light_lifecycle.py` |
| Visualization/export: marker-mode RGB; failed export restoration | `blender_light_mat.py`, `blender_light_evaluated_render.py` |
| Images: non-square edges; refreshed pixels | `test_drones_anim_light_images.py`, `blender_light_image_sequence.py`, `blender_light_ui.py` |
| Lifecycle: reload/teardown; independent scenes | `blender_light_lifecycle.py`, `blender_light_runtime.py`; optional `blender_light_gui_coinstall.py` |
| JSON: source-compatible import; missing image; export filters; invalid input/write | `blender_light_json.py` (also imports the README example) |
| Trust: imported/reopened untrusted path; failed approved function | `blender_light_json.py`, `blender_light_lifecycle.py`, `blender_light_ui.py`, `test_drones_anim_light_functions.py`, `blender_light_runtime_failures.py` |

For a complete isolated run, execute the model, runtime, animation runtime,
lifecycle, evaluator, geometry, image sequence, base edits, runtime failures,
UI, JSON, MAT, custom examples, standalone, benchmark, and both render
fixtures separately. The standalone fixture expects `BLENDER_USER_SCRIPTS`
to point to the temporary copied installation. GUI fixtures additionally use
`DRONEANIM_UI_OUTPUT` for screenshots; the optional co-installation fixture
requires Skybrush's own separately installed package and dependencies.


## Shared video/procedural list

The lighting sidebar contains **LED** (ordinary color and UV sampling) and
**灯效** (one shared list). Use **程序灯效** or **视频灯效** to add a row. Lower
rows overlay upper rows; moving a video across a procedural row changes their
composition order. Both types use the same master switch, timing, fades,
influence and blend controls. Video rows additionally expose a local source,
source-duration lock/looping, and captured projection view/targets.

Legacy independent video lists migrate on registration/load, preserving their
priority over existing procedural effects and converting scene keyframe paths.
JSON retains video texture names, source frame counts, projection matrices,
target names and range locks. It references existing assets and does not open
external video files automatically. Missing references import disabled. These
video JSON rows are a DroneAnim-specific extension.

Run `test/blender_unified_lights_smoke.py` with Blender's `--background
--factory-startup --python-exit-code 1 --python` flags to verify the shared list,
legacy migration and panel draw branches. Decoded video pixels are synthetic in
this test; native video decoding and interactive export require GUI validation.
