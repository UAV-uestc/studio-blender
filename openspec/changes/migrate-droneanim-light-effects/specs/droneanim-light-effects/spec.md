# Spec Delta

## Purpose

Enable DroneAnim users to compose procedural LED effects for a single formation, with standalone persistence and consistent colors across visualization and export.

## ADDED Requirements

### Requirement: Standalone installation and supported versions

The feature SHALL work with only DroneAnim installed on Blender 4.2 and 5.x, without Skybrush, network access, or separately installed Python dependencies. Existing LED and animation functionality SHALL remain available.

#### Scenario: Isolated installation
- **WHEN** the packaged DroneAnim directory is installed without Skybrush or the repository's source modules
- **THEN** the add-on registers and its Light Effects panel, built-in effects, LED controls, and MAT export work without dependency errors

#### Scenario: Supported animation APIs
- **WHEN** the user applies effects to animated drones using legacy actions or supported layered actions on the respective supported Blender versions
- **THEN** base animation and procedural lighting evaluate correctly

### Requirement: Lighting sidebar and effect stack management

The feature SHALL expose a Light Effects panel in DroneAnim's existing lighting sidebar, using its Chinese UI convention. Users SHALL enable the stack, create and rename effects, toggle entries, remove, duplicate, and reorder entries. Names SHALL be unique for lossless name-keyed export.

#### Scenario: Duplicate and reorder an effect
- **WHEN** the user duplicates an effect with a configured color ramp, edits the copy, and moves it above another effect
- **THEN** the original ramp remains unchanged, the copy has a distinct identity and unique name, and subsequent evaluation uses the displayed order

#### Scenario: Empty effect stack
- **WHEN** the last effect is removed or no effect has been created
- **THEN** the panel remains usable and drone colors equal their base LED colors

### Requirement: Absolute single-formation timing

Effects SHALL use absolute start frame, duration of at least one frame, and inclusive end frame equal to start plus duration minus one. Users SHALL set start or end to the current integer frame. The feature SHALL NOT expose or depend on storyboard attachments, transition timing, or linked offsets.

#### Scenario: Update timing
- **WHEN** an effect starts at frame 10 and its duration is set to 5
- **THEN** its end frame is 14 and it is inactive before frame 10 and after frame 14

#### Scenario: No storyboard dependency
- **WHEN** the user opens the Light Effects panel or changes an effect's timing
- **THEN** no Attach-to or storyboard offset controls appear and no storyboard data is required

### Requirement: Fade and subframe timing semantics

Effects SHALL preserve Skybrush's integer-frame fade and temporal-fraction results and evaluate those formulas continuously at subframes. Influence SHALL be clamped to the range zero through one after fade composition.

#### Scenario: Source fade endpoints
- **WHEN** an effect starts at frame 10 with duration 5, influence 1, fade-in 2, and fade-out 2
- **THEN** effective influence is 0.5 at frame 10, 1 at frame 11, 0.5 at frame 13, and 0 at frame 14

#### Scenario: Subframe progression
- **WHEN** a temporal ramp runs from frame 10 through frame 14 and is sampled at frame 10.5
- **THEN** its temporal coordinate is 0.125 rather than the integer-frame coordinate zero

### Requirement: Color sources

Users SHALL select color-ramp, image, or custom Python function sources. Ramp colors SHALL retain stops, alpha, and interpolation. Images SHALL use independently configurable X/Y outputs. Custom color functions SHALL support the migrated legacy and vectorized argument contracts without requiring Skybrush.

#### Scenario: Ramp evaluation
- **WHEN** the user chooses a ramp source with first-color and last-color outputs
- **THEN** the corresponding endpoint colors and alpha are used

#### Scenario: Image axis evaluation
- **WHEN** a valid image source uses different X and Y output functions
- **THEN** each drone's sampled pixel is determined by both coordinates rather than UV attachment sampling

#### Scenario: Dependency-free custom function
- **WHEN** the user approves a compatible custom function file that uses only its supplied arguments and Blender-provided libraries
- **THEN** its color result is applied without importing Skybrush

### Requirement: Output functions and mapping

The feature SHALL provide first/last color, temporal, drone index, formation index, all six axis-order gradients, distance from mesh origin, custom legacy/vectorized output functions, and built-in presets. Applicable outputs SHALL support ordered and proportional mapping with source-compatible results.

