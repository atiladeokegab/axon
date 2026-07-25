# Pose API — contract for the pose-estimation service

This is the interface between the **pose estimator** (a teammate's service) and
the **control loop**. We do not implement vision; we consume its output.

Hand this document to whoever builds the estimator.

---

## Transport

**UDP datagrams, JSON payload.**

| | |
|---|---|
| Host | the machine running `controller/run.py` |
| Port | **9090** (`POSE_LISTEN_PORT`) |
| Rate | **20–60 Hz** (30 Hz is ideal) |
| Encoding | UTF-8 JSON, one complete message per datagram, < 2048 bytes |

UDP is chosen deliberately: we want the *newest* pose, never a replayed backlog
of stale ones. Dropped packets are fine — a real outage stops the stream, which
is detected and stops stimulation.

---

## Message format (preferred): 3D joint positions

```json
{
  "shoulder":  [0.00, 0.00, 0.00],
  "elbow":     [0.02, 0.01, -0.30],
  "wrist":     [0.05, 0.02, -0.55],
  "timestamp": 1721740000.123
}
```

| Field | Type | Notes |
|---|---|---|
| `shoulder` | `[x, y, z]` | metres |
| `elbow` | `[x, y, z]` | metres |
| `wrist` | `[x, y, z]` | metres |
| `timestamp` | float | seconds (monotonic or epoch); optional but preferred |

### Coordinate frame

Right-handed, subject-centred:

```
+X = subject's forward
+Y = subject's left
+Z = up
```

Origin may be anywhere (we only use **relative** vectors between joints), but
**axis orientation must match** — otherwise the angles come out wrong and the
controller drives the arm the wrong way. If your estimator uses a different
convention (e.g. MediaPipe's image-space axes), convert before sending.

Only the **stimulated arm** is needed.

---

## Message format (alternative): pre-computed angles

If your side already computes joint angles, send them directly:

```json
{
  "elbow": 92.5,
  "shoulder_flex": 30.0,
  "shoulder_abd": 15.0,
  "timestamp": 1721740000.123
}
```

The receiver auto-detects which format you sent (number vs. list for `elbow`).

### Angle definitions

| Name | Degrees | Zero | Positive direction |
|---|---|---|---|
| `elbow` | 0–150 | arm fully straight | more flexed |
| `shoulder_flex` | −20–110 | arm hanging at side | arm forward/up |
| `shoulder_abd` | 0–90 | arm hanging at side | arm out to the side |

All measured from the **arm-hanging-at-rest** pose of a seated subject — the
natural zero, and the pose gravity returns the limb to.

---

## Requirements that matter

1. **Send continuously.** Poses older than **300 ms** are treated as stale and
   stimulation stops. Do not send only on change.
2. **Do not send a guess.** If tracking is lost or the arm is occluded, **stop
   sending** (or send with an old timestamp). Silence safely stops stimulation;
   a fabricated pose makes the controller drive the limb against reality.
3. **Timestamp every message.** It is how we detect staleness.
4. Smoothing on your side is welcome; we additionally low-pass filter
   (`POSE_FILTER_ALPHA = 0.35`) because vision jitter chatters the relays.
5. Malformed messages are counted and dropped, never partially applied.

---

## Testing your integration

Send test poses with no hardware involved. **Activate the venv in each terminal
first** (`.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate`
on macOS/Linux):

```bash
# terminal 1 — controller, real pose input, nothing stimulated
cd controller && python run.py --no-board

# terminal 2 — fake estimator
python - <<'PY'
import json, socket, time, math
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
t = 0.0
while True:
    bend = math.radians(20 + 40 * (0.5 + 0.5 * math.sin(t)))
    msg = {
        "shoulder": [0, 0, 0],
        "elbow":    [0, 0, -0.30],
        "wrist":    [0.26 * math.sin(bend), 0, -0.30 - 0.26 * math.cos(bend)],
        "timestamp": time.time(),
    }
    s.sendto(json.dumps(msg).encode(), ("127.0.0.1", 9090))
    t += 0.05
    time.sleep(1 / 30)
PY
```

The controller display should show `pose:OK` and the measured elbow angle
tracking the sine wave. If it shows `pose:STALE`, check the port and rate; if
angles look inverted, check the coordinate frame.
