# 3D anatomical digital twin (fallback) — design

## Context

The project brief's pipeline step 2 ("digital twin — arm position mapped onto
a 3D representation on screen") was scoped to another teammate's separate
Three.js frontend, not this repo. That frontend isn't going to be delivered
in time for the Jul 25–26 2026 hackathon demo. This spec covers a fallback
digital twin, owned by the vision/backend side, so the demo has a working
visual regardless of what the frontend teammate produces.

Explicitly **not** a replacement for the pad-placement calibration overlay
work (`backend/placement/overlay.py`) — that's a separate, already-shipped
feature. This is about the *live* view of the arm during use.

## Goal

A page that shows a 3D arm, driven by the live pose stream already
broadcast over `/ws`, with the specific muscle currently being stimulated
visually highlighted the instant its TENS pad fires.

## Decisions made during brainstorming

- **Muscle "flex" is driven by actuation events, not vision.** The backend
  already knows with certainty which pad is firing (`{"type": "status",
  "pad": "BICEP", "state": "firing"}`) — that's more reliable than trying to
  detect real muscle bulge live, and requires no new CV work. Rejected
  alternative: real-time vision-driven muscle deformation (too hard, too
  risky for the timeline, and doesn't fit "mirrors current pose" cleanly
  since bulge isn't position).
- **Real z-depth from MediaPipe**, not a faked/reconstructed depth. MediaPipe
  already computes `landmark.z`; `backend/pose/estimator.py`'s `_extract()`
  currently discards it. Real z is more accurate to the actual arm than a
  fixed-bone-length IK guess, which is ambiguous (can't tell if the elbow
  bends toward or away from the camera from 2D alone) — and the backend
  change is small.
- **Arm + shoulder stub only, not a full body.** Only the arm is actually
  tracked (shoulder/elbow/wrist); a full-body asset would put untracked,
  fabricated body parts on screen. Simpler to source and rig, too.
- **Static (non-skinned) muscle meshes, rigidly parented to bone segments**,
  not smoothly deformed. The available open-source anatomy assets aren't
  rigged/skinned to a skeleton; building real skinning would be a
  significant, unnecessary time-sink for a demo fallback.

## Architecture & data flow

```
MediaPipe (unchanged)
  -> backend/pose/estimator.py  [CHANGED: _extract() keeps z, not just x,y]
  -> backend/pose/broadcaster.py  [unchanged, passes dict through]
  -> /ws "pose" message  [CHANGED: landmarks become [x,y,z], not [x,y]]
  -> frontend/twin.html  [NEW: Three.js page, connects to the same /ws]
  -> Three.js scene update, every "pose" message
```

The same `/ws` connection's existing `"status"` messages (pad fire
lifecycle) drive the muscle highlight — no new backend endpoint, no new
message types.

## Backend change

`backend/pose/estimator.py`:
- `_extract()` returns `(x, y, z)` tuples instead of `(x, y)`.
- Both `ArmPoseEstimator.process()` (live video) and `detect_pose_in_image()`
  (calibration stills) are affected, since both call `_extract()`.
- `main.py`'s documented WebSocket contract comment updates:
  `{"type": "pose", "landmarks": {"shoulder": [x,y,z], ...}}`.