#### Scenario: Ordered versus proportional gradient
- **WHEN** three drones at X coordinates 0, 1, and 4 use an X-primary gradient
- **THEN** ordered mapping produces coordinates 0, 0.5, and 1 while proportional mapping produces 0, 0.25, and 1

#### Scenario: Source distance semantics
- **WHEN** three drones lie 0, 1, and 2 units from the target mesh's world origin and distance uses proportional mapping
- **THEN** their coordinates are 0, 0.25, and 1, preserving squared-origin-distance behavior

### Requirement: Stable single-formation indexing

Only DroneAnim-tagged mesh drones SHALL participate. Drone-index ordering SHALL be deterministic and compatible with MAT ordering. Formation-index outputs SHALL use valid vertex tags, rank sparse indices, and assign equal coordinates to equal indices without a storyboard. Invalid formation indices SHALL be excluded from formation-index output; built-in presets SHALL retain their source unmapped-index fallback.

#### Scenario: Sparse and duplicate vertex indices
- **WHEN** participating drones have valid vertex indices 2, 7, 7, and 12
- **THEN** formation-index output produces coordinates 0, 0.5, 0.5, and 1

#### Scenario: Unrelated and detached objects
- **WHEN** the scene contains untagged meshes and tagged drones whose attachment constraints have been removed
- **THEN** untagged meshes remain untouched and valid tagged drones remain eligible for effects

### Requirement: Complete portable built-in preset catalog

The feature SHALL migrate every registered preset in the current Skybrush source's basic, chasing-tail, filling, generic, group, radar-scan, radial, sweep, and wave categories. Stable IDs, ordering, and deterministic mathematical results SHALL be preserved with formation mapping adapted to DroneAnim.

#### Scenario: Catalog completeness
- **WHEN** the built-in preset selector is opened
- **THEN** all 61 currently registered source presets are available in source order with their stable IDs

#### Scenario: Preset parity and persistence
- **WHEN** each preset is evaluated on a deterministic formation fixture and a selected preset is saved and reopened
- **THEN** results match the corresponding source fixture within numerical tolerance and the selected preset identity is preserved

### Requirement: Spatial and collection targeting

Users SHALL target all participating drones, containment in an evaluated mesh, or the front side of its first-face plane, optionally constrained to a collection including nested membership. Inversion SHALL complement spatial selection within the allowed collection. Missing required target references SHALL make the effect inactive with a diagnostic, not broaden its scope.

#### Scenario: Inverted group-limited targeting
- **WHEN** an effect targets a drone group and inverts inside-mesh selection
- **THEN** only group drones outside the evaluated mesh receive the effect

#### Scenario: Animated target geometry
- **WHEN** the target mesh is transformed or deformed at a new frame or subframe
- **THEN** targeting reflects its evaluated world-space geometry at that time

#### Scenario: Missing required target
- **WHEN** a mesh-dependent effect lacks a usable mesh or an imported required group cannot be resolved
- **THEN** that effect applies to no drones and the user receives a diagnostic

### Requirement: Blending and deterministic randomness

Effects SHALL compose in stack order using normal, multiply, screen, darken, lighten, overlay, soft-light, or hard-light blending, with source-compatible alpha and influence. Random offsets SHALL be reproducible for unchanged seed and drone identity, and the same per-drone offset SHALL be used on image X and Y.

#### Scenario: Repeated evaluation
- **WHEN** the same effect stack, seed, inputs, and frame are evaluated repeatedly or reached through different navigation paths
- **THEN** final colors and random offsets remain equal within numerical tolerance

#### Scenario: Disabled effect
- **WHEN** an individual effect is disabled
- **THEN** it contributes nothing while other enabled effects retain their displayed composition order

### Requirement: Non-destructive base colors and LED editing

Procedural evaluation SHALL preserve existing object-color keys, interpolation, drivers, NLA, and unanimated base colors. Material presentation SHALL write derived colors only to evaluated objects, never to original object colors. Repeated updates SHALL NOT compound effects. Existing Apply, Fade, and UV sampling operations SHALL edit their intended base colors rather than accidentally keyframing procedural display results.

#### Scenario: Same-frame edits and removal
- **WHEN** an enabled effect is edited repeatedly at the same frame and then disabled or removed
- **THEN** evaluation does not compound, original animation data is unchanged, and the correct base color is restored

