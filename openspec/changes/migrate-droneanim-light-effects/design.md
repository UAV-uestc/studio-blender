# Design

## Context

See `proposal.md` for motivation and scope. The source entry point is `src/modules/sbstudio/plugin/panels/light_effects.py`; most behavior resides in `plugin/model/light_effects.py`, the update/session task package, and the nine preset implementation modules.

DroneAnim's current integration points are:

- `led_animation.get_drones(scene)` selects tagged mesh objects, including nested collections. `drones_anim_vertex_index` supplies single-formation identity; the `Drone Anim Vertex` constraint identifies the attachment source.
- `led_animation.insert_color()` supports legacy and layered actions. Created LED materials read Object Info Color, and existing Apply/Fade/UV operations write object-color keyframes.
- `led_control._draw_markers()` reads evaluated object colors; its frame handler currently only redraws.
- `mat_export._sample_drones()` samples evaluated object RGB at integer frames and subframes, independently of visualization mode.
- `auto_load.py` recursively discovers module classes and runs module registration hooks. New property groups must participate in its dependency ordering without duplicate class registration.
- There are no existing OpenSpec capabilities. `test/test_drones_anim_safety_math.py` demonstrates loading a pure numerical module without importing the add-on's Blender entry point.
- The user confirmed that storyboard logic must be discarded and Blender 4.2 and 5.x supported.

## Goals / Non-Goals

**Goals:**

- Establish one authoritative, scene-aware lighting evaluator for viewport display and export.
- Keep Blender RNA/UI code separate from numerical evaluation so most behavior is testable without Blender.
- Preserve source effect identifiers, mathematical semantics, and portable preset IDs while replacing show-specific dependencies.
- Make ownership, handler teardown, non-destructive presentation, and custom-code trust explicit.

**Non-Goals:**

- Reproduce Skybrush's show model, callback framework, server/API clients, or storyboard abstractions.
- Rewrite arbitrary user Python modules that import Skybrush.
- Embed image pixel payloads in JSON, change the existing MAT format, or introduce a bake-to-keyframes tool.

## Decisions

### 1. Own the implementation in a local package

Create `dronesAnim/light_effects/` with `model.py`, `math.py`, `context.py`, `evaluator.py`, `runtime.py`, `operators.py`, `ui.py`, `io.py`, `images.py`, `functions.py`, `compat.py`, and `presets/`. The final file grouping can remain small where responsibilities naturally fit, but every required helper stays under `dronesAnim`.

Port the necessary blend, deterministic random, image/color-space, ramp-copy, spatial predicate, and function-adapter behavior with GPL attribution. Rewrite all imports, including type-only imports, to local relative imports or Blender-provided libraries. Do not manipulate `sys.path` or import from `src/modules`. NumPy is supplied by Blender; no wheels or pip installation are required.

Alternative: import or vendor the whole `sbstudio` package. Rejected because it couples registration and runtime to a much larger show framework and violates standalone installation.

### 2. Use local RNA storage with explicit resource ownership

Attach a collection property group to `Scene.drones_anim_light_effects`. It owns a master enable flag, ordered entries, active index, and persisted randomness seed. Each entry owns its stable ID, name, enable flag, absolute timing, source/output settings, target references, and source-compatible blend identifiers.

Keep a Blender texture per entry to provide the native ramp editor and image pointer, as in the source. Duplicate ramp/texture state independently; sharing an image datablock remains intentional. Track owned resources explicitly and remove only orphaned resources created for these effects. Never remove a user-selected mesh, collection, image, or Python file.

Expose the new panel as a sibling of the existing LED control panel in the existing lighting sidebar, with Chinese labels and no Skybrush operator IDs. Keep RNA properties expressed in a form `auto_load` can inspect; avoid postponed string annotations on Blender property declarations. Use existing auto-discovery rather than registering discovered classes twice.

Alternative: flatten all fields into the existing LED property group. Rejected because the ordered effect stack has its own persisted lifecycle and active selection.

### 3. Replace storyboard mapping with stable single-formation identity

Use natural-name ordering compatible with existing MAT ordering for drone-row indexing. Freeze that ordering within an evaluation snapshot. Derive formation indices from nonnegative integer vertex tags, without requiring a currently active attachment constraint, so detached tagged drones remain manageable.

For indexed-by-formation ramps, rank valid vertex indices and normalize by the number of distinct valid indices minus one, matching the source treatment of sparse mappings. Duplicate vertex indices share the same output. Invalid indices produce an inactive result for formation-index output; presets retain the source helper's documented default index of zero for unmapped rows. Exclude untagged/non-mesh objects throughout.

