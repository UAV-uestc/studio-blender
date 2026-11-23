# Tasks

## 1. Local Package and Persistent Model

- [x] 1.1 Create `dronesAnim/light_effects/` with package-relative imports and GPL adaptation notices; verify an import scan contains no `sbstudio`, repository-path injection, server clients, or separately installed dependencies.
- [x] 1.2 Add local RNA groups for the ordered scene stack and effect properties, omitting every storyboard field; verify registration order through `auto_load` and a factory-startup Blender register/unregister smoke test.
- [x] 1.3 Implement unique effect names/IDs, owned texture creation, independent ramp duplication, active selection, and resource cleanup; verify Blender tests for create/duplicate/reorder/remove and preservation of user-owned image/mesh/group assets.
- [x] 1.4 Align the declared minimum Blender version to 4.2 and add the manifest file-access permission; verify manifest validation and add-on metadata agree.
- [x] 1.5 Start `dronesAnim/light_effects/README.md` with single-formation scope, standalone dependencies, supported versions, and resource ownership; verify it matches the persisted model and excludes storyboard attachment.

## 2. Numerical Outputs and Composition

- [x] 2.1 Port local blend and deterministic random helpers; add pure tests for all eight blend modes, source alpha semantics, seed reproducibility, and equal X/Y random offsets; verify the new tests and existing mathematical tests pass.
- [x] 2.2 Implement start/duration/end consistency, inclusive activation, source-compatible fades, and continuous temporal fraction; verify pure tests for start/end boundaries, one-frame effects, overlapping fades, and subframes, including the specification's explicit examples.
- [x] 2.3 Add deterministic natural-name drone ordering and single-formation vertex mapping; verify tests for names with numbers, nested tagged drones, untagged exclusion, sparse/duplicate indices, invalid tags, and detached drones.
- [x] 2.4 Implement endpoint, temporal, drone/formation index, all six gradients, distance, and ordered/proportional outputs; verify reference tests for source squared-origin-distance semantics, ties, coincident coordinates, empty/single inputs, and invalid prerequisites.
- [x] 2.5 Implement the local per-effect masks and stack compositor over base RGBA arrays; verify tests for ordering, enable toggles, influence, alpha, NaN/inactive outputs, and repeated input idempotence.
- [x] 2.6 Document output mappings, formation-index adaptation, fade endpoints, and squared-distance semantics in the feature README; verify each documented numeric example against the pure tests.

## 3. Presets, Images, Geometry, and Custom Functions

- [x] 3.1 Port the local preset registry and all nine source category modules with stable IDs/order and local context helpers; verify the inventory contains exactly the current 61 source IDs and enum strings remain alive across repeated selector draws.
- [x] 3.2 Add deterministic fixture comparisons for every preset plus empty/single-drone inputs; verify local results match the source reference fixtures within an explicit tolerance without requiring Skybrush in the shipped runtime.
- [x] 3.3 Implement local ramp copying/evaluation and image pixels/color-space conversion/cache invalidation; verify tests for ramp alpha/interpolation, portrait/landscape/empty images, edge coordinates, refreshed pixels, and animated-image frame changes.
- [x] 3.4 Implement evaluated-world-space containment/first-face-plane predicates and nested collection masks with inversion; verify Blender tests for transformed/deformed meshes, boundary/empty geometry behavior, missing references, and group-limited inversion.
- [x] 3.5 Implement local V1/V2 color and output function adapters with non-executing name discovery, explicit session trust, and file-refresh invalidation; verify fixtures for valid signatures, untrusted paths, import errors, runtime exceptions, and stale files.
- [x] 3.6 Document all preset categories, image coordinate behavior, target prerequisites, custom function contracts, and unrestricted approved-code execution; verify the dependency-free custom examples run only after approval in Blender.

## 4. Scene Runtime and Non-Destructive Base Colors

