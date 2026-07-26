# Third-party mesh assets

All files in `frontend/assets/bp3d/` are unmodified Wavefront `.obj` exports from
**BodyParts3D, © The Database Center for Life Science**, licensed under
[Creative Commons Attribution-Share Alike 2.1 Japan](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html).
Source: `isa_BP3D_4.0_obj_99.zip`, https://dbarchive.biosciencedbc.jp/data/bodyparts3d/LATEST/

BodyParts3D ships 2234 unlabeled `FJ####.obj` files with no name index in the
zip itself — the ID → name mapping below was cross-referenced from the
archive's `isa_element_parts.txt` / `isa_parts_list_e.txt` index files.

**Both sides are included**, and both are real BP3D geometry rather than one
side mirrored in code. An arm is chiral: a mirrored right arm *is* a left arm,
so rendering flipped right-side meshes for a tracked left arm would put the
muscles on the wrong faces. `twin.html` swaps the mesh set to match the tracked
side instead.

Watch out for the two different ID conventions between sides:

| structure type | right | left |
|---|---|---|
| muscles | `FJ1478` | same number + `M` suffix (`FJ1478M`) |
| hand bones | `FJ3350` | an unrelated number (`FJ3240`) |

Verified as true mirrors: right-side geometry spans X `-229.9..-135.2`, left
spans `+135.2..+229.9`, and the derived segment lengths agree within ~1mm
(humerus 313.5 vs 312.6, forearm 240.3 vs 241.7, hand 134.7 vs 134.4).

## `bicep` (upper arm, anterior)
| file | FMA concept | structure |
|---|---|---|
| FJ1478.obj | FMA37686 | long head of right biceps brachii |
| FJ1512.obj | FMA37684 | short head of right biceps brachii |

## `tricep` (upper arm, posterior)
| file | FMA concept | structure |
|---|---|---|
| FJ1479.obj | FMA37699 | long head of right triceps brachii |
| FJ1480.obj | FMA37695 | medial head of right triceps brachii |
| FJ1477.obj | FMA37697 | lateral head of right triceps brachii |

## `frontDelt`
| file | FMA concept | structure |
|---|---|---|
| FJ1468.obj | FMA34680 | clavicular part of right deltoid |
| FJ1467.obj | FMA34682 | acromial part of right deltoid |

## `rearDelt`
| file | FMA concept | structure |
|---|---|---|
| FJ1513.obj | FMA34684 | spinal part of right deltoid |

## `forearmFlexor` (anterior forearm)
| file | FMA concept | structure |
|---|---|---|
| FJ1496.obj | FMA38460 | right flexor carpi radialis |
| FJ1502.obj | FMA38463 | right palmaris longus |
| FJ1475.obj | FMA38470 | right flexor digitorum superficialis (piece 1) |
| FJ1499.obj | FMA38470 | right flexor digitorum superficialis (piece 2) |
| FJ1497.obj | FMA38479 | right flexor digitorum profundus |

## `forearmExtensor` (posterior forearm)
| file | FMA concept | structure |
|---|---|---|
| FJ1487.obj | FMA38486 | right brachioradialis |
| FJ1490.obj | FMA38495 | right extensor carpi radialis longus |
| FJ1489.obj | FMA38498 | right extensor carpi radialis brevis |
| FJ1492.obj | FMA38501 | right extensor digitorum |
| FJ1472.obj | FMA38507 | right extensor carpi ulnaris (piece 1) |
| FJ1517.obj | FMA38507 | right extensor carpi ulnaris (piece 2) |

## `handGrip` (static hand/finger skeleton, rigidly attached at the wrist)
No live finger tracking exists in the pose pipeline (`backend/pose/estimator.py`
only ever extracts shoulder/elbow/wrist), so this is bone geometry only,
attached as one rigid unit — it completes the arm visually but doesn't
articulate per finger.

| file | FMA concept | structure |
|---|---|---|
| FJ3350.obj | FMA24464 | right first metacarpal bone |
| FJ3352.obj | FMA24466 | right second metacarpal bone |
| FJ3354.obj | FMA24468 | right third metacarpal bone |
| FJ3356.obj | FMA24470 | right fourth metacarpal bone |
| FJ3358.obj | FMA24472 | right fifth metacarpal bone |
| FJ3327.obj | FMA24450 | proximal phalanx of right thumb |
| FJ3322.obj | FMA24451 | proximal phalanx of right index finger |
| FJ3325.obj | FMA24452 | proximal phalanx of right middle finger |
| FJ3326.obj | FMA24453 | proximal phalanx of right ring finger |
| FJ3323.obj | FMA24454 | proximal phalanx of right little finger |
| FJ3303.obj | FMA24455 | middle phalanx of right index finger |
| FJ3306.obj | FMA24456 | middle phalanx of right middle finger |
| FJ3292.obj | FMA24457 | middle phalanx of right ring finger |
| FJ3304.obj | FMA24458 | middle phalanx of right little finger |
| FJ3198.obj | FMA24459 | distal phalanx of right thumb |
| FJ3193.obj | FMA24460 | distal phalanx of right index finger |
| FJ3196.obj | FMA24461 | distal phalanx of right middle finger |
| FJ3197.obj | FMA24462 | distal phalanx of right ring finger |
| FJ3194.obj | FMA24463 | distal phalanx of right little finger |