Remove storyboard fields, imports, dynamic timing offsets, and Attach-to controls entirely. Timing stays absolute: `end = start + duration - 1`, with duration at least one and an inclusive active interval. Preserve source integer-frame fades and time fraction, extending their calculation continuously to subframes without changing integer-frame results. In particular, preserve the source fade-out value of zero at the end frame when fade-out is enabled.

Alternative: add local storyboard-shaped time anchors. Rejected by the user's single-formation scope decision.

### 4. Preserve source and output behavior through a shared evaluation context

Build an immutable input snapshot with scene, frame plus subframe, ordered drone identities, evaluated world positions, base RGBA, vertex mapping, and deterministic per-drone random values. Numerical work uses vectorized arrays and per-effect masks. Apply enabled, in-range effects in displayed order.

Retain color-ramp, image, and custom color-function sources. Outputs include first/last color, temporal fraction, drone/formation index, all six axis-order gradients, distance, legacy/custom V2 functions, and light presets. Ordered mapping distributes ranked rows; proportional mapping rescales the principal coordinate. Preserve source distance semantics, which use squared distance to the mesh object's world origin, not closest-surface distance.

Port the source V1/V2 adapters with local protocols and equivalent arguments/context fields, so dependency-free user functions remain compatible. Preserve all preset IDs and registration order from `basic`, `chasing_tails`, `filling`, `generic`, `groups`, `radar_scan`, `radial`, `sweep`, and `wave`; there are currently 61 registered presets. Store enum item strings for the registration lifetime.

Preserve all eight blend modes, source influence/fade composition, alpha semantics, deterministic randomness, and identical per-drone random offsets on image X and Y. Seed/identity-based randomness must not depend on frame navigation order. Use reference fixtures from the current source to check parity; unsupported or invalid prerequisites must not silently become an all-drone effect.

Alternative: copy only ramp gradients and a subset of presets. Rejected because the requested migration covers the section's functionality, not a simplified replacement.

### 5. Separate authoritative base colors from derived display colors

Expose a shared final-color query used by markers and MAT export even when material visualization is off. Effect evaluation itself never inserts keys or modifies F-curves.

The material path uses Object Info Color, but derived presentation must never write to original object colors: Blender can capture such writes in undo snapshots and subsequently compound effects. Publish material presentation only to evaluated objects, without retaining those objects or dependency graphs across updates.

- Obtain authoritative base colors from a freshly evaluated graph before any presentation override, leaving actions, drivers, and NLA to Blender. Ensure subsequent evaluations cannot sample prior derived overrides; the exact graph refresh and callback ordering must be established by tests.
- Keep Apply/Fade/UV operations editing original base colors and invalidate the final-color snapshot after edits. They must never keyframe a procedural result.
- Markers read the final-color query directly; MAT export obtains final RGB explicitly, independently of material presentation.
- On disabling effects, switching visualization, loading, undo/redo, failure, or teardown, discard derived state and ensure a fresh evaluation presents the correct base. Release all reentrancy guards even on failure.
- Keep live presentation hooks disabled until actual material renders, undo/redo, and base sampling are verified on Blender 4.2 and a 5.x build. If evaluated-only publication does not reach a supported render path, stop and resolve the architecture instead of falling back to original-object writes.

Cache results by scene identity, full frame/subframe, ordered drone identities, and input revision, not integer frame alone. Property callbacks, relevant dependency graph updates, edits, undo/redo, image refresh, and drone membership changes invalidate snapshots. Guard evaluated presentation against recursive updates.

Alternative: temporarily write original object colors and restore recorded bases. Rejected because undo snapshots can capture derived values. Manually evaluating object-color F-curves is also rejected because it loses drivers, NLA, and layered-action behavior.

### 6. Evaluate images and geometry in the current dependency graph

Cache pixels with image identity, dimensions, color-space metadata, and revision. Index row-major `(height, width, RGBA)` arrays, clamp edge coordinates, convert supported image colors to scene-linear RGB, and preserve alpha. Clear dynamic-image entries on frame changes and static entries on explicit refresh/load/undo. Keep this separate from existing one-shot UV sampling.

Spatial targeting uses the current evaluated mesh in world space for containment or the oriented first-face plane, with deterministic handling of boundaries and empty meshes. Collection masks include nested collection membership. Inversion complements the spatial predicate inside the allowed drone group. Missing selected prerequisites make that effect inactive with a diagnostic instead of widening its target. Free temporary evaluated meshes and invalidate cached spatial data on transformations or geometry updates.

