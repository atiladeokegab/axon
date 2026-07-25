# Digital Twin Anatomical Muscles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap `frontend/twin.html`'s capsule proxies for real BodyParts3D-derived muscle meshes where available (bicep, tricep, front delt, rear delt), add forearm flexor/extensor regions that take over the wrist pads' highlight (replacing the abstract wrist marker), add a grip/hand mass correctly parented for live rotation, and fix the status-highlight timing so a flash is never invisibly short — all with graceful per-muscle fallback to capsules when a real mesh isn't available.

**Architecture:** A single reusable async helper (`loadMuscleMesh`) attempts to `fetch`+parse a `.glb` file for a given muscle target; on success it recenters/scales the loaded mesh to match the existing capsule's footprint, swaps it in as that target's rendered geometry, and re-captures its materials into the existing highlight system; on failure (file missing/network error) it does nothing, leaving the capsule exactly as it renders today. `WRIST_FLEX`/`WRIST_EXTEND` are rewired from the old abstract wrist marker to the new forearm flexor/extensor meshes (the anatomically correct target). Grip has no hardware pad and stays visual-only, but is parented to `forearm.pivot` (not `wristNode`) so it rotates correctly with the arm. The existing `duration_ms` actuation parameter is surfaced over the WebSocket and used to enforce a minimum highlight hold time.

**Tech Stack:** Three.js r180 (CDN ES modules, no build step), `GLTFLoader` (Three.js addon, CDN), vanilla browser JS, FastAPI/pytest for the one backend change. No test framework for `twin.html` itself (established project pattern).

## Global Constraints