## Coordinate frame

BP3D `.obj` files are in the database's whole-body frame (millimetres, Z is
vertical with the shoulder at high Z). All files share that one frame, which is
what makes the data anatomical: the pieces already interlock. `twin.html`
therefore aligns each rig segment **once, as a unit**, rather than centring and
scaling each muscle independently.

The reference pose's arm is **not** vertical — measured lean is ~12° for the
humerus, ~13° for the forearm and ~26° for the hand — so the limb axis is
measured from the geometry (centroids of a slab at each end of a segment's Z
range) instead of assumed. See `AXIS_SLAB_FRACTION` and `measureSegmentAxis()`.

Segments are chained through **shared joints**: the elbow comes from the
biceps/triceps measurement and the wrist from the hand bones. The forearm is
deliberately *not* measured from its own muscles — brachioradialis originates
above the elbow and the finger flexor tendons run past the wrist, so its own
span reads 430mm against a true elbow→wrist bone of 240mm, which scaled those
muscles 1.79× too small.

## Left-side file map

Same structures as the right-side tables above, in rig-target order.

### `bicep` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1478M.obj | FMA37687 | long head of left biceps brachii |
| FJ1512M.obj | FMA37685 | short head of left biceps brachii |

### `tricep` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1479M.obj | FMA37700 | long head of left triceps brachii |
| FJ1480M.obj | FMA37696 | medial head of left triceps brachii |
| FJ1477M.obj | FMA37698 | lateral head of left triceps brachii |

### `frontDelt` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1468M.obj | FMA34681 | clavicular part of left deltoid |
| FJ1467M.obj | FMA34683 | acromial part of left deltoid |

### `rearDelt` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1513M.obj | FMA34685 | spinal part of left deltoid |

### `forearmFlexor` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1496M.obj | FMA38461 | left flexor carpi radialis |
| FJ1502M.obj | FMA38464 | left palmaris longus |
| FJ1475M.obj | FMA38471 | left flexor digitorum superficialis (piece 1) |
| FJ1499M.obj | FMA38471 | left flexor digitorum superficialis (piece 2) |
| FJ1497M.obj | FMA38480 | left flexor digitorum profundus |

### `forearmExtensor` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ1487M.obj | FMA38487 | left brachioradialis |
| FJ1490M.obj | FMA38496 | left extensor carpi radialis longus |
| FJ1489M.obj | FMA38499 | left extensor carpi radialis brevis |
| FJ1492M.obj | FMA38502 | left extensor digitorum |
| FJ1472M.obj | FMA38508 | left extensor carpi ulnaris (piece 1) |
| FJ1517M.obj | FMA38508 | left extensor carpi ulnaris (piece 2) |

### `handGrip` (left)

| file | FMA concept | structure |
|---|---|---|
| FJ3240.obj | FMA24465 | left first metacarpal bone |
| FJ3243.obj | FMA24467 | left second metacarpal bone |
| FJ3246.obj | FMA24469 | left third metacarpal bone |
| FJ3249.obj | FMA24471 | left fourth metacarpal bone |
| FJ3252.obj | FMA24473 | left fifth metacarpal bone |
| FJ3318.obj | FMA65470 | proximal phalanx of left thumb |
| FJ3313.obj | FMA71915 | proximal phalanx of left index finger |
| FJ3316.obj | FMA71908 | proximal phalanx of left middle finger |
| FJ3317.obj | FMA71916 | proximal phalanx of left ring finger |
| FJ3314.obj | FMA66791 | proximal phalanx of left little finger |
| FJ3296.obj | FMA23938 | middle phalanx of left index finger |
| FJ3299.obj | FMA23940 | middle phalanx of left middle finger |
| FJ3291.obj | FMA23942 | middle phalanx of left ring finger |
| FJ3297.obj | FMA23944 | middle phalanx of left little finger |
| FJ3188.obj | FMA23951 | distal phalanx of left thumb |
| FJ3183.obj | FMA23953 | distal phalanx of left index finger |
| FJ3186.obj | FMA23955 | distal phalanx of left middle finger |
| FJ3187.obj | FMA23957 | distal phalanx of left ring finger |
| FJ3184.obj | FMA23959 | distal phalanx of left little finger |