Alternative: reuse UV sampling as image effect evaluation. Rejected because procedural X/Y outputs and UV attachment sampling have different inputs and semantics.

### 7. Preserve JSON interoperability without importing trust

Export source-compatible name-keyed JSON with transferable camelCase fields and ramp/texture metadata, plus a namespaced metadata block for format version/global seed if needed. Ensure unique effect names so entries cannot overwrite each other. Include all/enabled/selected export filters and all/enabled import filters.

Read plain Skybrush name-keyed files without requiring new metadata. Strip storyboard linkage with a warning and retain serialized absolute timing. Resolve mesh, collection, and image names only against current-file datablocks. JSON references images by name, not pixels; cross-machine image files must be supplied separately. Warn and skip/disable unsupported effects or unresolved required references without silently substituting an unrestricted target.

Validate input before committing each entry and reject malformed top-level input without leaving partial entries/resources. Use atomic destination replacement for export. External paths are data, not executable import instructions.

Custom function execution requires an explicit session-level user trust action for the selected file; loading a `.blend`, parsing JSON, drawing panels, or enumerating names must not execute it. Inspect function names without importing code, then load/cache a trusted file on demand and invalidate on file refresh. Report trust, signature, load, and runtime errors with the affected effect name; never execute code from JSON itself. Arbitrary trusted Python is not sandboxed, and this must be disclosed in the UI.

Alternative: automatically import files on panel draw, as a convenient function picker. Rejected because saved/imported paths are not consent to code execution.

### 8. Integrate lifecycle and compatibility deliberately

Align `bl_info` with the confirmed 4.2 minimum; keep the manifest at 4.2 with 5.x awareness. Add an explained file-access permission for JSON and custom files. Keep version-specific action/GPU/RNA differences in compatibility helpers and retain existing layered-action support.

Use idempotent persistent handlers for frame, graph, load, and undo/redo changes once the presentation validation gates pass. Discard derived state, refresh base presentation, clear caches, remove handlers and owned draw hooks, then remove the scene property during teardown before auto-loader unregisters classes. Opening multiple scenes or installing alongside Skybrush must not share mutable effect state, registry IDs, or callbacks.

Alternative: reuse Skybrush's global task/callback objects. Rejected because they introduce hidden cross-add-on dependencies and teardown collisions.

## Risks / Trade-offs

- Evaluated-only writes may be discarded on graph refresh or not reach a render engine. Mitigation: verify actual material renders and callback ordering on Blender 4.2 and 5.x before enabling live hooks; revisit the architecture if either fails.
- Previously published evaluated colors may be mistaken for base colors. Mitigation: establish fresh graph evaluation before sampling, guard callbacks, and test same-frame idempotence, base editing, and undo/redo without original-object writes.
- Blender actions, drivers, NLA, and rendering can evaluate in different contexts. Mitigation: test animated and unanimated bases and compare headless renders with final-color queries.
- Mathematical source parity includes unintuitive distance/fade semantics. Mitigation: retain and document those semantics rather than silently changing them during migration.
- Image, mesh, collection, and function references are not self-contained JSON assets. Mitigation: document reference-only interchange, provide warnings, and test missing prerequisites.
- Custom Python can access the full Blender process after approval. Mitigation: explicit per-session trust, no automatic execution, and clear diagnostics; no sandbox claim.
- Invalid image dimensions, animated geometry, and large formations can exhaust caches or block the UI. Mitigation: bounded per-scene snapshots, revision-based invalidation, vectorized numerics, empty-input checks, and a 1,000-drone benchmark recorded during implementation.
- Local adaptation can drift from upstream presets. Mitigation: inventory stable IDs, retain GPL notices, and compare a representative deterministic fixture for every preset.
- Actual Blender executables might not cover every supported minor release. Mitigation: test available 4.2/4.4/5.x builds, record the exact matrix, and distinguish untested versions from verified versions.

## Migration Plan

1. Implement the local model and numerical/preset layer, then scene runtime and shared color access.
2. Add UI/IO and integrate existing LED editing, markers, materials, and MAT export.
3. Validate pure tests and isolated Blender lifecycle/render/export tests with only the packaged `dronesAnim` directory installed.
4. Save/reopen a DroneAnim effect scene; test optional JSON transfer from Skybrush, without automatic `.blend` conversion.
5. Distribute the complete DroneAnim package after passing the supported-version checks. Do not modify Skybrush or the pre-existing ZIP files as part of this planning change.
6. Rollback by disabling the feature/add-on through its teardown, which discards derived state, refreshes base presentation, and removes runtime hooks. Existing object animation remains intact; preserve a scene backup before deploying a new add-on version.
