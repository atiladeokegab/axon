#!/usr/bin/env python3
"""Measure the pose estimator's noise, then recommend filter and deadband.

    python tools/pose_noise.py                     # 20 s capture, then analyse
    python tools/pose_noise.py --seconds 30
    python tools/pose_noise.py --replay            # re-analyse, no human needed

Have the subject hold ONE still posture for the whole run. Everything that
moves during it is treated as noise, so any real movement invalidates the
numbers - which is why this now reports whether what it saw looks like noise
or like movement.

WHY THIS EXISTS: the controller's deadband must be LARGER than the measurement
noise, or it chases jitter and the relays chatter. Filtering reduces noise but
costs lag, and our loop already has 150-300 ms of it. Both settings should come
from measurement, not guesswork - this is the measurement.

Every capture is SAVED, so filter settings can be re-evaluated with --replay
instead of asking someone to sit still again.

It listens on the SAME UDP port the controller uses, and only one process can
bind it. So do not run this alongside run.py - use the pose-only mode:

    terminal 1:  py tools/launch.py --pose-only
    terminal 2:  py tools/pose_noise.py
"""

import argparse
import json
import math
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "controller"))

JOINTS = ("elbow", "shoulder_flex", "shoulder_abd")
DEFAULT_CAPTURE = os.path.join(ROOT, "pose_capture.json")


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def stats(xs):
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": 0.0, "sd": 0.0, "p2p": 0.0}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return {"n": n, "mean": mean, "sd": math.sqrt(var),
            "p2p": max(xs) - min(xs)}


def sd(xs):
    return stats(xs)["sd"]


def whiteness(xs):
    """How much of the variation is frame-to-frame (filterable) vs slow drift.

    For pure white noise consecutive samples are independent, so the standard
    deviation of the DIFFERENCES is sqrt(2) times the signal's own. For a slow
    wander - or a subject who actually moved - consecutive samples are nearly
    equal, so the differences are tiny by comparison.

    ~1.0 means white noise; well below 1.0 means drift. This is THE diagnostic,
    because filtering can only remove the white part.
    """
    if len(xs) < 3:
        return 0.0
    diffs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    s = sd(xs)
    if s < 1e-9:
        return 0.0
    return sd(diffs) / (math.sqrt(2.0) * s)


