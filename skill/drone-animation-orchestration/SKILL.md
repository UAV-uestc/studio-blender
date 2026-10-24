---
name: drone-animation-orchestration
description: Refine the existing models mesh, and create one formation and its animated presentation in Drone Anim through Blender MCP using live safety limits. Use for single-formation animation. Not for Skybrush show orchestration.
---

# Single Formation Animation

Scope this workflow to one formation and one self-contained animation
presentation in the Drone Anim add-on. Keep the existing skill identifier and
directory for compatibility; the name does not expand this scope.
Do not build a complete drone show, a sequence of distinct formations, Skybrush
storyboard entries, inter-formation transitions, takeoff, or landing phases.
If the request requires those features, explain that they need a separate
workflow instead of silently adding them.

Use Blender MCP for live scene inspection, animation creation, and verification.
Acquire safety settings before planning or changing animation. Never reuse limits
from a previous conversation as current configuration.

You must implement the animiation on the original 3D model, not to a copy.

## 1. Acquire Live Safety Configuration

Discover the Blender MCP Python execution tool available in this session. Tool
names may vary; use the tool that executes Python inside the user's live Blender.
Pass the user's current request verbatim in `user_prompt` when supported.
Do not substitute local Python execution for reading the running Blender scene.

Execute this read-only acquisition code through Blender MCP:

```python
import bpy
import json
import math
from datetime import datetime, timezone

scene = bpy.context.scene
props = getattr(scene, "drones_anim_safety", None)
if props is None:
    raise RuntimeError("Drone Anim safety settings are unavailable; stop planning")

fields = {
    "minimum_distance": "proximity_warning_threshold",
    "minimum_navigation_altitude": "min_navigation_altitude",
    "maximum_altitude": "altitude_warning_threshold",
    "horizontal_speed_limit": "velocity_xy_warning_threshold",
    "vertical_downward_speed_limit": "velocity_z_warning_threshold",
    "acceleration_limit": "acceleration_warning_threshold",
    "yaw_rate_limit": "yaw_rate_warning_threshold",
}
missing = [attr for attr in fields.values() if not hasattr(props, attr)]
if missing:
    raise RuntimeError("Missing safety properties: " + ", ".join(missing))
limits = {key: float(getattr(props, attr)) for key, attr in fields.items()}
separate_up = getattr(props, "velocity_z_warning_different_up", None)
raw_up = getattr(props, "velocity_z_warning_threshold_up", None)
if separate_up is False:
    limits["vertical_upward_speed_limit"] = limits["vertical_downward_speed_limit"]
    up_source = "shared downward limit; separate ascent is disabled"
elif separate_up is True and raw_up is not None:
    limits["vertical_upward_speed_limit"] = float(raw_up)
    up_source = "separate ascent limit"
else:
    limits["vertical_upward_speed_limit"] = None
    up_source = "unavailable or ambiguous; resolve before any ascent"

for key, value in limits.items():
    if value is not None and (not math.isfinite(value) or value < 0):
        raise RuntimeError("Invalid limit: " + key)
if limits["maximum_altitude"] <= limits["minimum_navigation_altitude"]:
    raise RuntimeError("Maximum altitude must exceed minimum navigation altitude")
fps = scene.render.fps / scene.render.fps_base
if not math.isfinite(fps) or fps <= 0:
    raise RuntimeError("Invalid effective FPS")

raw = {}
for prop in props.bl_rna.properties:
    if prop.identifier not in ("rna_type", "name"):
        value = getattr(props, prop.identifier)
        if isinstance(value, (bool, int, float, str)):
            raw[prop.identifier] = value

print(json.dumps({
    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    "scene": scene.name,
    "blend_file": bpy.data.filepath,
    "blender_version": bpy.app.version_string,
    "frame_range": [scene.frame_start, scene.frame_end],
    "effective_fps": fps,
    "unit_system": scene.unit_settings.system,
    "unit_scale_length": scene.unit_settings.scale_length,
    "limits": limits,
    "upward_limit_source": up_source,
    "raw_safety_properties": raw,
}, indent=2))
```

Report all eight requested limits and the upward-limit source before proceeding.
Keep the returned snapshot available for planning and final comparison.
Stop if MCP is disconnected or required configuration is missing or invalid.
Do not fall back to Skybrush settings, factory defaults, or remembered values.

Use these semantics, verified against this project's `dronesAnim/safety.py`
and `dronesAnim/safety_math.py`:

| Constraint | Meaning |
|---|---|
| Minimum distance | Euclidean distance between drone centers |
| Minimum navigation altitude | Minimum world Z while drones move horizontally within the presentation |
| Maximum altitude | Upper world Z boundary; the add-on warns at equality, so remain strictly below |
| Horizontal speed | Magnitude of world XY velocity, not separate X and Y limits |
| Downward speed | Nonnegative magnitude of negative world Z velocity |
| Upward speed | Separate threshold when enabled, otherwise shared downward threshold |
| Acceleration | Magnitude of the full 3D acceleration vector |
| Yaw rate | Absolute world yaw change in degrees per second |

