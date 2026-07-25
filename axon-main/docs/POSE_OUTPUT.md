# Pose output — what our service sends you

Reply to `POSE_API.md`. We've implemented your UDP contract; this documents what
we emit, what we've verified, and the two things you should know before wiring
it up.

Everything below is implemented and tested (118 tests). Measured on a real
socket, not just unit-tested.

---

## 1. The UDP feed (your contract)

**Implemented as specified.**

| | |
|---|---|
| Transport | UDP, JSON, UTF-8 |
| Destination | `POSE_UDP_HOST` : `POSE_UDP_PORT` (defaults `127.0.0.1:9090`) |
| Rate | **~28 Hz** measured |
| Datagram size | **~126 bytes** (your limit: 2048) |
| Enabled by | `POSE_UDP_ENABLED=true` — **off by default** |

```json
{
  "shoulder":  [0.0,     0.0,   0.0],
  "elbow":     [0.0,     0.0,  -0.3],
  "wrist":     [0.16712, 0.0,  -0.49917],
  "timestamp": 1784999572.058932
}
```

- Metres, one complete JSON object per datagram, no newlines.
- `timestamp` is `time.time()` (epoch seconds), on **every** message.
- We send the **3D joint positions** format, not the pre-computed angles one.
- Only the tracked arm is sent.

### Coordinate frame

Your frame, as specified: **+X = subject's forward, +Y = subject's left, +Z = up.**

We take MediaPipe's world landmarks (metres, origin at hip midpoint, axes
aligned to the image: +x image-right, +y down, +z away from camera) and apply:

```
X = -z_mp      (forward = toward the camera)
Y = +x_mp      (subject's left = image-right, for a subject facing the camera)
Z = -y_mp      (up = image-up)
```

We verified this rather than assuming it, because you flagged that wrong axes
don't fail loudly — they drive the limb the wrong way:

- the mapping is a **pure rotation** — determinant `+1`, orthonormal — so
  handedness is preserved and no scale or skew is introduced;
- each axis sign is pinned by a test against a physical pose (arm hanging →
  `Z` negative; arm overhead → `Z` positive; arm toward camera → `X` positive;
  arm out to the subject's left → `Y` positive);
- end to end on a real socket, feeding a 0.30 m upper arm and 0.26 m forearm at
  40° of flexion, we received 0.300 m / 0.260 m and a recovered flexion of 40°.

> **Assumption to check:** this assumes the subject **faces the camera**. Seated
> and front-on is what we've built and tested for. If your rig films side-on,
> tell us — `Y` and `X` swap roles and we'll need to adjust.

### Requirement 2 — silence on lost tracking

**Honoured.** Verified live: 5 datagrams while tracking, **0** after tracking was
lost. There is no code path that sends a fallback, a last-known value or an
interpolation. If our stream stops, treat it as the arm being untracked.

We do **not** rely on you noticing staleness — we simply stop sending. Our
worst-case gap while tracking is ~36 ms, well inside your 300 ms window.

### Rate

**~28 Hz**, inside your 20–60 Hz band and close to your 30 Hz ideal.

It's camera-bound, not compute-bound: the webcam is 640×480@30fps and MediaPipe
inference is ~13 ms, so ~28 Hz is near the physical ceiling of this hardware. A
faster camera would raise it; a faster model would not.

> Relevant to your `POSE_FILTER_ALPHA = 0.35`: a first-order filter settles in a
> fixed number of *samples*, so its time constant moves with our rate. At 28 Hz
> it converges in ~190 ms. If you tuned that constant against a different
> assumed rate, it's worth a second look.

### Smoothing

We send **raw** per-frame positions — unsmoothed, as you'd expect to filter them
yourself. (The smoothing in our system is display-only, applied in the 3D twin's
render loop, and never touches this feed.)

---

## 2. Also available, if useful

Not part of your contract — mentioned only in case they save you work.

### WebSocket `/ws` (port 8000)

Richer, TCP, and it reports *why* there's no pose — which the UDP feed
deliberately cannot, since it goes silent instead.

```json
{"type":"pose","landmarks":{...}|null,"side":"left"|"right"|null,
 "status":"tracking"|"no_person"|"arm_not_visible"}
```

**Do not substitute this for the UDP feed.** It publishes on *every* frame
including lost-tracking ones (our UI needs that to prompt the user), which is
the exact opposite of your requirement 2. It also carries **normalized image
coordinates**, not metres — no scale, so it would be wrong for control, not
merely imprecise. Two separate emitters on purpose.

You can also send `{"type":"set_side","side":"left"|"right"|null}` to force
which arm is tracked (`null` = auto-pick). A forced side never silently falls
back to the other arm.

### `GET /camera.mjpeg`

`multipart/x-mixed-replace` JPEG preview of the same frames the pose runs on —
useful for sanity-checking what the estimator is actually seeing, without
opening a second handle to the camera.

---

## 3. Running it

```bash
POSE_UDP_ENABLED=true POSE_UDP_HOST=<your-host> uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

It's off by default because enabling it starts sending pose to whatever is
listening — which on your rig drives a limb.

To check the wire format without our camera, point your receiver at port 9090
and use your own fake-estimator snippet from `POSE_API.md` — our payload is
field-for-field identical to the format it sends.

---

## 4. Open questions for you

1. **Subject orientation** — we assume front-on to the camera (see above). Is
   that right for your setup?
2. **Host/port** — we default to `127.0.0.1:9090`. Same machine, or should we
   point somewhere else?
3. **Actuation** — we understand these are two independent rigs (your control
   loop and our TENS path drive separate hardware). If that's ever not true,
   flag it: our controller's single-flight lock and cooldown only arbitrate our
   own commands and are structurally blind to yours, so two controllers on one
   relay board would defeat both sets of safety rails.