- [x] 4.1 Revalidate scene-scoped snapshot/result caches under evaluated-only presentation, keyed by full frame/subframe, input revisions, and actual drone identities; verify same-frame edits, navigation-order independence, reorder/rename/add/remove, and two-scene isolation.
- [x] 4.2 Implement fresh authoritative base sampling and evaluated-only material presentation without writing derived colors to original objects; verify unanimated colors, legacy/layered animation, drivers, and NLA are preserved and repeated evaluation never compounds.
- [x] 4.3 Add idempotent frame/graph/load/undo/redo invalidation and teardown that discards derived state and refreshes base presentation; verify register-unregister-register, undo/redo, load, and disable leave one handler set and correct base presentation. Keep live hooks disabled until render and undo gates pass on Blender 4.2 and 5.x.
- [x] 4.4 Connect existing Apply/Fade/UV color editing to base-color invalidation in `led_animation.py` and `led_control.py`; verify each operation writes its intended original base keys with unchanged interpolation while effects are visible.
- [x] 4.5 Add fault-isolation tests for custom-function failure and invalid mesh/image inputs; verify no partial failed-effect result is published and all guards are released for a subsequent valid update.
- [x] 4.6 Document evaluated-only presentation, base-color ownership, edit behavior, effect disabling, and lifecycle recovery in the feature README; verify the documented recovery sequence presents the expected colors.

## 5. Lighting UI and JSON Interchange

- [x] 5.1 Add the Chinese Light Effects panel and list beside the current LED controls, plus local create/remove/duplicate/reorder/set-start/set-end operators; verify all stack controls function and no Skybrush IDs or storyboard controls appear.
- [x] 5.2 Add conditional source/output/mapping/preset controls, image selection/open/refresh, mesh/group targeting, blend/influence/randomness, and custom-function trust diagnostics; verify a UI smoke test exercises all three source types without executing untrusted files.
- [x] 5.3 Implement validated name-keyed JSON serialization/deserialization for source-compatible fields, ramp metadata, and reference resolution; verify local round-trip and Skybrush-format fixture tests preserve timing, presets, and transferable settings while warning about removed storyboard linkage.
- [x] 5.4 Implement all/enabled/selected export filters, all/enabled import filters, unique imported names, and unsupported-entry diagnostics; verify empty selections, duplicate names, unknown enums/preset IDs, and absent image/mesh/group references do not lose data or broaden targets.
- [x] 5.5 Add atomic export replacement and import validation/resource rollback; verify malformed files, permission errors, and simulated interrupted writes leave no invalid entries/resources or truncated destination files.
- [x] 5.6 Document UI operation, JSON reference-only portability, filters, warning behavior, and non-executing imported Python paths; verify the documented sample JSON imports without Skybrush.

## 6. Visualization and MAT Integration

- [x] 6.1 Route viewport marker colors through the shared final-color query while material mode uses evaluated-only presentation; verify equal final RGBA in both modes and no duplicate overlays/hooks.
- [x] 6.2 Update `mat_export._sample_drones()` to obtain final effect RGB explicitly at every sample time; verify expected zero-through-255 values and unchanged MAT field/ordering layout in both visualization modes and with visualization disabled.
- [x] 6.3 Add export tests for subframe temporal effects, reordered subsets, original frame/subframe restoration, and injected sampling failures; verify the restored scene evaluates normally after success or failure.
- [x] 6.4 Headless-render a short material-effect sequence on Blender 4.2 and 5.x and compare its intended drone emission colors with final-color queries; verify procedural lighting is present in rendering, not only in viewport markers, before enabling live hooks.
- [x] 6.5 Document visualization/export agreement and the unchanged MAT schema; verify the README's sample export produces matching procedural colors.

## 7. End-to-End Compatibility and Standalone Verification

- [x] 7.1 Run syntax checks and all pure/regression tests, including existing safety and color helpers; verify passing results and confirm unrelated DroneAnim behavior is unchanged.
- [x] 7.2 Run isolated Blender installation, registration, save/reopen, UI, render, and MAT checks on available 4.2/4.4/5.x builds; record exact versions and distinguish verified builds from unavailable ones.
- [x] 7.3 Test the complete copied DroneAnim package outside the repository with Skybrush absent and no added source paths; verify ramps, all presets, images, trusted custom files, and export work using only packaged modules and Blender libraries.
- [x] 7.4 Test optional Skybrush co-installation without accessing its properties; verify distinct scene storage, operator/class IDs, and independent registration/teardown.
- [x] 7.5 Benchmark a representative 1,000-drone stack with gradient/image/preset layers and repeated subframe sampling; record timings/cache behavior and verify no cumulative growth across repeated navigation and teardown.
- [x] 7.6 Complete the feature README's verification matrix and requirement-to-test coverage summary; verify every specification scenario has a passing automated check or an explicitly recorded manual result.

## Workflow Follow-up

- Review the completed implementation and its recorded compatibility evidence before archiving this change.
- Archive only after implementation is requested, all tracked tasks are completed, and review is satisfied.