The downward property has a misleading UI label in some versions. Use its RNA
identifier and verified behavior, not a translation of the label.
If the installed version differs, inspect its live RNA and narrowly scoped
implementation through MCP before changing these mappings.

Read the master enable flag, per-category flags, proximity scope, FPS, and units.
Treat numeric limits as hard planning constraints even if their warning toggles
are off. Do not enable, disable, or relax settings to make a plan pass.
The add-on evaluates raw world coordinates. With `scale_length != 1`, verify
the installed unit convention and report raw and physical units; never silently
apply a conversion that disagrees with the add-on's checker.
An unavailable ascent limit blocks ascent until clarified. A zero speed,
acceleration, or yaw limit prohibits that corresponding motion, not an unlimited
motion allowance.

## 2. Define One Formation and Its Presentation

Read the intended formation source mesh, its vertices, modifiers, shape keys,
armature, object animation, and existing drone bindings through MCP.
Read drone collections, identities, evaluated world transforms, parents,
constraints, and drivers. 

At the beginning, refine the existing models mesh to ensure their vertices' clearance 
is at least the minimum distance, and can insert some vertices at the faces with sparse
vertices, remove vertices at the faces with dense vertices, to keep 
the vertices distribution in mesh as even as possible, so a promising formation can be created.
Last but not least, be mind of the vertices around the joints of the mesh, and more 
clearance is needed around them to avoid collision as joints rotating during animation.

The current add-on checker inspects mesh objects directly in the `Drones`
collection. Explicitly compare that set with the intended drone set; do not claim
coverage for nested collections or objects the checker ignores.

Resolve the single formation's shape, drone count, spatial placement, scale,
presentation frame interval, and requested motion from the user's request and
scene. Rotation, translation, deformation, or a repeating motion of this one
formation are in scope; assembling a sequence of separate formations is not.
Use one source mesh with one stable vertex-to-drone identity per point where
practical. Do not introduce a topology-changing modifier or reorder vertices
after binding. Ask only for substantial missing decisions. If the user only
requests inspection or a skill, do not create a formation or animation.
If only a static formation is requested, validate its placement and separation
without inventing motion; temporal checks are not applicable to that result.

Preserve existing unrelated scene data and animation. Before edits, provide a
brief change summary. Stage generated data separately or preserve the precise
affected actions, keyframes, constraints, and timeline values for rollback.
Do not rely on undo across MCP calls. Do not overwrite or save a blend file
unless requested; use a separate backup only with an appropriate agreed path.

Inspect the installed Drone Anim creation workflow before using it. The current
`drones_anim.attach_drones_to_vertices` operator creates drones for every
unbound vertex of every eligible mesh in the scene, excluding existing drones;
selection alone does not limit its scope. Preview the complete source list and
expected new drone count. Invoke it only when that list contains solely the
intended formation source. Otherwise use a verified, narrowly scoped binding
procedure through MCP or pause to agree on scene isolation; do not attach
drones to unrelated meshes or unlink user objects silently.
Preserve existing bindings and prevent duplicate drones. Read
`scene.drones_anim_panel.drone_radius` rather than assuming a visible drone size.
Do not invent a formation operator or use Skybrush operators or planning APIs.

## 3. Plan the Formation Presentation Within Every Limit

Validate the source formation, its intended placement, and the entire proposed
motion envelope. Reject infeasible geometry rather than changing the requested
shape, scale, position, or drone count silently.
Use explicit positive clearance and speed margins; state the chosen margins.
Enforce minimum separation for all formation drones throughout the presentation,
even when the UI proximity scope excludes low-altitude drones.
Place the presentation above minimum navigation altitude with clearance whenever
horizontal motion occurs, and keep it strictly below maximum altitude.
Do not add a ground arrangement or paths to or from the presentation.

Keep drone identities fixed as the formation animates. Check intermediate
deformations as well as initial and final poses. Increasing duration reduces
speed and acceleration but cannot fix geometry that violates minimum distance.
Adjust the proposed deformation or request agreement on geometry changes instead.
For rigid rotation, account for each drone's radius from the rotation axis:
outer drones can exceed speed and acceleration limits even if the center is still.

Prefer a smooth trajectory with zero endpoint velocity and acceleration.
For a straight rest-to-rest segment use quintic progress
`s(u) = 10*u**3 - 15*u**4 + 6*u**5`, with `u = t/T`.
For displacement `(dx, dy, dz)` and signed unwrapped yaw displacement `theta`
in degrees, choose duration conservatively from:

```text
T >= 1.875 * hypot(dx, dy) / horizontal_speed_limit
T >= 1.875 * abs(dz) / directional_vertical_speed_limit
T >= sqrt((10 / sqrt(3)) * sqrt(dx*dx + dy*dy + dz*dz) / acceleration_limit)
T >= 1.875 * abs(theta) / yaw_rate_limit
```

Apply the chosen margins, round duration upward to whole frames, and verify the
resulting path. Skip a bound only when its displacement is zero. Never divide by
a zero limit. These bounds apply to that quintic segment, not arbitrary curves,
constraint-driven motion, or piecewise-linear approximations.
For curved paths include tangential and curvature-induced acceleration.
Unwrap yaw deliberately; avoid representation jumps and unintended full turns.

