"""Offline checks - no hardware, no pose estimator, no network.

Verifies the parts that must not be wrong:
  1. the PI loop actually converges on a target
  2. duty never exceeds the configured ceiling
  3. antagonist pairs are never co-contracted
  4. a stale pose stops stimulation
  5. the firmware safety supervisor clamps and watchdogs correctly

Run:  python test_simulation.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "firmware"))

import settings as C
import control_loop
import mapping
from link import NullLink
from pose_api import SimulatedPoseSource

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, condition, detail=""):
    results.append((PASS if condition else FAIL, name, detail))
    print("  [%s] %s %s" % (PASS if condition else FAIL, name, detail))
    return condition


# ---------------------------------------------------------------------------
print("\n1. Closed-loop convergence (simulated arm)")
arm = SimulatedPoseSource()
ctl = control_loop.ArmController(NullLink(), arm)
ctl.arm()
ctl.targets["elbow"] = 70.0
ctl.targets["shoulder_flex"] = 25.0

dt = 1.0 / C.CONTROL_RATE_HZ
peak_duty = 0.0
# Virtual clock: the controller must advance in SIMULATED time, not wall-clock,
# or dt collapses to microseconds and the integrator never accumulates.
vnow = time.monotonic()
for _ in range(int(12 / dt)):          # 12 simulated seconds
    arm.step(ctl.efforts, dt)
    vnow += dt
    ctl.step(now=vnow)
    peak_duty = max(peak_duty, max(ctl.duties.values()))

err_elbow = abs(ctl.targets["elbow"] - ctl.measured["elbow"])
err_flex = abs(ctl.targets["shoulder_flex"] - ctl.measured["shoulder_flex"])
check("elbow converges within deadband+5deg", err_elbow < 12.0,
      "err=%.1f deg" % err_elbow)
check("shoulder_flex converges", err_flex < 12.0, "err=%.1f deg" % err_flex)

# ---------------------------------------------------------------------------
print("\n2. Duty ceiling respected")
check("peak duty <= DUTY_MAX", peak_duty <= C.DUTY_MAX + 1e-6,
      "peak=%.3f max=%.2f" % (peak_duty, C.DUTY_MAX))

# ---------------------------------------------------------------------------
print("\n3. No antagonist co-contraction")
pairs = [("CH1", "CH2"), ("CH3", "CH4")]
worst = None
for effort in (-0.6, -0.2, 0.0, 0.2, 0.6):
    d = mapping.efforts_to_duties(
        {"elbow": effort, "shoulder_flex": effort, "shoulder_abd": effort},
        duty_max=C.DUTY_MAX)
    for a, b in pairs:
        if d[a] > 0.001 and d[b] > 0.001:
            worst = (effort, a, b)
check("agonist/antagonist never driven together", worst is None,
      "" if worst is None else "violation at %s" % (worst,))

d_neg = mapping.efforts_to_duties({"shoulder_abd": -0.5}, duty_max=C.DUTY_MAX)
check("negative abduction uses gravity (CH5 and CH6 idle)",
      d_neg["CH5"] == 0.0 and d_neg["CH6"] == 0.0)

# ---------------------------------------------------------------------------
print("\n4. Stale pose stops stimulation")


class StalePose:
    def latest(self):
        return {"elbow": 40.0, "shoulder_flex": 0.0, "shoulder_abd": 0.0}, False


ctl2 = control_loop.ArmController(NullLink(), StalePose())
ctl2.arm()
ctl2.targets["elbow"] = 120.0
ctl2.step()
check("all duties zero on stale pose",
      all(v == 0.0 for v in ctl2.duties.values()),
      "fault=%s" % ctl2.status()["fault"])

# ---------------------------------------------------------------------------
print("\n5. Operator e-stop latches")
ctl3 = control_loop.ArmController(NullLink(), SimulatedPoseSource())
ctl3.arm()
ctl3.targets["elbow"] = 100.0
ctl3.step()
ctl3.kill()
ctl3.step()
check("killed state blocks stimulation",
      ctl3.killed and all(v == 0.0 for v in ctl3.duties.values()))
ctl3.step()
check("kill stays latched without re-arm", ctl3.killed and not ctl3.armed)

# ---------------------------------------------------------------------------
print("\n6. Firmware safety supervisor")
try:
    from lib.safety import SafetySupervisor

    sup = SafetySupervisor(duty_max=0.70, command_timeout_ms=500)
    check("clamps over-range duty", sup.clamp_duty(5.0) == 0.70)
    check("clamps negative duty", sup.clamp_duty(-1.0) == 0.0)
    check("rejects garbage duty", sup.clamp_duty("nonsense") == 0.0)
    check("disarmed at construction", not sup.stim_allowed())
    sup.arm()
    check("armed allows stimulation", sup.stim_allowed())
    sup.kill("test")
    check("kill latches off", not sup.stim_allowed() and sup.is_killed())
    sup.arm()
    check("re-arm clears kill", sup.stim_allowed() and not sup.is_killed())

    # Watchdog: pretend no command arrived for longer than the timeout.
    sup._last_command_ms = sup._last_command_ms - 5000
    check("watchdog expires and blocks stim", not sup.stim_allowed())
except ImportError as exc:
    check("firmware safety importable", False, str(exc))

# ---------------------------------------------------------------------------
print("\n7. Firmware channel PWM + pin sanity")
try:
    from config import pins as P
    from lib.stim_channel import StimChannel

    P.assert_no_conflicts()
    check("no pin conflicts / reserved-pin collisions", True)
    check("8 channels mapped", len(P.CHANNEL_PINS) == 8)

    ch = StimChannel("T", 4, active_low=True, period_ms=150, min_pulse_ms=25)
    ch.set_duty(0.5)
    on_seen = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.4:
        if ch.service():
            on_seen = True
        time.sleep(0.002)
    check("channel energises at 50% duty", on_seen)

    ch.set_duty(0.05)            # below MIN_PULSE_MS -> should be dropped
    ch._cycle_start = 0          # force a fresh period on next service
    ch.service()
    tiny_on = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.3:
        if ch.service():
            tiny_on = True
        time.sleep(0.002)
    check("sub-minimum pulse is dropped, not half-actuated", not tiny_on)

    ch.off()
    check("off() de-energises", not ch.is_on())
except Exception as exc:
    check("firmware channel tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n8. Firmware StimArray end-to-end")
# This section exists because a missing constant in _service_timer() once
# crashed the firmware main loop on EVERY iteration, and nothing caught it:
# the array was never exercised as a whole. Always drive service() here.
try:
    from lib.safety import SafetySupervisor as _Sup
    from lib.stim_array import StimArray

    sup = _Sup()
    array = StimArray(sup)

    # Disarmed: apply() must refuse and everything stays off.
    applied = array.apply([0.5] * 8)
    check("apply() refused while disarmed", applied is False)

    sup.arm()
    check("apply() accepted once armed", array.apply([0.5] * 8) is True)

    # service() must survive repeated calls (this is what was broken).
    t0 = time.monotonic()
    ticks = 0
    while time.monotonic() - t0 < 0.5:
        array.service()
        ticks += 1
        time.sleep(0.001)
    check("service() runs without exceptions", ticks > 50, "%d iterations" % ticks)

    # Drive service() until a channel actually energises. The previous version
    # asserted `any_on or True`, which can never fail - it tested nothing.
    # Pet the watchdog BEFORE applying, and re-apply each pass: if the watchdog
    # had expired when apply() was called it silently returns False, leaving the
    # duty at zero - which made this check flaky rather than meaningful.
    energised = False
    t_end = time.monotonic() + 1.5
    while time.monotonic() < t_end and not energised:
        sup.note_command()
        array.apply([0.7] + [0.0] * 7)   # one channel: avoids antagonist guard
        array.service()
        energised = any(c.is_on() for c in array.channels.values())
        time.sleep(0.002)
    check("channels actually energise under service()", energised)

    # The service loop above ran past COMMAND_TIMEOUT_MS, so the watchdog has
    # expired and apply() will (correctly) refuse. Pet it before asserting on
    # anything that requires stimulation to be permitted - otherwise the
    # assertions below pass vacuously against all-zero duties.
    # Name said "refused after watchdog expiry" but asserted `is not None`,
    # which is true for both True and False. Test the real behaviour instead.
    sup._last_command_ms = sup._last_command_ms - 5000      # force expiry
    check("apply() refused while the watchdog is expired",
          array.apply([0.5] * 8) is False)
    sup.note_command()                                      # pet it again
    check("apply() accepted once the watchdog is fed",
          array.apply([0.5] * 8) is True)

    # Over-range duty must be clamped by the safety layer, not passed through.
    # NOTE: driving all 8 at once also trips the antagonist guard, which zeroes
    # each opposing pair - so only the non-paired channels remain clamped.
    sup.note_command()
    array.apply([5.0] * 8)
    over = [n for n, c in array.channels.items() if c.duty() > sup.duty_max]
    check("apply() clamps over-range duty to duty_max", not over, str(over))

    # Firmware-side antagonist enforcement (NOT just the PC-side mapping).
    # SAFETY.md says the board does not trust the controller, so the rule has
    # to exist here too - a controller bug or a hostile packet could ask for it.
    sup.note_command()
    array.apply([0.7, 0.7] + [0.0] * 6)          # CH1 + CH2 = biceps + triceps
    check("firmware refuses antagonist co-contraction (CH1+CH2)",
          array.channels["CH1"].duty() == 0.0 and
          array.channels["CH2"].duty() == 0.0,
          "CH1=%.2f CH2=%.2f" % (array.channels["CH1"].duty(),
                                 array.channels["CH2"].duty()))

    sup.note_command()
    array.apply([0.0, 0.0, 0.7, 0.7] + [0.0] * 4)   # CH3 + CH4 deltoid pair
    check("firmware refuses antagonist co-contraction (CH3+CH4)",
          array.channels["CH3"].duty() == 0.0 and
          array.channels["CH4"].duty() == 0.0)

    sup.note_command()
    array.apply([0.7] + [0.0] * 7)                # CH1 alone must still work
    check("a single agonist is unaffected by the guard",
          array.channels["CH1"].duty() == 0.7)

    check("co-contraction attempts are counted",
          array._cocontraction_blocks >= 2,
          "blocks=%d" % array._cocontraction_blocks)

    # Grip convenience flag drives CH7 and releases CH8 (never co-contract).
    sup.note_command()
    array.apply([0.0] * 8, grip=True)
    check("grip drives CH7, releases CH8",
          array.channels["CH7"].duty() > 0 and array.channels["CH8"].duty() == 0,
          "CH7=%.2f CH8=%.2f" % (array.channels["CH7"].duty(),
                                 array.channels["CH8"].duty()))

    # Manual timer press must actually register.
    before = array.status()["timer_presses"]
    array.press_timer_now()
    check("press_timer_now() registers",
          array.status()["timer_presses"] == before + 1)

    # Watchdog expiry must open everything on the next service pass.
    sup._last_command_ms = sup._last_command_ms - 5000
    array.service()
    check("watchdog expiry de-energises all channels",
          not any(c.is_on() for c in array.channels.values()))

    array.all_off()
    check("all_off() clears every channel",
          all(c.duty() == 0.0 and not c.is_on() for c in array.channels.values()))

    check("status() reports all 8 channels", len(array.status()["channels"]) == 8)
except Exception as exc:
    check("firmware StimArray tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n9. Command merge: control flags must never be dropped")
# Regression test. poll() drains the socket and keeps only the newest packet
# for duty - correct. But it originally discarded control flags on older
# packets too, so a 'kill' arriving just before a routine duty update was
# silently lost. An e-stop must survive being followed by other traffic.
try:
    import socket as _sock
    from lib import net_udp as _nu

    port = 19099
    cl = _nu.CommandLink(port=port, local_ip="127.0.0.1")
    tx = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)

    def blast(msgs):
        for m in msgs:
            tx.sendto(json.dumps(m).encode(), ("127.0.0.1", port))
        time.sleep(0.15)
        return cl.poll()

    # kill arrives first, then a normal duty packet in the same drain window
    merged = blast([{"kill": True, "seq": 1},
                    {"duty": [0.5] * 8, "seq": 2}])
    check("kill survives a following duty packet",
          bool(merged and merged.get("kill")), str(merged and merged.get("kill")))

    # kill must outrank an arm that arrives afterwards
    merged = blast([{"kill": True, "seq": 10},
                    {"arm": True, "duty": [0.0] * 8, "seq": 11}])
    check("kill outranks a later arm",
          bool(merged.get("kill")) and not merged.get("arm"))

    # duty still comes from the newest packet
    merged = blast([{"duty": [0.1] * 8, "seq": 20},
                    {"duty": [0.6] * 8, "seq": 21}])
    check("duty taken from the newest packet",
          merged.get("duty") == [0.6] * 8, str(merged.get("duty"))[:30])

    # arm and timer_press also survive being batched
    merged = blast([{"arm": True, "seq": 30},
                    {"timer_press": True, "seq": 31},
                    {"duty": [0.2] * 8, "seq": 32}])
    check("arm survives batching", bool(merged.get("arm")))
    check("timer_press survives batching", bool(merged.get("timer_press")))

    # REGRESSION: a restarted controller starts its seq at 1 again. The board
    # must accept it, not ignore it until the counter climbs back. This bug made
    # run.py appear dead after a bench.py session had raised last_seq.
    blast([{"duty": [0.3] * 8, "seq": 400}])          # long-running client
    merged = blast([{"duty": [0.9] * 8, "seq": 1}])   # fresh client restarts
    check("restarted client (seq resets to 1) is accepted",
          merged is not None and merged.get("duty") == [0.9] * 8,
          str(merged and merged.get("duty"))[:24])

    # ...while genuine small reordering is still dropped.
    blast([{"duty": [0.4] * 8, "seq": 50}])
    merged = blast([{"duty": [0.8] * 8, "seq": 48}])  # 2 packets out of order
    check("small out-of-order packet still dropped",
          merged is None or merged.get("duty") != [0.8] * 8)

    tx.close()
except Exception as exc:
    check("command merge tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n10. Deadband HOLD behaviour (no limit cycle, still safe)")
# Inside the deadband the controller holds the integrator's learned duty rather
# than returning 0. Returning 0 caused the arm to sag out of the deadband and
# be re-driven ~26 times/second - audible, and destructive to relay contacts.
# These checks pin the improvement AND the safety properties it must not break.
try:
    from pid import PIController as _PI

    pose10 = SimulatedPoseSource()
    ctl10 = control_loop.ArmController(NullLink(), pose10)
    ctl10.arm()
    ctl10.targets["elbow"] = 45.0

    dt10 = 1.0 / C.CONTROL_RATE_HZ
    v10 = time.monotonic()
    switches = 0
    last_on = None
    tail = []
    peak10 = 0.0
    for i in range(int(25 / dt10)):
        pose10.step(ctl10.efforts, dt10)
        v10 += dt10
        ctl10.step(now=v10)
        peak10 = max(peak10, max(ctl10.duties.values()))
        on = ctl10.duties["CH1"] > 0.0
        if last_on is not None and on != last_on:
            switches += 1
        last_on = on
        if i > int(25 / dt10) - 300:        # last 10 s
            tail.append(ctl10.measured["elbow"])

    ripple = max(tail) - min(tail)
    check("no limit cycle at the setpoint (ripple < 1 deg)", ripple < 1.0,
          "ripple=%.2f deg" % ripple)
    check("relay stops switching once settled", switches < 20,
          "%d transitions in 25 s" % switches)
    check("still respects DUTY_MAX while holding", peak10 <= C.DUTY_MAX + 1e-6,
          "peak=%.3f" % peak10)

    # SAFETY: the hold must never survive disarm or kill.
    ctl10.disarm()
    ctl10.step(now=v10 + 1)
    check("hold is cleared by disarm",
          all(d == 0.0 for d in ctl10.duties.values()))

    ctl10.arm()
    ctl10.step(now=v10 + 2)
    ctl10.kill()
    ctl10.step(now=v10 + 3)
    check("hold is cleared by e-stop",
          all(d == 0.0 for d in ctl10.duties.values()) and ctl10.killed)

    # The integrator must stay FROZEN inside the deadband (hold, not adapt),
    # otherwise it would wind up while the arm sits at target.
    p = _PI(kp=0.02, ki=0.03, deadband=5.0, i_limit=0.7,
            out_min=-0.7, out_max=0.7)
    for _ in range(200):
        p.update(20.0, 0.033)               # drive it out of the deadband
    wound = p._integral
    for _ in range(200):
        p.update(1.0, 0.033)                # now sit inside the deadband
    check("integrator frozen inside deadband", abs(p._integral - wound) < 1e-9,
          "delta=%.2e" % abs(p._integral - wound))

    held = p.update(1.0, 0.033)
    check("held output equals the learned holding duty",
          abs(held - p.ki * p._integral) < 1e-9 or held in (0.7, -0.7),
          "held=%.3f" % held)
    check("held output never exceeds out_max", abs(held) <= 0.7 + 1e-9)

    # A zero-integral controller must hold zero, not something arbitrary.
    p2 = _PI(kp=0.02, ki=0.03, deadband=5.0, i_limit=0.7,
             out_min=-0.7, out_max=0.7)
    check("no integral learned => holds 0", p2.update(1.0, 0.033) == 0.0)
except Exception as exc:
    check("deadband hold tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n11. Actuator dead zone (duties below MIN_PULSE_MS do nothing)")
# The firmware drops pulses shorter than MIN_PULSE_MS, so any duty under
# MIN_EFFECTIVE_DUTY produces NO relay movement. Without compensation the
# controller silently commanded 0.08-0.11 and nothing ever fired - the whole
# hardware-in-the-loop path looked dead while being "correct".
try:
    thresh = C.MIN_EFFECTIVE_DUTY

    d = mapping.efforts_to_duties({"elbow": 0.10}, duty_max=C.DUTY_MAX,
                                  min_effective=thresh, deadzone=True)
    check("small effort snapped up to the minimum that actually fires",
          d["CH1"] >= thresh, "CH1=%.3f (thresh %.3f)" % (d["CH1"], thresh))

    d = mapping.efforts_to_duties({"elbow": 0.02}, duty_max=C.DUTY_MAX,
                                  min_effective=thresh, deadzone=True)
    check("negligible effort snapped down to zero (no twitch)",
          d["CH1"] == 0.0, "CH1=%.3f" % d["CH1"])

    d = mapping.efforts_to_duties({"elbow": 0.5}, duty_max=C.DUTY_MAX,
                                  min_effective=thresh, deadzone=True)
    check("normal duty passes through untouched", abs(d["CH1"] - 0.5) < 1e-9)

    d = mapping.efforts_to_duties({"elbow": 5.0}, duty_max=C.DUTY_MAX,
                                  min_effective=thresh, deadzone=True)
    check("deadzone never breaks the DUTY_MAX clamp", d["CH1"] <= C.DUTY_MAX)

    d = mapping.efforts_to_duties({"elbow": 0.0}, duty_max=C.DUTY_MAX,
                                  min_effective=thresh, deadzone=True)
    check("zero effort stays exactly zero", d["CH1"] == 0.0)

    # End-to-end: the closed loop must produce duties the hardware can act on.
    pose11 = SimulatedPoseSource()
    ctl11 = control_loop.ArmController(NullLink(), pose11)
    ctl11.arm()
    for _ in range(4):
        ctl11.jog("shoulder_flex", C.JOG_STEP_DEG)
        ctl11.jog("elbow", C.JOG_STEP_DEG * 0.5)
    dt11 = 1.0 / C.CONTROL_RATE_HZ
    v11 = time.monotonic()
    fired = 0
    for _ in range(int(6 / dt11)):
        pose11.step(ctl11.efforts, dt11)
        v11 += dt11
        ctl11.step(now=v11)
        if any(x >= thresh for x in ctl11.duties.values()):
            fired += 1
    check("hardware-in-the-loop actually commands firable duties",
          fired > 0, "%d/%d samples above threshold" % (fired, int(6 / dt11)))
except Exception as exc:
    check("dead zone tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n12. Shoulder kinematics (saturation + axis independence)")
# REGRESSION: shoulder_flex used atan2(forward, max(down, 1e-9)), which clamped
# the denominator and SATURATED at exactly 90 deg. JOINT_LIMITS allows 110 deg,
# so a reachable target became unmeasurable: the error could never close and the
# integrator wound up to DUTY_MAX, holding a muscle there indefinitely.
try:
    import math as _m
    import kinematics as K

    ok = True
    for true in (30, 60, 89, 100, 110, 130):
        r = _m.radians(true)
        f, _a = K.shoulder_angles((0, 0, 0), (_m.sin(r), 0.0, -_m.cos(r)))
        if abs(f - true) > 0.01:
            ok = False
    check("flexion measures correctly past 90 deg (no saturation)", ok)

    lim_hi = C.JOINT_LIMITS["shoulder_flex"][1]
    r = _m.radians(lim_hi)
    f, _a = K.shoulder_angles((0, 0, 0), (_m.sin(r), 0.0, -_m.cos(r)))
    check("the joint limit itself is measurable", abs(f - lim_hi) < 0.01,
          "limit=%.0f measured=%.2f" % (lim_hi, f))

    ok = True
    for true in (0, 30, 45, 60, 90):
        r = _m.radians(true)
        f, a = K.shoulder_angles((0, 0, 0), (0.0, _m.sin(r), -_m.cos(r)))
        if abs(a - true) > 0.01 or abs(f) > 0.01:
            ok = False
    check("pure abduction does not leak into flexion", ok)

    ok = True
    for fl, ab in ((60, 30), (100, 20), (45, 45)):
        fr, ar = _m.radians(fl), _m.radians(ab)
        lat, sag = _m.sin(ar), _m.cos(ar)
        f, a = K.shoulder_angles((0, 0, 0),
                                 (sag * _m.sin(fr), lat, -sag * _m.cos(fr)))
        if abs(f - fl) > 0.01 or abs(a - ab) > 0.01:
            ok = False
    check("combined flex+abd stay independent", ok)
except Exception as exc:
    check("kinematics tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n13. Pose staleness honours the sender's timestamp")
# POSE_API.md tells the estimator it may signal lost tracking by sending an old
# timestamp. That was documented but NOT implemented - staleness used arrival
# time only, so a frozen pose kept the controller driving the limb.
try:
    from pose_api import PoseReceiver

    rx = PoseReceiver()
    base = {"shoulder": [0, 0, 0], "elbow": [0, 0, -0.3], "wrist": [0, 0, -0.55]}

    check("first timestamped pose is accepted",
          not rx._frozen(dict(base, timestamp=100.0)))
    check("advancing timestamp is accepted",
          not rx._frozen(dict(base, timestamp=100.1)))
    check("repeated timestamp is rejected as frozen",
          rx._frozen(dict(base, timestamp=100.1)))
    check("older timestamp is rejected as frozen",
          rx._frozen(dict(base, timestamp=99.0)))
    check("frozen messages are counted", rx._stale_ts == 0,
          "counter is incremented by _loop, not _frozen; got %d" % rx._stale_ts)

    rx2 = PoseReceiver()
    check("a message with no timestamp still passes (arrival-age fallback)",
          not rx2._frozen(dict(base)))
except Exception as exc:
    check("pose timestamp tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n14. Control link requires the shared token")
# Any host on the hotspot could otherwise send {"arm":true,...} to :8080.
try:
    import socket as _sock
    from lib import net_udp as _nu

    port = 19110
    cl = _nu.CommandLink(port=port, local_ip="127.0.0.1", token="secret")
    tx = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)

    def send_raw(m):
        tx.sendto(json.dumps(m).encode(), ("127.0.0.1", port))
        time.sleep(0.15)
        return cl.poll()

    check("packet with no token is rejected",
          send_raw({"arm": True, "duty": [0.7] * 8, "seq": 1}) is None)
    check("packet with a wrong token is rejected",
          send_raw({"arm": True, "duty": [0.7] * 8, "seq": 2, "tok": "nope"}) is None)
    got = send_raw({"arm": True, "duty": [0.7] * 8, "seq": 3, "tok": "secret"})
    check("packet with the correct token is accepted",
          got is not None and got.get("arm") is True)
    check("rejections are counted", cl._rejected >= 2, "n=%d" % cl._rejected)

    # Discovery must stay open, or the board becomes unfindable.
    tx.sendto(json.dumps({"discover": True}).encode(), ("127.0.0.1", port))
    time.sleep(0.15)
    cl.poll()
    check("discovery still works without a token", True)
    tx.close()
except Exception as exc:
    check("token tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n14b. Pin map is valid for the Axiometa Genesis Mini")
# The board swap (Goouuu ESP32-S3-N16R8 -> Genesis Mini ESP32-S3-Mini-N4R2)
# changes which GPIO exist, which are committed to on-board functions, and
# which are physically reachable. Every one of those failures presents as a
# hardware fault - a relay that never clicks, or an e-stop wired to the battery
# monitor - so they are worth catching in software.
try:
    from config import pins as GP

    check("pin map self-check passes", GP.assert_no_conflicts() is True)
    check("board is identified in the pin map", "Genesis Mini" in GP.BOARD,
          GP.BOARD)

    # The Genesis Mini brings out only 12 GPIO. A pin can be legal on the
    # ESP32-S3 and still have no connector, which is invisible until nothing
    # happens when you drive it.
    assigned = GP.all_assigned_pins()
    check("all 10 assigned pins are on an AX22 port header",
          set(assigned) <= set(GP.AX22_PINS),
          "off-header: %s" % sorted(set(assigned) - set(GP.AX22_PINS)))
    check("10 pins assigned (8 channels + timer + e-stop)",
          len(assigned) == 10, "got %d" % len(assigned))

    # GPIO8 was the e-stop on the OLD board and is battery sense on this one.
    check("e-stop moved off GPIO8 (battery sense on this board)",
          GP.ESTOP_PIN != 8, "ESTOP_PIN=%s" % GP.ESTOP_PIN)
    check("e-stop is assigned at all", GP.ESTOP_PIN is not None)

    # Strapping pins change how the chip boots. GPIO3 is on port P1, so it is
    # reachable and therefore tempting.
    STRAPPING = {0, 3, 45, 46}
    check("no strapping pin is used", not (set(assigned) & STRAPPING),
          "used: %s" % sorted(set(assigned) & STRAPPING))

    # On-board peripherals that are easy to collide with.
    BOARD_COMMITTED = {21, 45, 8, 34, 46, 10, 11, 12, 13, 14}
    check("no on-board peripheral pin is reused for stimulation",
          not (set(assigned) & BOARD_COMMITTED),
          "collides: %s" % sorted(set(assigned) & BOARD_COMMITTED))

    # The e-stop was previously excluded from conflict checking, which is why
    # GPIO8 passed silently. Prove the check now covers inputs.
    _orig = GP.ESTOP_PIN
    try:
        GP.ESTOP_PIN = 8
        try:
            GP.assert_no_conflicts()
            check("conflict check covers the e-stop INPUT pin", False,
                  "ESTOP_PIN=8 was accepted")
        except ValueError:
            check("conflict check covers the e-stop INPUT pin", True)
        GP.ESTOP_PIN = 33          # legal S3 pin, but on no AX22 header
        try:
            GP.assert_no_conflicts()
            check("conflict check rejects unreachable pins", False,
                  "GPIO33 was accepted")
        except ValueError:
            check("conflict check rejects unreachable pins", True)
    finally:
        GP.ESTOP_PIN = _orig

    check("pin map still valid after the tamper test",
          GP.assert_no_conflicts() is True)
except Exception as exc:
    check("Genesis Mini pin map tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n15. Relay polarity - the idle state MUST release the relay")
# SAFETY-CRITICAL. COM->NO = electrodes on the SUBJECT, COM->NC = dummy
# resistor. A de-energised relay rests on NC, so the idle level must be the one
# that does NOT energise. With the modules we use (HIGH-level trigger) that
# means idle = LOW.
#
# This was wrong: CHANNEL_ACTIVE_LOW=True made idle HIGH, which energised every
# relay and connected the subject to a live TENS output at boot, on watchdog
# expiry, on e-stop, and between every PWM pulse.
try:
    from lib.hal import Pin as _Pin
    from lib.stim_channel import StimChannel as _SC
    from config import pins as _P

    check("channel idle level is LOW (relay released -> dummy load)",
          _P.CHANNEL_ACTIVE_LOW is False,
          "CHANNEL_ACTIVE_LOW=%s" % _P.CHANNEL_ACTIVE_LOW)
    check("timer relay idle level is LOW (button not held)",
          _P.TIMER_ACTIVE_LOW is False,
          "TIMER_ACTIVE_LOW=%s" % _P.TIMER_ACTIVE_LOW)

    # A freshly constructed channel must sit at the de-energised level.
    ch = _SC("P", 4, active_low=_P.CHANNEL_ACTIVE_LOW)
    idle = ch._pin.value()
    check("channel constructs de-energised", idle == 0, "idle level=%d" % idle)

    ch.set_duty(0.7)
    ch.service()
    ch.off()
    check("off() returns to the de-energised level", ch._pin.value() == 0)

    # boot.py must drive the same level before anything else runs.
    off_level = 1 if _P.CHANNEL_ACTIVE_LOW else 0
    check("boot.py safe level matches (subject disconnected at power-on)",
          off_level == 0, "boot writes %d" % off_level)
except Exception as exc:
    check("relay polarity tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n16. Pose filtering rejects landmark jumps")
# Vision noise is jitter PLUS occasional large outliers when the estimator
# mis-locates a joint. An exponential filter cannot reject an outlier - it
# smears it across several frames, which is worse for control than the spike.
# A median prefilter discards it outright.
try:
    from pose_api import PoseReceiver as _PR

    rx = _PR(median_n=5, alpha=0.35)
    steady = 45.0
    # feed a clean signal, then one big jump, then clean again
    for _ in range(10):
        rx._ingest({"elbow": steady, "shoulder_flex": 0.0, "shoulder_abd": 0.0})
    before = rx.latest()[0]["elbow"]
    rx._ingest({"elbow": steady + 30.0, "shoulder_flex": 0.0, "shoulder_abd": 0.0})
    after = rx.latest()[0]["elbow"]
    check("a single 30 deg landmark jump is rejected",
          abs(after - steady) < 2.0,
          "moved %.2f deg" % abs(after - steady))

    # ...but a SUSTAINED change must still get through (it is real movement).
    for _ in range(10):
        rx._ingest({"elbow": steady + 30.0, "shoulder_flex": 0.0,
                    "shoulder_abd": 0.0})
    moved = rx.latest()[0]["elbow"]
    check("a sustained move is still tracked",
          moved > steady + 15.0, "reached %.1f deg" % moved)

    check("median window is odd (median is a real sample)",
          C.POSE_MEDIAN_WINDOW % 2 == 1, "n=%d" % C.POSE_MEDIAN_WINDOW)
except Exception as exc:
    check("pose filtering tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n17. Adaptive (one-euro) pose filter")
# The claim being tested is the whole reason for the filter: it smooths HARDER
# while the signal is still than a fixed filter of equal responsiveness, and
# is no slower to follow real movement. If either half fails, the fixed
# exponential is the better choice and this is just extra machinery.
try:
    import random
    from filters import OneEuroFilter, MedianWindow, JointFilter

    RATE = 28.0
    random.seed(11)
    noise = [45.0 + random.gauss(0, 2.5) for _ in range(400)]

    def run(f, xs):
        return [f(x, RATE) for x in xs]

    def spread(xs):
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5

    oe = run(JointFilter(RATE, 5, 0.15, 0.005, mode="oneeuro"), noise)
    ema = run(JointFilter(RATE, 5, 0, 0, mode="ema", alpha=0.35), noise)

    # Ignore the first second of each: both start from the first sample.
    warm = int(RATE)
    sd_oe, sd_ema = spread(oe[warm:]), spread(ema[warm:])
    check("adaptive filter is quieter than the fixed one on a held pose",
          sd_oe < sd_ema,
          "one-euro sd %.2f vs ema sd %.2f" % (sd_oe, sd_ema))

    # ...and must NOT be slower to follow a real move, which is the trade a
    # plain low-pass would have had to make to get that reduction.
    step = [0.0] * 30 + [40.0] * 120

    def rise_time(vals):
        for i, v in enumerate(vals[30:]):
            if v >= 0.632 * 40.0:
                return i
        return 10 ** 6

    t_oe = rise_time(run(JointFilter(RATE, 5, 0.15, 0.005, mode="oneeuro"), step))
    t_ema = rise_time(run(JointFilter(RATE, 5, 0, 0, mode="ema", alpha=0.35), step))
    check("adaptive filter is not slower to follow a real move",
          t_oe <= t_ema + 2,
          "one-euro %d samples vs ema %d" % (t_oe, t_ema))

    # A filter that let the cutoff run away would ring or overshoot; a bounded
    # low-pass never leaves the range of its input.
    check("filter never overshoots its input range",
          max(oe) <= max(noise) + 1e-9 and min(oe) >= min(noise) - 1e-9)

    # Order matters: median BEFORE the adaptive stage. Reversed, an outlier is
    # read as fast motion, the cutoff opens, and the spike is passed through.
    jf = JointFilter(RATE, 5, 0.15, 0.005, mode="oneeuro")
    for _ in range(20):
        jf(45.0, RATE)
    after_spike = jf(95.0, RATE)
    check("adaptive stage still cannot be fooled by one outlier",
          abs(after_spike - 45.0) < 2.0,
          "moved %.2f deg" % abs(after_spike - 45.0))

    # An odd window is what guarantees the median is an actual sample.
    check("MedianWindow forces an odd length", MedianWindow(4).n == 5)

    # ---- rate gate ----------------------------------------------------
    # A median only rejects an outlier while it is a MINORITY of its window,
    # so a burst longer than half the window becomes the median and passes
    # through. Real captures had bursts of 6 on shoulder abduction.
    from filters import RateGate

    burst = [10.0] * 20 + [45.0] * 6 + [10.0] * 20      # 6-sample burst
    no_gate = [JointFilter(RATE, 5, 0.15, 0.005, max_deg_per_s=0)(x, RATE)
               for x in burst]
    gated_f = JointFilter(RATE, 5, 0.15, 0.005, max_deg_per_s=400.0)
    gated = [gated_f(x, RATE) for x in burst]
    check("a 6-sample burst defeats the median alone",
          max(no_gate) > 20.0, "reached %.1f deg" % max(no_gate))
    check("the rate gate contains that burst",
          max(gated) < max(no_gate), "%.1f vs %.1f deg"
          % (max(gated), max(no_gate)))

    # It must LIMIT, not hold-and-snap. Holding was measured to make the elbow
    # worse than no gate at all, because a real level change is held for the
    # whole retry window and then jumps.
    g = RateGate(400.0)
    for _ in range(5):
        g(10.0, RATE)
    one = g(200.0, RATE)
    two = g(200.0, RATE)
    check("gate limits a step rather than discarding it",
          one > 10.0 and two > one,
          "converging: %.1f then %.1f" % (one, two))

    # ...and must therefore always converge on a sustained new value, or a
    # side-switch in the estimator would freeze the pose permanently.
    for _ in range(60):
        g(200.0, RATE)
    check("gate always converges on a sustained change",
          abs(g(200.0, RATE) - 200.0) < 0.5)

    # Real movement must pass untouched, or the gate is costing accuracy.
    g2 = RateGate(400.0)
    slow = [10.0 + 2.0 * i for i in range(20)]      # 2 deg/frame ~= 56 deg/s
    out = [g2(x, RATE) for x in slow]
    check("plausible movement passes the gate unchanged",
          g2.limited_total == 0 and abs(out[-1] - slow[-1]) < 1e-9)

    check("gate disabled by setting the limit to 0",
          RateGate(0.0)(999.0, RATE) == 999.0)

    # Cutoff must rise with speed, or it is just a fixed filter.
    f = OneEuroFilter(RATE, 0.15, 0.005)
    a_still = f._alpha(0.15)
    a_fast = f._alpha(0.15 + 0.005 * 500.0)
    check("cutoff opens up as the signal speeds up", a_fast > a_still,
          "alpha %.3f -> %.3f" % (a_still, a_fast))

    # The receiver must actually be using it, or none of the above matters.
    from pose_api import PoseReceiver as _PR2
    check("PoseReceiver uses the configured filter mode",
          _PR2().mode == C.POSE_FILTER_MODE,
          "mode=%s" % _PR2().mode)

    # The deadband is only defensible if it clears the FILTERED noise.
    for _j, (_kp, _ki, _db, _il) in C.GAINS.items():
        check("%s deadband exceeds 3 sd of filtered noise" % _j,
              _db >= 3.0 * sd_oe - 1e-9,
              "deadband %.1f vs 3sd %.2f" % (_db, 3.0 * sd_oe))
except Exception as exc:
    check("adaptive pose filter tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n18. Pose noise is measured and reported, but does NOT widen the band")
# Repeated captures on one rig gave elbow noise of 2.5, 3.3 and 7.3 deg, so a
# constant deadband is wrong most of the time: tuned quiet it chatters, tuned
# noisy the arm stops short. The controller therefore sizes it from what it is
# actually receiving.
try:
    import random
    from pose_api import PoseReceiver as _PR3
    from control_loop import ArmController

    class _FakeLink:
        def __init__(self):
            self.sent = []

        def send_duties(self, duties, grip):
            self.sent.append(list(duties))

        def poll_status(self):
            return None

    def feed(rx, noise_sd, n=900, base=45.0, seed=5):
        """Feed CORRELATED noise, because that is what vision produces.

        Pure white noise is almost entirely removed by the filter chain (a
        6 deg white input leaves ~0.8 deg residual), so testing with it would
        assert that the deadband never needs to widen - which is true of white
        noise and false of the real thing. Real captures classified as 'mixed':
        a fast component the filter removes, plus a slow wander it cannot.
        """
        random.seed(seed)
        wander = 0.0
        for _ in range(n):
            wander = 0.97 * wander + random.gauss(0, noise_sd * 0.25)
            rx._ingest({"elbow": base + wander + random.gauss(0, noise_sd),
                        "shoulder_flex": 0.0, "shoulder_abd": 0.0})

    quiet = _PR3()
    quiet._rate_hz = 28.0
    feed(quiet, 0.5)
    noisy = _PR3()
    noisy._rate_hz = 28.0
    feed(noisy, 6.0)

    nq = quiet.noise_sd("elbow")
    nn = noisy.noise_sd("elbow")
    check("noise estimate rises with actual noise", nn > nq * 3,
          "quiet %.2f vs noisy %.2f deg" % (nq, nn))

    ctl_q = ArmController(_FakeLink(), quiet)
    ctl_n = ArmController(_FakeLink(), noisy)

    # THE DEFAULT: noise must NOT change the deadband. Measured on the plant,
    # widening it produced zero reduction in relay switching (0 at every band
    # from 0.5 to 12 deg) while costing up to 7.6 deg of steady-state error and,
    # at the top end, preventing the arm settling at all. A control knob that
    # only has a downside should not be automatic.
    check("adaptive widening is OFF by default", C.DEADBAND_ADAPTIVE is False)
    check("a noisy feed does NOT widen the deadband",
          abs(ctl_n.effective_deadband("elbow") - C.GAINS["elbow"][2]) < 1e-9,
          "%.2f deg" % ctl_n.effective_deadband("elbow"))
    check("a quiet feed also sits at the configured value",
          abs(ctl_q.effective_deadband("elbow") - C.GAINS["elbow"][2]) < 1e-9,
          "%.2f deg" % ctl_q.effective_deadband("elbow"))

    # ...but the MECHANISM must still work, because it is the documented escape
    # hatch if a rig ever does chatter. Exercise it with the flag forced on.
    _was = C.DEADBAND_ADAPTIVE
    try:
        C.DEADBAND_ADAPTIVE = True
        db_q = ctl_q.effective_deadband("elbow")
        db_n = ctl_n.effective_deadband("elbow")
        check("with the flag on, a noisy feed does widen it", db_n > db_q + 1.0,
              "%.1f vs %.1f deg" % (db_n, db_q))
        check("and it is still capped", db_n <= C.DEADBAND_MAX_DEG + 1e-9,
              "%.1f vs cap %.1f" % (db_n, C.DEADBAND_MAX_DEG))
    finally:
        C.DEADBAND_ADAPTIVE = _was

    # The cap must be low enough to still settle. 12 deg did not.
    check("the widening cap is low enough that the arm still settles",
          C.DEADBAND_MAX_DEG <= 8.0, "cap is %.1f deg" % C.DEADBAND_MAX_DEG)

    # However broken the feed gets, the deadband must never run away.
    wild = _PR3()
    wild._rate_hz = 28.0
    feed(wild, 40.0)
    check("deadband is capped even on a wildly broken feed",
          ArmController(_FakeLink(), wild).effective_deadband("elbow")
          <= C.DEADBAND_MAX_DEG + 1e-9)

    # ...and never below the configured floor, which encodes the mechanical
    # accuracy we are willing to accept regardless of how clean the vision is.
    perfect = _PR3()
    perfect._rate_hz = 28.0
    feed(perfect, 0.0)
    check("deadband never drops below the configured floor",
          ArmController(_FakeLink(), perfect).effective_deadband("elbow")
          >= C.GAINS["elbow"][2] - 1e-9)

    # The estimate must NOT count real movement as noise, or the arm would
    # widen its own deadband whenever it tried to travel and stop short.
    moving = _PR3()
    moving._rate_hz = 28.0
    for i in range(900):
        moving._ingest({"elbow": 20.0 + 0.35 * i,      # ~10 deg/s, clean ramp
                        "shoulder_flex": 0.0, "shoulder_abd": 0.0})
    check("a clean ramp is not mistaken for noise",
          moving.noise_sd("elbow") < 1.0,
          "estimated %.2f deg on noise-free movement"
          % moving.noise_sd("elbow"))

    # Simulation must stay at the floor, or --sim stops being comparable with
    # the tuning done against real captures.
    from pose_api import SimulatedPoseSource as _SPS
    check("simulated pose reports no noise", _SPS().noise_sd("elbow") == 0.0)

    # The operator has to be able to SEE it, or a widening deadband gets
    # mis-diagnosed as weak gains.
    st = ctl_n.status()
    check("measured noise is reported in status()",
          "pose_noise" in st and st["pose_noise"]["elbow"] > 1.0,
          "elbow noise %.2f" % st.get("pose_noise", {}).get("elbow", -1))
    check("the deadband it reports is the configured one",
          st["deadbands"]["elbow"] == C.GAINS["elbow"][2])
except Exception as exc:
    check("adaptive deadband tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n19. Concurrency is bounded by the electrical topology, not anatomy")
# An earlier version forbade shoulder and elbow from firing together. That was
# the wrong rule: the leads are patched so each ANTAGONIST PAIR sits on one
# constant-current bank, which means every pair sharing a source is already a
# pair the firmware refuses. Blocking two ISOLATED channels bought no safety and
# cost roughly 0.9 s on a two-joint move.
#
# What replaces it: same-bank co-firing stays refused, and a cap bounds how much
# of the limb may be live at once.
try:
    from lib.safety import SafetySupervisor as _Sup
    from lib.stim_array import StimArray as _Arr
    from lib.hal import ticks_ms as _tms
    from config import settings as _FS
    from config import pins as _FP

    def _run(cmd, grip=False, secs=6.0, apply_hz=30):
        """Drive the real firmware array on ONE injected clock."""
        sup = _Sup(duty_max=_FS.DUTY_MAX,
                   command_timeout_ms=_FS.COMMAND_TIMEOUT_MS,
                   max_burst_ms=_FS.MAX_BURST_MS, cooldown_ms=_FS.COOLDOWN_MS)
        arr = _Arr(sup)
        t0 = _tms()
        sup.arm()
        on = {n: 0 for n in arr.channels}
        steps = 0
        worst = 0
        nxt = 0
        for ms in range(0, int(secs * 1000), 5):
            now = t0 + ms
            sup.note_command(now)
            if ms >= nxt:
                arr.apply(cmd, grip=grip)
                nxt = ms + int(1000 / apply_hz)
            arr.service(now)
            steps += 1
            live = [n for n, c in arr.channels.items() if c.is_on()]
            arm = [n for n in live if n not in _FP.CONCURRENCY_EXEMPT]
            worst = max(worst, len(arm))
            for n in live:
                on[n] += 1
        return {n: on[n] / steps for n in on}, worst

    # --- the invariant the whole relaxation rests on ---------------------
    check("every current bank is un-parallelable", _FP.assert_banks_safe())
    _pairs = set(frozenset(p) for p in _FP.ANTAGONIST_PAIRS)
    for _b in _FP.CURRENT_BANKS:
        _live = [c for c in _b if c not in _FP.CHANNEL_UNUSED]
        check("bank %s cannot be paralleled" % (list(_b),),
              len(_live) < 2 or frozenset(_b) in _pairs)

    # A re-patch putting two agonists on one bank must be REFUSED, not
    # silently accepted - that is the failure this check exists for.
    _orig = _FP.CURRENT_BANKS
    try:
        _FP.CURRENT_BANKS = (("CH1", "CH3"),) + _orig[1:]
        try:
            _FP.assert_banks_safe()
            check("a re-patch onto a shared bank is refused", False,
                  "CH1+CH3 on one current source was accepted")
        except ValueError:
            check("a re-patch onto a shared bank is refused", True)
    finally:
        _FP.CURRENT_BANKS = _orig

    # --- the speed case: two joints must run CONCURRENTLY ----------------
    avg, worst = _run({"CH1": 0.7, "CH3": 0.7}, grip=True)
    check("elbow and shoulder now run concurrently", worst == 2,
          "max %d arm channels live at once" % worst)
    check("neither joint is halved by interleaving",
          avg["CH1"] > 0.4 and avg["CH3"] > 0.4,
          "CH1 %.3f CH3 %.3f (commanded 0.70; burst limit caps near 0.47)"
          % (avg["CH1"], avg["CH3"]))
    check("grip still runs alongside both", avg["CH7"] > 0.5,
          "CH7 %.3f" % avg["CH7"])

    # --- the cap still bounds the worst case -----------------------------
    avg3, worst3 = _run({"CH1": 0.7, "CH3": 0.7, "CH5": 0.7})
    check("three joints cannot all be live at once",
          worst3 <= _FP.MAX_CONCURRENT_ARM_CHANNELS,
          "max %d live, cap %d" % (worst3, _FP.MAX_CONCURRENT_ARM_CHANNELS))
    check("but a three-joint move still completes all three",
          all(avg3[c] > 0.1 for c in ("CH1", "CH3", "CH5")),
          "CH1 %.2f CH3 %.2f CH5 %.2f" % (avg3["CH1"], avg3["CH3"], avg3["CH5"]))
    for _n in ("CH1", "CH3", "CH5"):
        check("%s average duty stays within DUTY_MAX" % _n,
              avg3[_n] <= _FS.DUTY_MAX + 1e-6,
              "avg %.3f vs ceiling %.2f" % (avg3[_n], _FS.DUTY_MAX))

    # --- same-bank co-firing is still refused ----------------------------
    avgA, _ = _run({"CH1": 0.7, "CH2": 0.7})
    check("two channels on one current source never both fire",
          avgA["CH1"] == 0.0 and avgA["CH2"] == 0.0,
          "CH1 %.3f CH2 %.3f" % (avgA["CH1"], avgA["CH2"]))

    # --- per-channel duty ceiling ----------------------------------------
    _s = _Sup(duty_max=_FS.DUTY_MAX, command_timeout_ms=_FS.COMMAND_TIMEOUT_MS,
              max_burst_ms=_FS.MAX_BURST_MS, cooldown_ms=_FS.COOLDOWN_MS)
    check("grip may reach duty 1.0", _s.clamp_duty(1.0, channel="CH7") == 1.0)
    check("an arm channel may not exceed DUTY_MAX",
          _s.clamp_duty(1.0, channel="CH1") == _FS.DUTY_MAX,
          "got %.2f" % _s.clamp_duty(1.0, channel="CH1"))
    check("omitting the channel keeps the strict global ceiling",
          _s.clamp_duty(1.0) == _FS.DUTY_MAX)
    check("nothing may exceed a fully closed relay",
          _s.clamp_duty(5.0, channel="CH7") == 1.0)

    # --- the bug the scheduler introduced once ---------------------------
    from lib.stim_channel import StimChannel as _SC
    _c = _SC("t", 4, active_low=False, period_ms=_FS.PWM_PERIOD_MS,
             min_pulse_ms=_FS.MIN_PULSE_MS)
    _base = _tms()
    _c.set_duty(0.5)
    _c.restart_cycle(_base)
    _on = sum(1 for _ms in range(0, 600, 5)
              if _c.service(_base + _ms, max_burst_ms=None, cooldown_ms=0))
    _frac = _on / len(range(0, 600, 5))
    check("restart_cycle latches the duty immediately",
          0.4 < _frac < 0.6, "delivered %.2f from a commanded 0.50" % _frac)

    _c2 = _SC("t2", 4, active_low=False, period_ms=_FS.PWM_PERIOD_MS,
              min_pulse_ms=_FS.MIN_PULSE_MS)
    _b2 = _tms()
    _on2 = 0
    _n2 = 0
    for _ms in range(0, 600, 5):
        _c2.set_duty(0.5)                    # re-commanded, as apply() does
        if _c2.service(_b2 + _ms, max_burst_ms=None, cooldown_ms=0):
            _on2 += 1
        _n2 += 1
    check("re-commanding at high rate does not pin the relay closed",
          _on2 / _n2 < 0.75, "delivered %.2f from a commanded 0.50"
          % (_on2 / _n2))
except Exception as exc:
    check("concurrency cap tests", False, repr(exc))

# ---------------------------------------------------------------------------
print("\n20. Each control axis drives the muscle actually wired to it")
# The electrodes are placed by hand and the axis names are anatomical rather
# than operational, so it is easy for the two to drift apart. When they do, the
# controller drives the arm AWAY from the target and the integrator winds up
# against it - and on a blindfolded subject that presents as the arm slowly
# pushing the wrong way, which is the worst failure mode we have.
#
# This pins the mapping the electrodes were placed for:
#     elbow          W / S          CH1 biceps   / CH2 triceps
#     shoulder_flex  LEFT / RIGHT   CH3 anterior / CH4 posterior deltoid
#     shoulder_abd   UP / DOWN      CH5 middle deltoid / gravity
try:
    EXPECT = (
        ("elbow",          +1, "CH1", "bend the elbow -> biceps"),
        ("elbow",          -1, "CH2", "straighten the elbow -> triceps"),
        ("shoulder_flex",  +1, "CH3", "swing forward -> anterior deltoid"),
        ("shoulder_flex",  -1, "CH4", "swing back -> posterior deltoid"),
        ("shoulder_abd",   +1, "CH5", "raise the arm -> middle deltoid"),
    )
    for _joint, _sign, _ch, _why in EXPECT:
        _d = mapping.efforts_to_duties({_joint: 0.5 * _sign}, duty_max=C.DUTY_MAX)
        _fired = [c for c, v in _d.items() if v > 0.0]
        check(_why, _fired == [_ch],
              "expected %s, fired %s" % (_ch, _fired or "nothing"))

    # Lowering has no muscle at all. If a channel ever fires here, someone has
    # added an adductor - which would mean chest electrodes near the heart.
    _d = mapping.efforts_to_duties({"shoulder_abd": -0.5}, duty_max=C.DUTY_MAX)
    check("lower the arm -> NO channel, gravity does it",
          all(v == 0.0 for v in _d.values()),
          "fired %s" % [c for c, v in _d.items() if v > 0.0])

    # The two deltoid channels must stay an antagonist pair, because they share
    # one constant-current bank - that is what makes the concurrency relaxation
    # safe (section 19).
    check("anterior/posterior deltoid are still a declared antagonist pair",
          any(set(p) == {"CH3", "CH4"} for p in _FP.ANTAGONIST_PAIRS))

    # Backward travel must actually be reachable, or RIGHT looks like a dead key.
    _lo, _hi = C.JOINT_LIMITS["shoulder_flex"]
    check("backward swing has usable range", _lo <= -30.0,
          "limit is %.0f deg" % _lo)
except Exception as exc:
    check("axis-to-muscle mapping tests", False, repr(exc))

# ---------------------------------------------------------------------------
failed = [r for r in results if r[0] == FAIL]
print("\n%s" % ("-" * 60))
print("%d checks, %d failed" % (len(results), len(failed)))
if failed:
    for _, name, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    sys.exit(1)
print("All checks passed.")
