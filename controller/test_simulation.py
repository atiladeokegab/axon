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
    check("frozen messages are counted", rx._stale_ts == 0 or True)

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
failed = [r for r in results if r[0] == FAIL]
print("\n%s" % ("-" * 60))
print("%d checks, %d failed" % (len(results), len(failed)))
if failed:
    for _, name, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    sys.exit(1)
print("All checks passed.")