- Three.js is loaded via CDN ES module imports only — no build step, no npm/bundler, no new dependency management.
- `frontend/twin.html` has no automated test suite. Verification is manual: run a local static server, open the page in a browser, and visually/console-check the described behavior. The one backend change in Task 5 is TDD'd like the rest of this codebase's Python.
- Muscle meshes are static and rigidly parented to their bone segment's pivot — no skeletal skinning.
- Every sourced `.glb` under `frontend/assets/` must have its **actual, individually-verified** license recorded in `frontend/assets/THIRD_PARTY_ASSETS.md` — BodyParts3D's current license is CC BY 4.0, but older releases were CC BY-SA 2.1 Japan, so a given Sketchfab re-upload's license is not assumed, it's checked per-asset.
- Per-muscle sourcing/cleanup is timeboxed to ~30–40 minutes; past that, the muscle stays a capsule. Partial completion (some real meshes, some capsules) is an accepted, shippable outcome, not a blocker.
- Exported meshes must have their long axis on local +Y, transforms applied, and normals recalculated (see Task 0's export contract) — the runtime loader auto-recenters and auto-scales but never auto-rotates.
- Grip/hand is never added to `targets`/`padToTarget` — it must never highlight, regardless of any pad firing (no hardware output drives it).
- `WRIST_FLEX`/`WRIST_EXTEND` drive `forearmFlexor`/`forearmExtensor` respectively (not the old shared wrist marker — that decision was reversed after review, see the spec's "Revised wrist/forearm highlight decision").
- Left/right mesh mirroring and forearm/hand roll correction are explicitly out of scope (see spec's "Corrections from technical review") — not bugs to fix in this plan.
- Terminology: MediaPipe's `z` is normalized/relative, not metric depth — don't describe it as "real depth" in any new code comments or docs this plan touches.

## Asset manifest (fixed filenames, referenced by every task below)

| Target key | File | Existing capsule length (for scale reference) |
|---|---|---|
| `bicep` | `frontend/assets/bicep.glb` | `0.78` |
| `tricep` | `frontend/assets/tricep.glb` | `0.86` |
| `frontDelt` | `frontend/assets/front_delt.glb` | `0.34` |
| `rearDelt` | `frontend/assets/rear_delt.glb` | `0.36` |
| `forearmFlexor` | `frontend/assets/forearm_flexor.glb` | `0.7` (new capsule, defined in Task 3) |
| `forearmExtensor` | `frontend/assets/forearm_extensor.glb` | `0.72` (new capsule, defined in Task 3) |
| `handGrip` | `frontend/assets/hand_grip.glb` | `0.22` (new capsule, defined in Task 4) |

---

### Task 0: Source and prepare the 7 muscle assets

**This task is manual and human-only.** Downloading from Sketchfab, cleaning meshes in Blender, and judging "does this look right" are not things a coding agent can do in this environment (no browser or Blender tool access). Do not dispatch this task to a subagent. The code tasks (1–5) work correctly with zero assets present, so they don't need to wait on this.

**Files:**
- Create: `frontend/assets/bicep.glb`, `frontend/assets/tricep.glb`, `frontend/assets/front_delt.glb`, `frontend/assets/rear_delt.glb`, `frontend/assets/forearm_flexor.glb`, `frontend/assets/forearm_extensor.glb`, `frontend/assets/hand_grip.glb` (however many you get done in the timebox — missing ones just mean that muscle stays a capsule)
- Create: `frontend/assets/THIRD_PARTY_ASSETS.md`

- [ ] **Step 1: For each of the 7 muscles, search Sketchfab first** for a CC-licensed redistribution of the matching BodyParts3D structure (bicep, tricep, and deltoid are already confirmed to exist there; forearm flexor group, forearm extensor group, and hand/palm muscle mass are unresearched — check Sketchfab first, fall back to `lifesciencedb.jp/bp3d` directly if nothing turns up).

- [ ] **Step 2: Timebox each muscle to ~30–40 minutes** (source + clean + convert). If a muscle blows the timebox, abandon it and move to the next — it stays a capsule in the running app, which is an accepted outcome, not a failure.

- [ ] **Step 3: Check and record the actual license for each source you use.** BodyParts3D's current official license is CC BY 4.0 (verified at `dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html`), but older BodyParts3D releases were CC BY-SA 2.1 Japan — a Sketchfab re-upload could be derived from either. Check whatever the specific source page states (Sketchfab listing, or BodyParts3D's own site if sourced there directly) — do not assume either license applies without checking.

- [ ] **Step 4: Clean each downloaded mesh in Blender**, applying this export contract:
  - Delete any geometry that isn't the target muscle (BodyParts3D structures sometimes bundle adjacent tissue/bone/vessels).
  - Orient the mesh so its long axis is local **+Y** (matching how `CapsuleGeometry` and the rest of this rig are authored).
  - Use a consistent anterior-facing convention matching the existing capsules: bicep = anterior (`+z` in the rig), tricep = posterior (`-z`), mirrored for the forearm flexor (anterior)/extensor (posterior) pair.
  - Apply all transforms (no leftover non-uniform scale/rotation baked into the mesh node).
  - Recalculate normals.
  - Keep it within a reasonable triangle/file-size budget for a browser demo — decimate in Blender if the original anatomical scan is too dense.
  - Export as `.glb` using exactly the filename from the manifest table above, into `frontend/assets/`.

- [ ] **Step 5: For every file you produce, append a row to `frontend/assets/THIRD_PARTY_ASSETS.md`** with this format:

```markdown
# Third-party assets

| File | Source URL | Creator/uploader | License (verified per-asset) | Modifications |
|---|---|---|---|---|
| bicep.glb | <sketchfab or bp3d URL> | <name> | <e.g. "CC BY 4.0" or "CC BY-SA 2.1 Japan" — from Step 3, don't guess> | <e.g. "stripped adjacent bone/vessel geometry, recentered, reoriented +Y"> |
```

(one row per file you actually produced — don't pre-fill rows for muscles you didn't get to)

- [ ] **Step 6: Commit whatever you produced**, even if incomplete:

```bash
git add frontend/assets/
git commit -m "assets: add sourced BodyParts3D muscle meshes for digital twin"
```

---

### Task 1: Add GLTFLoader + reusable mesh-swap helper, wire it to bicep

**Files:**
- Modify: `frontend/twin.html:733` (imports), `frontend/twin.html:953-984` (targets/targetMaterials setup), `frontend/twin.html:912-915` (bicep creation)

**Interfaces:**
- Produces: `MUSCLE_ASSET_MANIFEST` (object, target-key → relative URL, all 7 keys), `captureTargetMaterials(name: string): void`, `loadMuscleMesh(targetName: string, fallbackGroup: THREE.Object3D, targetLength: number, sourceMaterial: THREE.Material): Promise<void>`. Tasks 2–4 call `loadMuscleMesh` and rely on this exact signature.

- [ ] **Step 1: Add the GLTFLoader import** next to the existing THREE import at line 733:

```js
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js";
import { GLTFLoader } from "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/GLTFLoader.js";
```

- [ ] **Step 2: Verify the GLTFLoader CDN URL actually resolves** before writing anything that depends on it: open `https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/GLTFLoader.js` directly in a browser tab. Expected: JS source loads (200), not a 404. If it 404s, check jsdelivr's file listing for `three@0.180.0/examples/jsm/loaders/` and adjust the path in Step 1 to match.

- [ ] **Step 3: Add the asset manifest**, near the top of the module script (after the existing `ARM_SCALE`/`Y_AXIS` constants around line 736-739):

```js
const MUSCLE_ASSET_MANIFEST = {
  bicep: "assets/bicep.glb",
  tricep: "assets/tricep.glb",
  frontDelt: "assets/front_delt.glb",
  rearDelt: "assets/rear_delt.glb",
  forearmFlexor: "assets/forearm_flexor.glb",
  forearmExtensor: "assets/forearm_extensor.glb",
  handGrip: "assets/hand_grip.glb",
};
```

- [ ] **Step 4: Refactor the inline material-capture loop into a named, reusable function.** Replace the existing block at lines 973-984:

```js
    Object.entries(targets).forEach(([name, group]) => {
      const materials = [];
      group.traverse((child) => {
        if (!child.isMesh) return;
        const material = child.material;
        material.userData.baseColor = material.color.clone();
        material.userData.baseEmissive = material.emissive.clone();
        material.userData.baseEmissiveIntensity = material.emissiveIntensity;
        materials.push(material);
      });
      targetMaterials.set(name, materials);
    });
```

with:

```js
    function captureTargetMaterials(name) {
      const group = targets[name];
      const materials = [];
      group.traverse((child) => {
        if (!child.isMesh) return;
        const material = child.material;
        material.userData.baseColor = material.color.clone();
        material.userData.baseEmissive = material.emissive.clone();
        material.userData.baseEmissiveIntensity = material.emissiveIntensity;
        materials.push(material);
      });
      targetMaterials.set(name, materials);
    }

    Object.keys(targets).forEach(captureTargetMaterials);
```

This is a pure refactor — same behavior, now callable again later per-target. Note: Task 3 changes what's in `targets` (removes `wrist`, adds `forearmFlexor`/`forearmExtensor`) — this refactor doesn't need to anticipate that, it just needs to keep working against whatever `targets` currently holds, which it does since it reads `targets[name]` dynamically.

- [ ] **Step 5: Add the `loadMuscleMesh` helper**, directly after the `captureTargetMaterials`/initial-capture block from Step 4:

```js
    const gltfLoader = new GLTFLoader();

    async function loadMuscleMesh(targetName, fallbackGroup, targetLength, sourceMaterial) {
      const url = MUSCLE_ASSET_MANIFEST[targetName];
      let gltf;
      try {
        gltf = await gltfLoader.loadAsync(url);
      } catch (err) {
        console.warn(`[twin] no real mesh for "${targetName}" at ${url}, keeping capsule fallback`);
        return;
      }

      const root = gltf.scene;
      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      root.position.sub(center);

      const longestAxis = Math.max(size.x, size.y, size.z) || 1;
      root.scale.setScalar(targetLength / longestAxis);

      root.traverse((child) => {
        if (!child.isMesh) return;
        child.material = sourceMaterial.clone();
        child.castShadow = true;
        child.receiveShadow = true;
      });

      fallbackGroup.clear();
      fallbackGroup.add(root);

      if (targets[targetName]) {
        captureTargetMaterials(targetName);
        refreshTarget(targetName);
      }
    }
```

Note: `refreshTarget` is a `function` declaration later in the same file (hoisted) and is only ever invoked here inside the `await`ed continuation, which runs after the whole module has finished its synchronous top-to-bottom execution — so this call is always safe regardless of where `loadMuscleMesh` sits textually. The `if (targets[targetName])` guard means calling this for `handGrip` (Task 4, never in `targets`) safely no-ops the highlight-registration part.

- [ ] **Step 6: Kick off the bicep swap.** Directly after the existing bicep creation block (`frontend/twin.html:912-915`):

```js
    const bicep = makeMuscle("bicep", 0.155, 0.78, 0xa53d34);
    bicep.position.z = 0.15;
    bicep.rotation.x = 0.07;
    upperArm.pivot.add(bicep);
    loadMuscleMesh("bicep", bicep, 0.78, muscleMaterial(0xa53d34)).catch(() => {});
```

(The `.catch(() => {})` is defensive only — `loadMuscleMesh` already catches internally and never rejects, but this makes the fire-and-forget intent explicit to a reader.)

- [ ] **Step 7: Manual verify — no asset present.** Ensure `frontend/assets/bicep.glb` does not exist yet. From the repo root:

```bash
python -m http.server 8080 --directory frontend
```

Open `http://localhost:8080/twin.html` in a browser (with the backend also running via `uv run uvicorn backend.main:app --reload` in another terminal). Expected: page loads and renders exactly as it does today (bicep capsule visible, arm tracks live pose), one `console.warn` reading `[twin] no real mesh for "bicep" ...`, no thrown errors.

- [ ] **Step 8: Manual verify — asset present (only if Task 0 has produced `bicep.glb` by now; otherwise skip and revisit after Task 0 delivers it).** Place a real `frontend/assets/bicep.glb`, reload the page. Expected: a real mesh renders in roughly the bicep's position/orientation instead of the capsule, no console errors. Trigger a `BICEP` firing status (either via the real backend actuating, or by manually sending `{"type":"status","pad":"BICEP","state":"firing"}` through the browser devtools WebSocket panel) and confirm the real mesh glows red-orange the same way the capsule used to; send `"state":"done"` and confirm it reverts.

- [ ] **Step 9: Commit.**

```bash
git add frontend/twin.html
git commit -m "feat(twin): add real-mesh loader with capsule fallback, wire bicep"
```

---

### Task 2: Wire tricep, front delt, rear delt to the same loader

**Files:**
- Modify: `frontend/twin.html:917-930` (tricep, frontDelt, rearDelt creation)

**Interfaces:**
- Consumes: `loadMuscleMesh` (Task 1)

- [ ] **Step 1: Add the swap call after each of the three existing creation blocks:**

```js
    const tricep = makeMuscle("tricep", 0.14, 0.86, 0x7f2928);
    tricep.position.z = -0.16;
    tricep.rotation.x = -0.06;
    upperArm.pivot.add(tricep);
    loadMuscleMesh("tricep", tricep, 0.86, muscleMaterial(0x7f2928)).catch(() => {});

    const frontDelt = makeMuscle("frontDelt", 0.18, 0.34, 0xb04435);
    frontDelt.position.set(-0.08, 0.24, 0.2);
    frontDelt.rotation.z = -0.34;
    upperArm.pivot.add(frontDelt);
    loadMuscleMesh("frontDelt", frontDelt, 0.34, muscleMaterial(0xb04435)).catch(() => {});

    const rearDelt = makeMuscle("rearDelt", 0.17, 0.36, 0x762527);
    rearDelt.position.set(0.07, 0.23, -0.2);
    rearDelt.rotation.z = 0.34;
    upperArm.pivot.add(rearDelt);
    loadMuscleMesh("rearDelt", rearDelt, 0.36, muscleMaterial(0x762527)).catch(() => {});
```

- [ ] **Step 2: Manual verify — no assets present.** Same server/page as Task 1 Step 7. Expected: page renders exactly as before (three capsules), three `console.warn` lines (tricep, frontDelt, rearDelt), no thrown errors.

- [ ] **Step 3: Manual verify — any assets present (repeat per file as Task 0 delivers them).** Same pattern as Task 1 Step 8, once per available `.glb`: real mesh renders in place of the capsule, highlight still works for that pad (`TRICEP`, `FRONT_DELT`, `REAR_DELT`).

- [ ] **Step 4: Commit.**

```bash
git add frontend/twin.html
git commit -m "feat(twin): wire tricep, front delt, rear delt to real-mesh loader"
```

---

### Task 3: Add forearm flexor + extensor, rewire WRIST_FLEX/WRIST_EXTEND to them

**Files:**
- Modify: `frontend/twin.html` (insert after the rear delt block from Task 2, before the `wristTarget` setup at line 932; modify `targets`/`padToTarget` at lines 953-968; modify `applyArmPose` at lines 1040-1055)

**Interfaces:**
- Consumes: `makeMuscle` (existing), `forearm.pivot` (existing, from `makeSegment(elbowNode, "forearmSegment")`), `loadMuscleMesh` (Task 1)
- Produces: `forearmFlexor`, `forearmExtensor` (module-scope `THREE.Object3D` references, registered in `targets`) — Task 4 must not add `handGrip` to `targets`, following this task's pattern of what *is* registered vs. not.

- [ ] **Step 1: Add the two capsule fallbacks, parented to the forearm segment** (mirrored front/back, matching the bicep/tricep pattern on the upper arm — position/rotation are starting guesses, adjust by eye once visible):

```js
    const forearmFlexor = makeMuscle("forearmFlexor", 0.1, 0.7, 0x9c3a30);
    forearmFlexor.position.z = 0.1;
    forearmFlexor.rotation.x = 0.05;
    forearm.pivot.add(forearmFlexor);
    loadMuscleMesh("forearmFlexor", forearmFlexor, 0.7, muscleMaterial(0x9c3a30)).catch(() => {});

    const forearmExtensor = makeMuscle("forearmExtensor", 0.095, 0.72, 0x6f2624);
    forearmExtensor.position.z = -0.11;
    forearmExtensor.rotation.x = -0.05;
    forearm.pivot.add(forearmExtensor);
    loadMuscleMesh("forearmExtensor", forearmExtensor, 0.72, muscleMaterial(0x6f2624)).catch(() => {});
```

- [ ] **Step 2: Rewire the highlight maps.** Replace the existing `targets`/`padToTarget` block (lines 953-968):

```js
    const targets = {
      bicep,
      tricep,
      frontDelt,
      rearDelt,
      wrist: wristTarget,
    };

    const padToTarget = {
      BICEP: "bicep",
      TRICEP: "tricep",
      FRONT_DELT: "frontDelt",
      REAR_DELT: "rearDelt",
      WRIST_FLEX: "wrist",
      WRIST_EXTEND: "wrist",
    };
```

with:

```js
    const targets = {
      bicep,
      tricep,
      frontDelt,
      rearDelt,
      forearmFlexor,
      forearmExtensor,
    };

    const padToTarget = {
      BICEP: "bicep",
      TRICEP: "tricep",
      FRONT_DELT: "frontDelt",
      REAR_DELT: "rearDelt",
      WRIST_FLEX: "forearmFlexor",
      WRIST_EXTEND: "forearmExtensor",
    };
```

`wristTarget` (the sphere+halo) keeps being created and added to the scene exactly as before (`wristNode.add(wristTarget)` — don't touch that line) — it's just no longer in `targets`, so it stays a plain static marker at whatever base color/opacity it was built with, and `refreshTarget`/`captureTargetMaterials` never touch it again.

- [ ] **Step 3: Give both new regions a per-frame position along the forearm.** The existing bicep/tricep/delt muscles get their `position.y` (how far along the bone segment they sit) recomputed every frame in `applyArmPose` from the live segment length — a fixed `position.z` alone (Step 1) is only the radial offset, not the position along the bone, so without this the meshes would render sitting at the elbow joint. Add two lines to `applyArmPose` (lines 1040-1055), right after the existing `rearDelt.position.y` line:

```js
    function applyArmPose(elbowPosition, wristPosition) {
      const upperVector = elbowPosition.clone();
      const forearmVector = wristPosition.clone().sub(elbowPosition);
      if (upperVector.length() < 0.05 || forearmVector.length() < 0.05) return false;

      elbowNode.position.copy(elbowPosition);
      wristNode.position.copy(wristPosition);
      aimSegment(upperArm, upperVector);
      aimSegment(forearm, forearmVector);

      bicep.position.y = upperArm.length * 0.54;
      tricep.position.y = upperArm.length * 0.56;
      frontDelt.position.y = Math.max(0.2, upperArm.length * 0.13);
      rearDelt.position.y = Math.max(0.2, upperArm.length * 0.13);
      forearmFlexor.position.y = forearm.length * 0.5;
      forearmExtensor.position.y = forearm.length * 0.5;
      return true;
    }
```

- [ ] **Step 4: Manual verify.** Same server/page setup as prior tasks. Expected: two additional capsule bulges (or real meshes if `forearm_flexor.glb`/`forearm_extensor.glb` exist from Task 0) now visible, spread along the forearm segment (not bunched at the elbow) — adjust the `position.z`/`rotation.x` values from Step 1 by eye if they clip through the forearm bone or sit obviously wrong. Send a `WRIST_FLEX` firing status through devtools and confirm `forearmFlexor` glows (not the wrist sphere); send `WRIST_EXTEND` and confirm `forearmExtensor` glows. Confirm the wrist sphere+halo marker never glows regardless of what fires.

- [ ] **Step 5: Commit.**

```bash
git add frontend/twin.html
git commit -m "feat(twin): add forearm flexor/extensor, rewire wrist pads to them"
```

---

### Task 3b: Fix stale legend entries for the rewired wrist pads

**Found during Task 3's review** (not anticipated in the original plan): the sidebar legend (`frontend/twin.html:679-693`) has one `<div class="legend-item" data-target="...">` row per highlight target, driven by `refreshTarget`'s `document.querySelectorAll('[data-target="${targetName}"]')` (`frontend/twin.html:1073`). Task 3 removed `"wrist"` from `targets`/`padToTarget` and added `forearmFlexor`/`forearmExtensor` — but nobody updated the legend HTML, which the plan never mentioned. Result: the "Wrist marker" legend row is now permanently inert (nothing ever sets `targetName` to `"wrist"` anymore), and there are no legend rows at all for `forearmFlexor`/`forearmExtensor`, so firing `WRIST_FLEX`/`WRIST_EXTEND` glows the correct 3D mesh but gives zero legend feedback.

**Files:**
- Modify: `frontend/twin.html:691-693` (the stale wrist legend row)

- [ ] **Step 1: Replace the stale legend row.** Replace:

```html
        <div class="legend-item" data-target="wrist">
          <span class="legend-swatch"></span><span>Wrist marker</span><span class="legend-code">WRS</span>
        </div>
```

with:

```html
        <div class="legend-item" data-target="forearmFlexor">
          <span class="legend-swatch"></span><span>Forearm flexor</span><span class="legend-code">FFL</span>
        </div>
        <div class="legend-item" data-target="forearmExtensor">
          <span class="legend-swatch"></span><span>Forearm extensor</span><span class="legend-code">FEX</span>
        </div>
```

- [ ] **Step 2: Verify no other legend row references `"wrist"`.** Grep the file for `data-target="wrist"` — expect zero matches after this edit. Grep for `data-target="forearmFlexor"` and `data-target="forearmExtensor"` — expect exactly one match each.

- [ ] **Step 3: Commit.**

```bash
git add frontend/twin.html
git commit -m "fix(twin): replace stale wrist legend row with forearm flexor/extensor"
```

---

### Task 4: Add grip/hand mass, correctly rotated with the forearm

**Files:**
- Modify: `frontend/twin.html` (insert after the `forearmExtensor` block from Task 3, before the `wristTarget` setup at line 932; modify `applyArmPose` again)

**Interfaces:**
- Consumes: `makeMuscle` (existing), `forearm.pivot` (existing — **not** `wristNode**, see below), `loadMuscleMesh` (Task 1)
- Produces: `handGrip` (module-scope `THREE.Object3D` reference) — must not be added to `targets`.

- [ ] **Step 1: Add the capsule fallback, parented to `forearm.pivot` (not `wristNode`).** `wristNode` only ever receives a position update (`applyArmPose`'s `wristNode.position.copy(wristPosition)`) — it never gets a rotation, so anything parented to it stays world-axis-aligned as the arm moves. `forearm.pivot` gets a live quaternion every frame via `aimSegment`, so parenting there gives correct rotation for free:

```js
    const handGrip = makeMuscle("handGrip", 0.12, 0.22, 0x8a3128);
    forearm.pivot.add(handGrip);
    loadMuscleMesh("handGrip", handGrip, 0.22, muscleMaterial(0x8a3128)).catch(() => {});
```

(No fixed `position.z`/`rotation.x` here — it's centered on the forearm's own axis, distinguishing it from the flexor/extensor's radial offsets.)

- [ ] **Step 2: Position it at the distal (wrist) end of the forearm, per-frame** (the same reasoning as Task 3 Step 3 — it needs to track live `forearm.length`, not a value fixed at creation time). Add one more line to `applyArmPose`, after the `forearmExtensor.position.y` line from Task 3:

```js
      forearmFlexor.position.y = forearm.length * 0.5;
      forearmExtensor.position.y = forearm.length * 0.5;
      handGrip.position.y = forearm.length;
      return true;
    }
```

- [ ] **Step 3: Do not add `handGrip` to `targets` or `padToTarget`** — there is still no hardware pad for grip (only 5 regions are ever actuated). Confirm after editing that `targets` (Task 3 Step 2) still lists exactly `bicep, tricep, frontDelt, rearDelt, forearmFlexor, forearmExtensor` — `handGrip` appears in neither `targets` nor `padToTarget`.

- [ ] **Step 4: Manual verify.** Same setup as prior tasks. Expected: a muscle mass now visible past the end of the forearm segment, roughly where a hand would be, distinct from the wrist sphere+halo marker (both are visible near the wrist, that's fine — they represent different things). **Specifically test rotation**: move/rotate your arm significantly in front of the camera (not just translate it) and confirm `handGrip` turns to track the forearm's orientation rather than staying fixed in world space while only sliding to follow the wrist point. Confirm it never glows regardless of which pad fires (send a few different `status` messages through devtools — `BICEP`, `WRIST_FLEX`, etc. — and confirm `handGrip`'s material never changes).

- [ ] **Step 5: Commit.**

```bash
git add frontend/twin.html
git commit -m "feat(twin): add grip/hand mass, parented to forearm for live rotation"
```

---

### Task 5: Status-highlight minimum hold time

**Problem:** `main.py` sends `"firing"` before `controller.fire()` resolves and `"done"` immediately after — in mock mode this round trip can be near-instant, so a highlight flash could be visually imperceptible. `ActuationController.fire()` already accepts `duration_ms` (`config.DEFAULT_DURATION_MS = 800`), but it's never sent to the client.

**Files:**
- Modify: `backend/main.py:6-16` (WebSocket contract docstring), `backend/main.py:75` (the firing status message)
- Test: `tests/test_main_endpoints.py:23-32` (existing test needs updating)
- Modify: `frontend/twin.html` (the `handleStatus` function, lines ~1014-1028)

**Interfaces:**
- Consumes: `config.DEFAULT_DURATION_MS` (existing, `backend/config.py:68`, value `800`)

**Backend (TDD — this part of the codebase has test coverage, follow it):**

- [ ] **Step 1: Update the existing test to assert the new field first.** In `tests/test_main_endpoints.py`, change `test_websocket_select_motion_fires_mock_pad_and_reports_done` (lines 23-32):

```python
def test_websocket_select_motion_fires_mock_pad_and_reports_done():
    with TestClient(main.app).websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "select_motion", "motion": "grip"})

        firing = ws.receive_json()
        done = ws.receive_json()

    assert firing == {
        "type": "status", "pad": "WRIST_FLEX", "state": "firing",
        "duration_ms": 800,
    }
    assert done == {"type": "status", "pad": "WRIST_FLEX", "state": "done"}
```

- [ ] **Step 2: Run it, confirm it fails.**

```bash
uv run pytest tests/test_main_endpoints.py::test_websocket_select_motion_fires_mock_pad_and_reports_done -v
```

Expected: FAIL — actual `firing` dict is missing the `duration_ms` key.

- [ ] **Step 3: Implement the change.** In `backend/main.py`, replace line 75:

```python
        await websocket.send_text(json.dumps({"type": "status", "pad": pad, "state": "firing"}))
```

with:

```python
        await websocket.send_text(json.dumps({
            "type": "status", "pad": pad, "state": "firing",
            "duration_ms": config.DEFAULT_DURATION_MS,
        }))
```

Also update the documented WebSocket contract in the module docstring (lines 6-14) so it matches:

```python
"""
FastAPI app: streams live pose landmarks to the frontend over WebSocket,
and fires TENS pads (through ActuationController — never TensClient
directly) when the frontend reports a selected motion.

WebSocket contract (my proposed default — confirm with whoever's doing
frontend, then delete this comment):
    Server -> client:
        {"type": "ready", "armed": bool}
            sent once on connect. armed=false means every fire is mocked
            (logged, not sent to real hardware) regardless of what the
            client asks for — the UI should show this state unmistakably.
        {"type": "pose", "landmarks": {"shoulder": [x,y,z], "elbow": [x,y,z], "wrist": [x,y,z]}}
        {"type": "status", "pad": "BICEP", "state": "firing", "duration_ms": 800}
        {"type": "status", "pad": "BICEP", "state": "done" | "error", "detail": "..."}
    Client -> server:
        {"type": "select_motion", "motion": "grip" | "raise" | "lower" | "push_forward" | "pull_back"}
"""
```

- [ ] **Step 4: Run it, confirm it passes.**

```bash
uv run pytest tests/test_main_endpoints.py -v
```

Expected: PASS, all tests in the file (this file has other tests unrelated to this change — confirm none of them regressed).

- [ ] **Step 5: Commit.**

```bash
git add backend/main.py tests/test_main_endpoints.py
git commit -m "feat(backend): surface duration_ms in WS firing status message"
```

**Frontend (manual verification, matching this file's established pattern):**

- [ ] **Step 6: Replace `handleStatus` and add the hold-timer state.** Current code (lines ~1014-1028, right after `refreshStimulationReadout`):

```js
    function handleStatus(message) {
      const targetName = padToTarget[message.pad];
      if (!targetName) return;

      if (message.state === "firing") {
        activePads.add(message.pad);
      } else if (message.state === "done" || message.state === "error") {
        activePads.delete(message.pad);
      } else {
        return;
      }

      refreshTarget(targetName);
      refreshStimulationReadout();
    }
```

Replace with:

```js
    const holdUntil = new Map();
    const releaseTimers = new Map();

    function releasePad(pad, targetName) {
      releaseTimers.delete(pad);
      holdUntil.delete(pad);
      activePads.delete(pad);
      refreshTarget(targetName);
      refreshStimulationReadout();
    }

    function handleStatus(message) {
      const targetName = padToTarget[message.pad];
      if (!targetName) return;

      const existingTimer = releaseTimers.get(message.pad);
      if (existingTimer) {
        clearTimeout(existingTimer);
        releaseTimers.delete(message.pad);
      }

      if (message.state === "firing") {
        activePads.add(message.pad);
        const holdMs = Number.isFinite(message.duration_ms) ? message.duration_ms : 0;
        holdUntil.set(message.pad, Date.now() + holdMs);
        refreshTarget(targetName);
        refreshStimulationReadout();
      } else if (message.state === "done" || message.state === "error") {
        const remaining = (holdUntil.get(message.pad) ?? 0) - Date.now();
        if (remaining > 0) {
          const timeoutId = setTimeout(() => releasePad(message.pad, targetName), remaining);
          releaseTimers.set(message.pad, timeoutId);
        } else {
          releasePad(message.pad, targetName);
        }
      }
    }
```

This handles both cases: `"done"`/`"error"` arriving before the hold expires (schedules the actual release for the remaining time), and arriving after (releases immediately, same as today). A new `"firing"` for the same pad arriving while a release is still pending cancels that pending release (via the `clearTimeout` at the top) and re-adds/re-holds — so rapid re-fires don't flicker off.

- [ ] **Step 7: Manual verify.** Same server/page setup as prior tasks. Using the browser devtools WebSocket panel (or the real backend, which now sends `duration_ms: 800` on every real fire):
  - Send `{"type":"status","pad":"BICEP","state":"firing","duration_ms":2000}` immediately followed by `{"type":"status","pad":"BICEP","state":"done"}`. Expected: bicep glows and stays glowing for roughly 2 seconds total before reverting, not instantly.
  - Send `{"type":"status","pad":"BICEP","state":"firing"}` (no `duration_ms` field at all) followed immediately by `{"type":"status","pad":"BICEP","state":"done"}`. Expected: reverts immediately — confirms the `Number.isFinite` fallback to `holdMs = 0` preserves today's behavior when no duration is present.
  - Trigger a real motion through the running backend (not just devtools) and confirm the flash is now clearly visible instead of instant.

- [ ] **Step 8: Commit.**

```bash
git add frontend/twin.html
git commit -m "feat(twin): enforce minimum highlight hold time from duration_ms"
```

---

### Task 6: Document local static-serving requirement

Real-mesh loading uses `fetch` under the hood (via `GLTFLoader`), which browsers block for `file://` pages. Prior to this plan, `twin.html` had no network calls of its own beyond the WebSocket, so this wasn't a problem; opening it directly as a local file may have worked. It doesn't reliably work anymore.

**Files:**
- Modify: `README.md` (the "Running" section)

- [ ] **Step 1: Add a line to the Running section** noting the twin now needs to be served over HTTP, not opened as a local file:

```markdown
`frontend/twin.html` loads real muscle meshes over `fetch`, which browsers
block on `file://` pages — serve it locally instead of double-clicking it:
```
python -m http.server 8080 --directory frontend
```
then open `http://localhost:8080/twin.html` (with the backend running
separately per above).
```

- [ ] **Step 2: Commit.**

```bash
git add README.md
git commit -m "docs: note twin.html needs local HTTP serving for real mesh loading"
```