## 4. Create the Single Formation Presentation Through Blender MCP

Re-read safety settings immediately before writing; replan if limits changed.
Create or reuse the intended source mesh, establish stable vertex bindings, and
animate only this formation's presentation. Prefer source mesh transforms,
shape keys, or topology-preserving deformation that its bound drones follow.
Print the source object, generated/reused drone names, count, and frame interval.
Use Blender 4.x/5.x-compatible public APIs and feature detection.
Use `context.temp_override(...)` only when an operator actually needs context.
Verify evaluated vertex count and identity before attachment and throughout
deformation. Clear temporary evaluated meshes with `to_mesh_clear()` in `finally`.
For constrained drones, animate the source driving their positions rather than
inserting drone location keys that the attachment constraints override.
Position attachment does not imply orientation attachment; animate or constrain
drone yaw explicitly only when requested, and validate that evaluated yaw.
Do not create storyboard entries, snap frames to show-wide boundaries, change
FPS, or extend the presentation into a takeoff-to-landing timeline.

Do not assume inserting endpoint keyframes reproduces a planned quintic.
Choose a representation that reproduces the trajectory, then validate its
evaluated result. If baking per-frame keys, set interpolation deliberately and
account for acceleration discontinuities between linear segments. Avoid
uncontrolled Bezier handle overshoot. Use supported layered-action APIs in
Blender 5.x instead of assuming legacy `action.fcurves` access is available.
For nested properties, resolve the target sub-object before keyframe insertion.

## 5. Validate Only This Presentation

Read positions and yaw from `obj.evaluated_get(depsgraph).matrix_world` after
`scene.frame_set(frame, subframe=...)`. Validate the full presentation interval,
including its own holds or loop seam, not merely keyframes.
Do not scan unrelated show segments or claim validation of a complete show.
Restore the original current frame and subframe in a `finally` block.
Use effective FPS and sample time, never an assumed integer FPS.

Run an independent checker with every requested constraint enforced, regardless
of warning flags. For uniform sample spacing `dt`:

```text
velocity = (position_now - position_previous) / dt
acceleration = (velocity_now - velocity_previous) / dt
horizontal_speed = hypot(velocity.x, velocity.y)
upward_speed = max(velocity.z, 0)
downward_speed = max(-velocity.z, 0)
yaw_rate = abs(unwrapped_yaw_now - unwrapped_yaw_previous) / dt
```

Convert yaw radians to degrees. Sample at least every frame and at subframes
where curves, constraints, fast rotation, or close approaches require it.
Check presentation boundary derivatives from the intended motion or nearby
samples where available. For loops, also check continuity of position, velocity,
acceleration, and yaw across the seam. Do not fabricate arrival or departure
motion to provide those samples. Reject missing/nonfinite transforms or a
changing formation drone set instead of treating missing measurements as zeros.
Include other scene drones in proximity checks if they occupy the presentation
space, without modifying or extending their animations.

Check all drone pairs or use a spatial index that preserves complete coverage.
For linear motion between samples, also check the exact closest approach by
minimizing the relative-motion segment. For nonlinear paths, use analytic
bounds or adaptive subdivision with justified error bounds when claiming
continuous separation. Sampling alone is not proof of continuous compliance.
Use analytic derivatives or a smooth motion model for continuous acceleration;
finite differences can hide peaks and interpolation discontinuities.
Verify altitude throughout each segment and horizontal navigation clearance.

If `bpy.ops.drones_anim.validate_trajectories` exists and its `poll()` succeeds,
run it as a supplementary UI check through MCP only when its scene frame range
matches this presentation, and read the new
`scene.drones_anim_safety.validation_summary`. It scans the scene frame range,
honors warning flags/scope, and returns `FINISHED` even when warnings are found.
An operator success code or empty/stale summary is not a safety pass.
If the ranges differ, use the independent presentation-scoped checker and
report the UI check as skipped; do not alter the user's timeline just to run it.
The UI operator also checks other drones in `Drones`; distinguish those results
from the formation-scoped report. Do not replace the independent checker with it.

On failure, print the offending drone or pair, frame/time, measurement, and
limit. Repair presentation timing, deformation, or yaw as appropriate, then
revalidate the full presentation interval. Keep thresholds unchanged. If no compliant solution is
feasible, restore staged changes or leave them clearly marked unvalidated and
report the blocker; do not present a failed candidate as completed animation.

## 6. Report Completion

Re-read the limits and verify they are unchanged; if they changed concurrently,
validate against the new configuration before claiming completion.
Report the formation source, drone count, presentation frame/time interval,
animation behavior, captured limits, margins, measured
minimum distance, navigation/maximum altitude, peak horizontal and directional
vertical speeds, peak 3D acceleration, and peak yaw rate.
Report independent validation and add-on validation separately. State any
unavailable check, approved exception, or sampling limitation explicitly.
Distinguish sampled compliance from analytically bounded continuous compliance.
State that the result covers only this single formation presentation, not a
complete show or its arrival/departure phases.
Never describe Blender validation as certification for real drone flight.