- Existing tests asserting the 2-tuple shape (`tests/test_pose_side_selection.py`
  and any others touching `_extract`'s output shape) need updating: TDD this
  — write/adjust the failing test for the 3-tuple shape first, watch it
  fail, then change `_extract()`.
- **Does not affect** `backend/placement/*` — the placement pipeline only
  ever used `landmarks["wrist"]`/`["elbow"]`/`["shoulder"]` as 2D `Point(x, y)`
  for percentage-geometry math; it can keep unpacking just the first two
  elements of the (now 3-)tuple, or `Point(*landmarks["wrist"][:2])`. This
  needs a one-line adjustment wherever `Point(*landmarks[...])` is called
  in `backend/placement/pipeline.py`, verified against existing pipeline
  tests (should stay green with no behavior change).

## Frontend (`frontend/twin.html`)

Single self-contained HTML file, Three.js loaded via CDN — no build step,
no new dependency management, matches the "fallback under time pressure"
scope.

**Skeleton**: three `Object3D` nodes — shoulder (fixed at scene origin),
elbow, wrist. Positioned each frame from incoming landmarks, relative to
the shoulder landmark (since MediaPipe coordinates are normalized image
space, not real-world units):
```
elbow_pos = (elbow_landmark - shoulder_landmark) * ARM_SCALE
wrist_pos = (wrist_landmark - shoulder_landmark) * ARM_SCALE
```
`ARM_SCALE` is a single tunable constant in the file — not scientifically
calibrated, just adjusted by eye until proportions look right.

**Muscle meshes**: bicep, tricep, front-delt, rear-delt loaded from
`frontend/assets/`, each rigidly parented to its relevant bone segment
(e.g. bicep parented to the shoulder→elbow segment) — moves with the arm,
does not deform.

**Wrist pad stand-in**: no "wrist muscle" mesh exists in the anatomy set: a
simple sphere/marker at the wrist joint gets the same highlight treatment.

**Muscle highlight**: on `{"type":"status","pad":"BICEP","state":"firing"}`,
the bicep mesh's material flashes (emissive glow / color swap); reverts on
the matching `"done"`/`"error"` status. Pad name → mesh mapping mirrors
`config.MOTION_TO_PAD` / `RELAY_PAIRS` naming (`BICEP`, `TRICEP`,
`FRONT_DELT`, `REAR_DELT`, `WRIST_FLEX`/`WRIST_EXTEND`).

**Lost tracking**: if `"pose"` messages stop arriving, or landmarks come
back `null`, the arm freezes at its last known position — no snap to
origin, no glitching.

## Assets & licensing

Source: BodyParts3D (Database Center for Life Science, Japan,
lifesciencedb.jp/bp3d) — individually-separated muscle meshes, redistributed
free on Sketchfab (bicep, tricep, and deltoid all exist as standalone
downloads derived from this same dataset, so they're anatomically
consistent with each other). License: CC Attribution-ShareAlike — requires
a credit line (e.g. in the README, or an on-page credit in `twin.html`).

**Risk**: Sketchfab's free download sometimes only offers the model's
original upload format, not glTF directly. If any needed mesh isn't
available as glTF/GLB, it needs a quick Blender export pass. **Fallback if
that's not workable in time**: plain primitive shapes (capsules) standing
in for each muscle, same parenting/highlight logic, less anatomical detail.
This is not a blocker to shipping the rest of the twin.

## Testing

- **Backend**: the `_extract()` z-change is TDD'd like the rest of this
  codebase — failing test first (asserting 3-tuple / z-value presence),
  then the implementation change. Existing pose/placement test suites must
  stay green (with the one-line `Point(*landmarks[...][:2])` adjustment
  noted above).
- **Frontend**: no automated tests. `twin.html` is manually verified by
  opening it in a browser against the real running backend (live pose
  movement, and a real/mocked pad-fire triggering the matching muscle
  highlight). This is a deliberate scope decision for a demo-fallback page,
  not an oversight — this repo has no frontend test tooling and adding one
  isn't justified for a single manually-driven page under this timeline.

## Explicitly out of scope

- Skinned/deformed muscle animation (bulging in response to real vision).
- A full-body model.
- Automated/visual-regression testing of the Three.js page.
- Precise anatomical calibration of `ARM_SCALE` or muscle mesh placement —
  "looks proportionate," not measured.

## Update — 2026-07-25: forearm/grip scope + completing the deferred mesh swap

Yesterday's asset sourcing didn't finish in time, so the twin shipped with
the "approved fallback" procedural capsules for bicep/tricep/front-delt/
rear-delt (see the comment at the capsule definitions in `twin.html`), and
no forearm or hand/grip representation at all. This update covers finishing
that deferred work, and extending scope from "upper arm only" to
"shoulder through grip."

**Trigger**: wanted the twin's fidelity to get closer to a reference
photoreal anatomical illustration (Complete-Anatomy-style, full arm
musculature with vasculature). Considered and rejected texturing the
existing capsules to fake that look — real BodyParts3D-derived meshes were
already the planned source (see Assets & licensing above, and the on-page
credit already in `twin.html`), so finishing that swap gets closer to the
reference with less risk than faking it procedurally.

### Scope addition: shoulder → grip, no individual fingers

Two new anatomical regions, neither wired to any actuation pad (the
hardware has no relay output for them — only 5 regions are ever actually
stimulated: bicep, tricep, front delt, rear delt, wrist flexor/extensor
pair):

- **Forearm flexor group** and **forearm extensor group** — parented to
  the existing `forearm` segment pivot, same rigid-parenting pattern as
  `upperArm.pivot.add(bicep)` today (`forearm.pivot.add(...)`).
- **Grip / hand mass** (thenar + intrinsic palm muscles as one combined
  region, not per-finger) — parented to `wristNode`, alongside the
  existing wrist marker.

Both are **anatomical-only**: always rendered, never highlighted. No
change to `targets`/`padToTarget`/`handleStatus`/`refreshTarget` for these
two additions.

**Wrist highlight contract — superseded, see "Corrections from technical
review" below.** This section originally decided to keep the shared
sphere+halo marker as the `WRIST_FLEX`/`WRIST_EXTEND` highlight target and
leave the new forearm meshes purely visual. That decision was reversed
after review — see below.

### Completing the real-mesh swap (bicep, tricep, front delt, rear delt)

These four already have working capsule proxies with live highlight
wiring — swap only the geometry, not the parenting or highlight logic:

1. **Source**: check Sketchfab first for CC-licensed redistributions of
   BodyParts3D structures (bicep, tricep, deltoid confirmed to exist there
   already per yesterday's research) before going to BodyParts3D's own
   site directly. Same search for the two new forearm groups and the
   hand/grip mass — not yet confirmed to exist as separate downloads,
   treat as unresearched.
2. **Convert offline, once per part**: import into Blender, strip
   anything outside the target muscle (BodyParts3D structures sometimes
   bundle adjacent tissue), export `.glb`. One-time asset-prep step, not
   runtime.
3. **Load at runtime**: add `GLTFLoader` alongside the existing `THREE`
   import in `twin.html`.
4. **Align**: recenter each loaded mesh to its own origin, then
   scale/position/rotate to match where its capsule proxy currently sits
   (e.g. bicep's existing `position.z = 0.15; rotation.x = 0.07`),
   parented to the same pivot so `aimSegment`/`applyArmPose` need zero
   changes.
5. **Re-skin materials**: replace BodyParts3D's default material with the
   existing `muscleMaterial()` factory so `refreshTarget`'s activation
   glow keeps working identically on the real geometry.
6. **Per-muscle fallback, timeboxed**: ~30–40 minutes per part to
   source + clean + convert + align. Past that, that specific muscle stays
   a capsule — same fallback already proven to ship yesterday. Ship with
   however many of the 7 regions (4 existing + forearm flexor + forearm
   extensor + grip) are done; partial completion is an acceptable outcome,
   not a blocker.

### Testing

Same as the original spec: no automated tests for `twin.html` (deliberate,
unchanged scope decision). Manually verify each swapped-in mesh renders,
is correctly oriented/scaled against the live pose stream, and (for the
four actuation-wired muscles) still highlights correctly on the matching
`"status"` message.

## Corrections from technical review (2026-07-25)

A review of this update (checked against the actual code, not just the
prose) found several inaccuracies and gaps. Verified findings and their
resolutions:

- **"Real z-depth from MediaPipe" (original spec, above) is inaccurate.**
  `backend/pose/estimator.py`'s `_extract()` reads `.x/.y/.z` off
  `result.pose_landmarks[0]` — MediaPipe's *normalized/image-relative*
  landmarks, where z is documented as roughly the same scale as x, not a
  metric measurement. `result.pose_world_landmarks` (the actual
  metric-depth output) is never used anywhere in this codebase. Every
  future reference to this value in docs/code comments should call it
  **normalized/relative z**, not "real depth." No behavior change — this
  is a terminology fix only; the placement pipeline and twin both keep
  using normalized coordinates as before.
- **Grip/hand mesh must not be parented to `wristNode`.** `wristNode` only
  ever receives `.position.copy(wristPosition)` (`applyArmPose`) — its
  rotation is never touched anywhere in the file, so anything parented to
  it stays world-axis-aligned regardless of arm orientation. Fixed by
  parenting the grip mesh to `forearm.pivot` instead (same node
  `forearmFlexor`/`forearmExtensor` already use), positioned at the distal
  (wrist) end via a per-frame `position.y = forearm.length` update in
  `applyArmPose` — `forearm.pivot`'s quaternion is set every frame by
  `aimSegment`, so anything parented to it inherits correct live
  orientation. The existing wrist sphere+halo marker keeps its current
  behavior (position-only, no rotation) since it's roughly radially
  symmetric and rotation-insensitive.
- **Forearm flexor/extensor also need a per-frame position update**, not
  just a fixed radial offset. Re-reading the existing bicep/tricep pattern:
  their `position.z` (radial offset) is set once at creation, but their
  `position.y` (how far along the bone segment they sit) is recomputed
  every frame in `applyArmPose` from the live segment length
  (`bicep.position.y = upperArm.length * 0.54`). The original forearm
  flexor/extensor task only set a fixed `position.z` and never gave them a
  `position.y` at all — without a per-frame update they'd render sitting
  at the elbow joint (`y = 0`) instead of spread along the forearm. Fixed
  by adding the same per-frame `position.y` pattern for both, driven by
  `forearm.length`.
- **BodyParts3D's current license is CC BY 4.0, not CC BY-SA** — verified
  by fetching `dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html`
  directly: "BodyParts3D, © The Database Center for Life Science licensed
  under CC Attribution 4.0 International." Older BodyParts3D releases were
  distributed under CC BY-SA 2.1 Japan, so a Sketchfab re-upload's actual
  license depends on which release it was derived from — **each asset's
  license must be checked and recorded individually, not assumed.** The
  credits file (see Asset export contract, below) is renamed
  `THIRD_PARTY_ASSETS.md` and requires the license per row, not a blanket
  statement.
- **`captureTargetMaterials`/async-swap re-registration was already
  correctly designed** in the implementation plan (not this spec) — flagged
  as a risk by the review, but the plan already re-captures a target's
  materials and calls `refreshTarget()` immediately after any successful
  mesh swap. No spec change needed; noted here so the concern isn't
  re-raised.
- **Fidelity ceiling, stated explicitly:** this update gets the twin to
  *recognizably anatomical real-time geometry* — real muscle silhouettes
  instead of capsules. It explicitly does **not** produce a photoreal,
  Complete-Anatomy-style result: no vasculature, no connective tissue, no
  photoreal materials/shading. `muscleMaterial()`'s existing solid
  physical material is reused as-is for swapped-in meshes.
- **Explicitly out of scope, accepted as demo-fidelity limitations, not
  bugs to fix in this pass:**
  - **Left/right mesh mirroring.** The pose estimator locks onto whichever
    arm side clears its visibility threshold (`estimator.py`'s
    `_locked_side`), but that side is never exposed in the `/ws` "pose"
    message today — the frontend has no way to know if it's tracking a
    left or right arm. Sourced meshes are used as-is regardless of tracked
    side; anatomically-correct left/right mirroring would need a backend
    change (expose the locked side) plus geometry-mirroring logic, both
    out of scope for a capsule→real-mesh swap.
  - **Forearm/hand roll.** `aimSegment`'s
    `quaternion.setFromUnitVectors(Y_AXIS, direction)` constrains a
    segment's long axis but leaves rotation *about* that axis (roll)
    unconstrained/arbitrary — an existing characteristic of the rigging
    approach, not a new regression. It's less noticeable on the current
    round capsules and more noticeable on anatomically distinct real
    meshes, but resolving it needs additional landmark data (e.g. hand
    landmarks, not currently extracted anywhere in `estimator.py`) to
    determine true roll, which is new backend scope.

## Revised wrist/forearm highlight decision (supersedes the section above)

After review, `WRIST_FLEX`/`WRIST_EXTEND` highlight the **forearm
flexor/extensor meshes** instead of the shared wrist marker —
anatomically, those pads drive forearm flexor/extensor muscle groups, so
highlighting the actual muscle mass instead of an abstract joint marker is
more correct and was the reviewer's clearest catch.

- `padToTarget` changes from `WRIST_FLEX: "wrist", WRIST_EXTEND: "wrist"`
  to `WRIST_FLEX: "forearmFlexor", WRIST_EXTEND: "forearmExtensor"`.
- The wrist sphere+halo marker (`wristTarget`) stays in the scene as a
  plain, static location indicator, but is **removed from `targets`** —
  nothing highlights it anymore, since nothing is driving it.
- `forearmFlexor`/`forearmExtensor` move from "anatomical-only, never
  wired" to fully wired targets, same as bicep/tricep/frontDelt/rearDelt.
- Grip/hand mass remains anatomical-only — there is still no hardware pad
  for it (only 5 regions are ever actuated: bicep, tricep, front delt,
  rear delt, wrist flexor/extensor pair), this decision doesn't change
  that.

## Status-highlight timing fix (folded in from review)

**Problem, confirmed in code:** `main.py`'s `_handle_client_messages` sends
`{"type":"status","pad":pad,"state":"firing"}` *before* `await
controller.fire(pad)` resolves, then sends `"state":"done"` immediately
after it resolves (`main.py:75-78`). `ActuationController.fire()` already
accepts a `duration_ms` parameter (`config.DEFAULT_DURATION_MS`), but it's
never included in the WebSocket message, and in mock mode the round trip
can resolve near-instantly — the highlight flash could be visually
imperceptible.

**Fix:**
- Backend: include `duration_ms` in the `"firing"` status message:
  `{"type":"status","pad":pad,"state":"firing","duration_ms":duration_ms}`.
- Frontend: on receiving `"firing"`, start a minimum-hold timer for
  `duration_ms`; if `"done"`/`"error"` arrives before the timer expires,
  keep the highlight active until the timer finishes, then re-evaluate
  (matches actual pad state at that point — if a new `"firing"` for the
  same pad arrived in the meantime, stay highlighted). If `"done"`/`"error"`
  arrives after the timer already expired, revert immediately as today.
- No change to `ActuationController`/`TensClient` themselves — `duration_ms`
  already exists and is already validated; this only surfaces the existing
  value to the client.

## Asset export contract (tightened from review)

Replaces the earlier "align by eye" guidance for sourced meshes. Every
`.glb` under `frontend/assets/` must, at export time:

- Have its long axis aligned to local +Y (matching how `CapsuleGeometry`
  and the rest of this rig are authored) — `loadMuscleMesh`'s runtime
  normalization auto-recenters (bounding-box center → origin) and
  auto-scales (longest bounding-box axis → target length), but never
  auto-rotates, so an inconsistently-oriented source mesh will swap in
  visibly misrotated.
- Have a consistent anterior-facing convention (matches how the existing
  capsules are posed: bicep = anterior/`+z`, tricep = posterior/`-z`,
  mirrored for the forearm pair).
- Have all transforms applied (no leftover non-uniform scale/rotation on
  the mesh node) before export.
- Have recalculated normals (avoid inverted-normal/black-face artifacts
  after cleanup edits in Blender).
- Stay within a reasonable triangle/file-size budget for a browser demo —
  no fixed number mandated, but a multi-million-triangle anatomical scan
  export is the wrong deliverable; simplify/decimate in Blender if the
  original is that dense.

`frontend/assets/CREDITS.md` (named earlier today) is renamed
`frontend/assets/THIRD_PARTY_ASSETS.md`, one row per asset:

```markdown
# Third-party assets

| File | Source URL | Creator/uploader | License (verified per-asset) | Modifications |
|---|---|---|---|---|
| bicep.glb | <url> | <name> | <e.g. "CC BY 4.0" or "CC BY-SA 2.1 Japan" — check the specific source, don't assume> | <e.g. "stripped adjacent bone/vessel geometry, recentered, reoriented +Y"> |
```