def median_then_ema(xs, median_n, alpha):
    """Exactly the filter chain in controller/pose_api.py _ingest().

    Modelling only the exponential stage - as this tool used to - understates
    the real filter whenever there are outliers, because the median stage is
    the one that removes those.
    """
    buf, y, out = [], None, []
    for x in xs:
        buf.append(x)
        if len(buf) > median_n:
            buf.pop(0)
        med = sorted(buf)[len(buf) // 2]
        y = med if y is None else alpha * med + (1 - alpha) * y
        out.append(y)
    return out


def median_then_oneeuro(xs, rate, median_n, mincutoff, beta):
    """The adaptive chain, matching controller/filters.py JointFilter."""
    from filters import JointFilter
    f = JointFilter(rate, median_n, mincutoff, beta, mode="oneeuro")
    return [f(x, rate) for x in xs]


def step_lag(filter_fn, rate, height=40.0):
    """Measure lag as the time to reach 63% of a step, in ms.

    Measured rather than derived, because the whole point of the adaptive
    filter is that its lag is NOT a fixed function of its constants - it
    depends on how fast the signal is moving. A formula would flatter it.
    """
    if rate <= 0:
        return 0.0
    n0 = int(rate)
    sig = [0.0] * n0 + [height] * int(rate * 5)
    out = filter_fn(sig)
    for i in range(n0, len(out)):
        if out[i] >= 0.632 * height:
            return (i - n0) / rate * 1000.0
    return 9999.0


def outlier_structure(xs):
    """Are the outliers isolated spikes, or sustained bursts?

    This distinction decides the fix, and nothing else reveals it. A median
    window of n rejects an outlier only while it is a MINORITY of that window,
    so isolated spikes are already handled and a burst longer than n/2 is not -
    it becomes the median and passes through as if it were signal.

    Returns (fraction, longest_burst).
    """
    if len(xs) < 5:
        return 0.0, 0
    med = sorted(xs)[len(xs) // 2]
    devs = sorted(abs(x - med) for x in xs)
    mad = devs[len(devs) // 2]
    robust_sd = 1.4826 * mad
    if robust_sd < 1e-9:
        return 0.0, 0
    flags = [abs(x - med) > 3 * robust_sd for x in xs]
    longest = run = 0
    for f in flags:
        run = run + 1 if f else 0
        longest = max(longest, run)
    return sum(flags) / float(len(xs)), longest


def max_velocity(xs, rate):
    """Fastest implied joint velocity, deg/s. A held posture should be slow."""
    if len(xs) < 2 or rate <= 0:
        return 0.0
    return max(abs(xs[i + 1] - xs[i]) for i in range(len(xs) - 1)) * rate


def outlier_fraction(xs):
    """Share of samples more than 3 robust-sd from the median.

    Uses the median absolute deviation, not the standard deviation, because
    outliers inflate the standard deviation and so hide themselves from it.
    """
    if len(xs) < 5:
        return 0.0
    med = sorted(xs)[len(xs) // 2]
    devs = sorted(abs(x - med) for x in xs)
    mad = devs[len(devs) // 2]
    robust_sd = 1.4826 * mad
    if robust_sd < 1e-9:
        return 0.0
    return sum(1 for x in xs if abs(x - med) > 3 * robust_sd) / float(len(xs))


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def capture(port, seconds):
    from kinematics import joints_from_pose

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        sys.exit(
            "Cannot bind UDP %d (%s).\n\n"
            "Something else is already listening - almost always run.py, which\n"
            "launch.py starts for you. Only one process can receive the poses.\n\n"
            "Use the pose-only mode instead:\n"
            "    terminal 1:  py tools/launch.py --pose-only\n"
            "    terminal 2:  py tools/pose_noise.py\n\n"
            "If nothing should be running, clear leftovers:\n"
            "    py tools/stop.py" % (port, exc))
    sock.settimeout(1.0)

    print("Listening on UDP %d for %.0f s." % (port, seconds))
    print("Have the subject HOLD ONE POSTURE STILL for the whole run.\n")

    raw = {j: [] for j in JOINTS}
    # Keep the LANDMARKS too, not just the angles derived from them. Angles are
    # a nonlinear function of nine coordinates, so a noisy angle cannot tell
    # you which coordinate was responsible - and MediaPipe's depth axis is far
    # noisier than its image-plane axes. Storing the inputs makes that
    # answerable offline, from a capture already taken.
    marks = {"shoulder": [], "elbow": [], "wrist": []}
    t_end = time.monotonic() + seconds
    gaps, last_rx, n_bad = [], None, 0

    while time.monotonic() < t_end:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        now = time.monotonic()
        if last_rx is not None:
            gaps.append(now - last_rx)
        last_rx = now
        try:
            m = json.loads(data.decode())
            if isinstance(m.get("elbow"), (int, float)):
                ang = {j: float(m.get(j, 0.0)) for j in JOINTS}
                lm = None
            else:
                lm = {k: tuple(map(float, m[k]))
                      for k in ("shoulder", "elbow", "wrist")}
                ang = joints_from_pose(lm["shoulder"], lm["elbow"], lm["wrist"])
        except Exception:
            n_bad += 1
            continue
        for j in JOINTS:
            raw[j].append(ang[j])
        if lm is not None:
            for k in marks:
                marks[k].append(list(lm[k]))

    sock.close()

    if not raw["elbow"]:
        sys.exit("No pose data received. Is the pose service running and the "
                 "arm visible? Check the 3D twin page.")

    rate = (len(gaps) / sum(gaps)) if gaps else 0.0
    cap = {"rate_hz": rate, "malformed": n_bad, "samples": raw}
    if marks["shoulder"]:
        cap["landmarks"] = marks
    return cap


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def analyse_landmarks(cap):
    """Which coordinate is actually noisy? Answers what angles cannot.

    A joint angle is a nonlinear function of nine coordinates, so a noisy angle
    tells you nothing about WHERE the noise entered. MediaPipe's depth estimate
    is substantially worse than its image-plane coordinates, and the elbow
    angle depends on depth much more strongly than shoulder abduction does -
    but that is a hypothesis until the per-axis numbers are on the table.
    """
    marks = cap.get("landmarks")
    if not marks or not marks.get("shoulder"):
        print("\n(no landmarks in this capture - re-record to get the")
        print(" per-axis breakdown; older captures stored angles only)")
        return

    # Frame is the one kinematics.py documents: +X forward, +Y left, +Z up.
    # With a front-on camera the subject's FORWARD axis points at the lens, so
    # X is the camera's depth axis - the one MediaPipe estimates worst.
    print("\nLANDMARK NOISE, per axis (metres, sd while holding still):")
    print("  landmark      X = DEPTH    Y = sideways   Z = up      worst")
    axis_sd = {}
    for name in ("shoulder", "elbow", "wrist"):
        cols = list(zip(*marks[name]))
        sds = [sd(list(c)) for c in cols]
        axis_sd[name] = sds
        worst = "XYZ"[max(range(3), key=lambda i: sds[i])]
        print("  %-12s %10.4f %12.4f %12.4f      %s"
              % (name, sds[0], sds[1], sds[2], worst))

    # axon-main's control frame is +X forward, +Y left, +Z up (POSE_API.md),
    # so the camera's depth axis is X. Report it in those terms rather than
    # MediaPipe's, since that is what the capture contains.
    depth = sum(axis_sd[n][0] for n in axis_sd) / 3.0
    plane = sum(axis_sd[n][1] + axis_sd[n][2] for n in axis_sd) / 6.0
    print("\n  mean depth-axis sd  : %.4f m" % depth)
    print("  mean in-plane sd    : %.4f m" % plane)
    if plane > 1e-9:
        ratio = depth / plane
        print("  depth is %.1fx the in-plane noise" % ratio)
        if ratio > 1.5:
            print("\n  >> DEPTH IS THE DOMINANT ERROR. This is the expected")
            print("     MediaPipe weakness, and it hits the elbow angle hardest")
            print("     because a front-on camera makes elbow flexion a mostly")
            print("     DEPTH-WARDS motion. Turning the subject or camera ~45")
            print("     deg converts that into in-plane motion, which is")
            print("     measured several times better. No filter competes with")
            print("     that; it is a free accuracy gain.")
        else:
            print("\n  Depth is NOT dominant here, so the camera-angle fix will")
            print("  not help much. Look at lighting, motion blur, distance,")
            print("  and clothing contrast instead.")


def analyse(cap, median_n, alpha, mode="oneeuro", mincut=0.25, beta=0.005):
    raw = cap["samples"]
    rate = cap.get("rate_hz", 0.0)

    def apply_current(xs):
        if mode == "oneeuro":
            return median_then_oneeuro(xs, rate, median_n, mincut, beta)
        return median_then_ema(xs, median_n, alpha)

    current_label = ("median-%d + 1-euro %.2f/%.3f" % (median_n, mincut, beta)
                     if mode == "oneeuro"
                     else "median-%d + EMA %.2f" % (median_n, alpha))

    print("Received %d samples at %.1f Hz  (%d malformed)\n"
          % (len(raw["elbow"]), rate, cap.get("malformed", 0)))

    # ---- 1. what the raw signal looks like -------------------------------
    print("RAW, while holding still:")
    print("  joint            mean      sd    p2p  outlr burst  peak    kind")
    character = {}
    worst_burst = 0
    worst_vel = 0.0
    for j in JOINTS:
        s = stats(raw[j])
        w = whiteness(raw[j])
        out_frac, burst = outlier_structure(raw[j])
        vel = max_velocity(raw[j], rate)
        worst_burst = max(worst_burst, burst)
        worst_vel = max(worst_vel, vel)
        if w > 0.7:
            kind = "white noise"
        elif w > 0.35:
            kind = "mixed"
        else:
            kind = "DRIFT/MOVE"
        character[j] = (w, kind, burst, vel)
        print("  %-14s %7.2f  %6.2f %5.1f  %4.1f%% %4d %6.0f/s  %s"
              % (j, s["mean"], s["sd"], s["p2p"], out_frac * 100.0, burst,
                 vel, kind))

    print("\n  kind : is the variation frame-to-frame, or slow? Filtering can")
    print("         only remove the frame-to-frame part.")
    print("  burst: longest run of CONSECUTIVE outliers. A median-%d rejects a"
          % median_n)
    print("         burst only up to %d long; beyond that the outliers BECOME"
          % (median_n // 2))
    print("         the median and pass through untouched.")
    print("  peak : fastest implied joint velocity. A held posture should be")
    print("         near zero; anything past ~400 deg/s is not a real limb.")

    if worst_burst > median_n // 2:
        print("\n  >> BURSTS LONGER THAN THE MEDIAN CAN REJECT (%d > %d)."
              % (worst_burst, median_n // 2))
        print("     These are tracking dropouts - the estimator briefly")
        print("     mis-locating a landmark - not noise. More filtering will")
        print("     not remove them. The rate gate (POSE_MAX_RATE_DEG_S) caps")
        print("     how far they can pull the value; the real fix is to stop")
        print("     the estimator emitting them:")
        print("       py tools\\launch.py --min-visibility 0.7")
        print("     which makes axon-main drop low-confidence frames rather")
        print("     than guess. A dropped frame ages out and stops")
        print("     stimulation; a confidently wrong one gets acted upon.")

    # ---- 1b. where the noise entered ------------------------------------
    analyse_landmarks(cap)

    # ---- 2. what OUR filter chain actually achieves -----------------------
    print("\nAFTER the real filter chain (%s):" % current_label)
    print("  joint            raw sd -> filtered sd    reduction")
    filt_sd = {}
    for j in JOINTS:
        f = apply_current(raw[j])
        filt_sd[j] = sd(f)
        raw_sd = sd(raw[j])
        red = (1 - filt_sd[j] / raw_sd) * 100 if raw_sd > 1e-9 else 0.0
        print("  %-14s %7.2f -> %7.2f          %4.0f%%"
              % (j, raw_sd, filt_sd[j], red))
    print("  costs %.0f ms of lag on a step"
          % step_lag(apply_current, rate))

    # ---- 3. filter options, on the WORST joint ---------------------------
    # Lag is MEASURED from each filter's step response rather than derived,
    # so the fixed and adaptive families are compared on the same footing -
    # the whole point of the adaptive one is that its lag is not a constant.
    worst = max(JOINTS, key=lambda j: sd(raw[j]))
    print("\nFilter options for the worst joint (%s)." % worst)
    print("Lag is measured to 63% of a 40 deg step.\n")
    print("  filter                          sd     lag     deadband")

    rows = []
    rows.append(("raw, no filtering", sd(raw[worst]),
                 0.0, "current" if median_n <= 1 else ""))
    for mn, a in ((median_n, alpha), (median_n, 0.20), (9, 0.20), (median_n, 0.10)):
        lbl = "median-%d + EMA %.2f" % (mn, a)
        cur = "<-- CURRENT" if (mn == median_n and abs(a - alpha) < 1e-9
                                and mode == "ema") else ""
        rows.append((lbl, sd(median_then_ema(raw[worst], mn, a)),
                     step_lag(lambda s, mn=mn, a=a: median_then_ema(s, mn, a), rate),
                     cur))
    for mc, b in ((0.25, 0.000), (0.25, 0.005), (0.25, 0.010),
                  (0.15, 0.005), (0.40, 0.010)):
        lbl = "median-%d + 1-euro %.2f/%.3f" % (median_n, mc, b)
        cur = "<-- CURRENT" if (mode == "oneeuro" and abs(mc - mincut) < 1e-9
                                and abs(b - beta) < 1e-9) else ""
        rows.append((lbl, sd(median_then_oneeuro(raw[worst], rate, median_n, mc, b)),
                     step_lag(lambda s, mc=mc, b=b:
                              median_then_oneeuro(s, rate, median_n, mc, b), rate),
                     cur))

    for lbl, s, lg, note in rows:
        if not note and lg > 400:
            note = "too laggy for our loop"
        print("  %-28s %5.2f  %5.0f ms    %4.1f   %s"
              % (lbl, s, lg, max(1.5, round(3 * s * 2) / 2.0), note))

    # ---- 4. recommendation, per joint ------------------------------------
    print("\n" + "-" * 64)
    print("RECOMMENDATION")
    print("  Deadband must exceed ~3 sd of the FILTERED signal, per joint:\n")
    print("  joint            filtered sd   deadband needed   current")
    trouble = []
    for j in JOINTS:
        need = max(1.5, round(3.0 * filt_sd[j] * 2) / 2.0)
        flag = "  <-- too small" if need > 3.0 else ""
        if need > 3.0:
            trouble.append((j, need))
        print("  %-14s %10.2f   %13.1f     3.0%s" % (j, filt_sd[j], need, flag))

    if not trouble:
        print("\n  The current 3.0 deg deadband clears this noise on every")
        print("  joint. Nothing to change.")
        return

    print("\n  A deadband is DEAD TRAVEL: the controller ignores errors smaller")
    print("  than it, so the arm stops that far short of every target. A 15 deg")
    print("  elbow deadband is not a usable demo. Treat a large recommended")
    print("  deadband as a signal to FIX THE MEASUREMENT, not a number to")
    print("  paste into settings.py.\n")

    for j, need in trouble:
        w, kind, burst, vel = character[j]
        print("  %s (%s):" % (j, kind))
        if kind == "white noise":
            print("    Frame-to-frame noise, so filtering genuinely helps.")
            print("    Take a longer median and lower alpha from the table")
            print("    above, then --replay to see the lag you paid for.")
        elif kind == "DRIFT/MOVE":
            print("    NOT filterable. Either the subject moved during the")
            print("    capture (re-run and hold properly still) or the")
            print("    estimator is drifting. No filter fixes either one.")
        else:
            print("    Part filterable, part not. Filter what you can, then")
            print("    re-measure; what remains is a measurement problem.")
        print("")

    if worst == "elbow":
        print("  The worst joint is the ELBOW - suspect GEOMETRY before")
        print("  filtering. Elbow angle depends on the WRIST landmark, and with")
        print("  a front-on camera elbow flexion swings the forearm toward the")
        print("  lens: the depth axis, where the estimator is weakest. That")
        print("  shoulder abduction is far quieter points the same way, since")
        print("  abduction is a sideways, in-plane motion. Turning the subject")
        print("  or camera ~45 deg puts elbow motion back in the image plane,")
        print("  and can beat any amount of filtering. See docs/CONTROL.md.\n")

    print("  Edit GAINS deadband / POSE_FILTER_ALPHA / POSE_MEDIAN_WINDOW in")
    print("  controller/settings.py, then re-check WITHOUT another capture:")
    print("    py tools/pose_noise.py --replay --median 9 --alpha 0.2")


def main():
    ap = argparse.ArgumentParser(description="Measure pose noise")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--replay", nargs="?", const=DEFAULT_CAPTURE,
                    help="re-analyse a saved capture instead of recording")
    ap.add_argument("--save", default=None,
                    help="where to write the capture")
    ap.add_argument("--label",
                    help="tag this capture, e.g. --label camera45. Saves to "
                         "pose_capture_<label>.json so setups can be compared "
                         "instead of overwritten.")
    ap.add_argument("--median", type=int, help="override POSE_MEDIAN_WINDOW")
    ap.add_argument("--alpha", type=float,
                    help="override POSE_FILTER_ALPHA (forces the old EMA mode)")
    ap.add_argument("--mincutoff", type=float,
                    help="override POSE_ONEEURO_MINCUTOFF")
    ap.add_argument("--beta", type=float, help="override POSE_ONEEURO_BETA")
    args = ap.parse_args()

    median_n, alpha = 5, 0.35
    mode, mincut, beta = "oneeuro", 0.25, 0.005
    try:
        import settings as C
        median_n = getattr(C, "POSE_MEDIAN_WINDOW", 5)
        alpha = getattr(C, "POSE_FILTER_ALPHA", 0.35)
        mode = getattr(C, "POSE_FILTER_MODE", "oneeuro")
        mincut = getattr(C, "POSE_ONEEURO_MINCUTOFF", 0.25)
        beta = getattr(C, "POSE_ONEEURO_BETA", 0.005)
    except Exception:
        pass
    if args.median:
        median_n = args.median
    if args.alpha:
        alpha = args.alpha
        mode = "ema"
    if args.mincutoff is not None:
        mincut = args.mincutoff
        mode = "oneeuro"
    if args.beta is not None:
        beta = args.beta
        mode = "oneeuro"

    save_to = args.save or (
        os.path.join(ROOT, "pose_capture_%s.json" % args.label) if args.label
        else DEFAULT_CAPTURE)

    if args.replay:
        if not os.path.exists(args.replay):
            sys.exit("No capture at %s - run without --replay first."
                     % args.replay)
        with open(args.replay) as fh:
            cap = json.load(fh)
        print("Replaying %s\n" % args.replay)
    else:
        cap = capture(args.port, args.seconds)
        try:
            with open(save_to, "w") as fh:
                json.dump(cap, fh)
            print("Capture saved to %s" % save_to)
            print("Re-analyse with different settings, no human needed:")
            print("  py tools/pose_noise.py --replay --median 9 --alpha 0.2\n")
        except Exception as exc:
            print("(could not save capture: %s)\n" % exc)

    analyse(cap, median_n, alpha, mode, mincut, beta)


if __name__ == "__main__":
    sys.exit(main())