#### Scenario: Edit base while effects are visible
- **WHEN** the user performs an existing LED Apply or Fade operation with material effects visible
- **THEN** the operation retains its original keyframing semantics and the effect is reevaluated over the updated base

#### Scenario: Undo and redo with effects visible
- **WHEN** the user edits a base color or effect and then undoes and redoes the edit while material effects are visible
- **THEN** original object colors and animation reflect the corresponding base edits, and the displayed effect is applied exactly once

### Requirement: Consistent visualization and export

Materials, viewport markers, and MAT export SHALL use the same final effect colors for a given scene and sample time. Export SHALL include effects independently of visualization mode, preserve the MAT schema and RGB conversion, and restore the original frame/subframe and valid presentation state after success or failure.

#### Scenario: Marker-mode export
- **WHEN** the user exports a procedural ramp effect while marker visualization is active
- **THEN** exported RGB equals the displayed final color converted to the existing zero-through-255 range, not the uneffected object RGB

#### Scenario: Failed export restoration
- **WHEN** an export fails during subframe sampling
- **THEN** the original frame and subframe are restored and subsequent lighting evaluation still works

### Requirement: Image refresh and color correctness

Image evaluation SHALL honor dimensions, scene-linear color conversion, and alpha, including portrait, landscape, boundary, and empty-image cases. Manual refresh, image/frame changes, scene load, and undo/redo SHALL prevent stale cached samples.

#### Scenario: Non-square image and edge coordinates
- **WHEN** an image effect samples a portrait image at coordinates zero and one on each axis
- **THEN** it reads the correct first/last valid pixels without transposition or out-of-bounds access

#### Scenario: Refreshed image
- **WHEN** image pixels change and the user invokes image refresh
- **THEN** the current frame's effect colors reflect the new pixels

### Requirement: Scene persistence and lifecycle isolation

Effect settings, order, ramp data, and references SHALL survive saving/reopening a scene. Enabling/disabling or reloading DroneAnim SHALL avoid duplicate handlers and stale state. Disabling SHALL restore base presentation and remove runtime hooks without deleting user-owned assets. Separate scenes and Skybrush co-installation SHALL remain isolated.

#### Scenario: Reload and teardown
- **WHEN** DroneAnim is registered, unregistered, and registered again with effects previously active
- **THEN** it operates with one set of hooks, restored base colors, and no duplicate class/handler errors

#### Scenario: Independent scenes
- **WHEN** two scenes have different effect stacks and the user switches between them
- **THEN** each scene uses its own settings, cached inputs, and drone colors

### Requirement: JSON interchange and filters

Users SHALL export all, enabled, or selected effects and import all or enabled effects using name-keyed JSON. Transferable Skybrush fields SHALL be accepted without Skybrush. Ramp settings and stable preset IDs SHALL round-trip; image/mesh/group references SHALL resolve by current-file name. Unsupported storyboard fields SHALL be ignored with a warning while absolute timing is retained.

#### Scenario: Skybrush JSON import
- **WHEN** a source-compatible file includes absolute timing, ramp data, a preset ID, and storyboard linkage
- **THEN** transferable settings are imported with unique names, absolute timing is preserved, and the discarded linkage is reported

#### Scenario: Missing image asset
- **WHEN** an imported image reference is absent from the current file
- **THEN** the user is warned and that effect remains inactive until a usable image is assigned

#### Scenario: Export filters
- **WHEN** the user exports only the selected effect or only enabled effects
- **THEN** the JSON contains exactly that selection without losing entries to duplicate names

#### Scenario: Invalid input or failed write
- **WHEN** JSON is malformed or a destination write fails
- **THEN** the user receives an error, invalid entries/resources are not committed, and an existing destination file is not replaced by incomplete output

### Requirement: Explicit trust for custom Python files

Loading scenes, importing JSON, drawing UI, and listing function names SHALL NOT execute referenced Python files. Execution SHALL require an explicit user approval for the selected file in the current session. Errors SHALL identify the affected effect and leave the evaluator usable. The UI SHALL disclose that approved Python is not sandboxed.

#### Scenario: Untrusted imported path
- **WHEN** imported JSON or a reopened scene references a Python file
- **THEN** its code is not executed and function-based evaluation remains inactive until explicitly approved

#### Scenario: Runtime function failure
- **WHEN** an approved custom function raises an exception
- **THEN** the error identifies the effect, no partial failed-effect colors are published, and later valid evaluation can proceed
