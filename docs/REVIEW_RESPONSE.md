# Code review — resolution log

Findings from the external review, and what was done. Verified by
`controller/test_simulation.py` (74 checks at the time of this review; 106 today).

## Blocking (all fixed)

| # | Finding | Status |
|---|---|---|
| 1 | `shoulder_flex` saturated at exactly 90° | **Fixed** — `atan2` no longer clamps its denominator; abduction now via `asin`, so the axes are independent. Verified 30–130° exact, and combined flex+abd decouple. |
| 2 | `POSE_API.md` staleness contract unimplemented | **Fixed** — `PoseReceiver._frozen()` rejects a non-advancing sender timestamp. Compared against the sender's own previous value, so no clock sync needed. |
| 3 | Antagonist rule enforced only on the PC | **Fixed** — `ANTAGONIST_PAIRS` in `pins.py`; `StimArray.apply()` zeroes **both** sides of any pair commanded together and counts it. |
| 4 | Command link unauthenticated | **Fixed** — shared `CONTROL_TOKEN` required on every control packet; discovery deliberately left open so the board stays findable. |

## Secondary (all fixed)

- `boot.py` secrets fallback could never import (`device_secrets.example.py` is
  not a module name) — fallback removed, so a missing file fails clearly.
- `press_timer_now()` blocked the 1 ms loop for 250 ms — now non-blocking,
  scheduling the release like the automatic path already did.
- Two assertions that could not fail (`any_on or True`; `apply(...) is not None`
  under a name claiming refusal) — both replaced with real assertions. The
  second now tests refusal *and* acceptance either side of a watchdog expiry.

## Doc drift

- Test count: README / TESTING said 37, MY_SETUP 46 — all now **74**.
- Deadband: README and `run.py`'s on-screen help said "~5–6°", code says 3.0 —
  both corrected. `CONTROL.md`'s 5–6° references are historical ("used to be"),
  which is accurate.

## Accepted, not changed

- `forward_kinematics()` remains unused scaffolding, now documented as such. It
  is not in the control path; the cross-coupling the review saw round-tripping
  through it is a property of that crude model, not of the live angle
  extraction, which is now verified independent.
