#!/usr/bin/env python3
"""Per-subject calibration sweep (docs/TESTING.md stage 4).

Ramps one channel's duty in steps and records the joint angle reached at each
step. That gives you the recruitment curve, which tells you two things:
  * the activation threshold (below which nothing happens), and
  * whether this muscle has any real authority on this subject.

Angles come from the pose API if it is running; otherwise you type in what you
observe. Manual mode is fine and is often what you want on the bench.

    python tools/calibrate.py --channel 1 --joint elbow
    python tools/calibrate.py --channel 1 --joint elbow --manual
    python tools/calibrate.py --channel 7 --joint grip --manual --max-duty 0.7

Output is written to calibration_<channel>.json.

SAFETY: this drives real stimulation. Complete docs/SAFETY.md screening first,
start with a low hand-set intensity, and keep the subject's kill switch in
their hand. Ctrl-C sends an immediate kill.
"""

import argparse
import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "controller"))

CHANNELS = 8


try:
    from settings import CONTROL_TOKEN as TOKEN
except Exception:
    TOKEN = None


def send(sock, host, port, duty, seq, **extra):
    msg = {"duty": duty, "seq": seq}
    if TOKEN:
        msg["tok"] = TOKEN
    msg.update(extra)
    sock.sendto(json.dumps(msg).encode(), (host, port))


def hold(sock, host, port, duty, seq_start, seconds):
    """Hold a duty for N seconds, re-sending to keep the watchdog fed."""
    seq = seq_start
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        seq += 1
        send(sock, host, port, duty, seq)
        time.sleep(0.1)
    return seq


def main():
    ap = argparse.ArgumentParser(description="FES calibration sweep")
    ap.add_argument("--channel", type=int, required=True, help="1-8")
    ap.add_argument("--joint", default="elbow",
                    help="elbow | shoulder_flex | shoulder_abd | grip")
    ap.add_argument("--host", help="board IP (default: auto-discover)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--steps", type=int, default=7)
    ap.add_argument("--max-duty", type=float, default=0.70)
    ap.add_argument("--dwell", type=float, default=3.0,
                    help="seconds to hold each step before reading")
    ap.add_argument("--rest", type=float, default=3.0,
                    help="seconds of rest between steps")
    ap.add_argument("--manual", action="store_true",
                    help="type observed angles instead of reading the pose API")
    args = ap.parse_args()

    if not 1 <= args.channel <= CHANNELS:
        sys.exit("--channel must be 1-8")

    host = args.host
    if not host:
        try:
            from link import discover
            host = discover(port=args.port)
        except ImportError:
            pass
    if not host:
        sys.exit("Board not found. Pass --host <ip>.")

    # Optional live pose feed
    pose = None
    if not args.manual:
        try:
            from pose_api import PoseReceiver
            pose = PoseReceiver().start()
            print("Waiting for pose data ...")
            for _ in range(50):
                if pose.latest()[1]:
                    break
                time.sleep(0.1)
            if not pose.latest()[1]:
                print("No pose data - falling back to manual entry.")
                pose = None
        except Exception as exc:
            print("Pose API unavailable (%s) - manual entry." % exc)
            pose = None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    idx = args.channel - 1
    seq = 0

    print("\nCalibrating CH%d against '%s' on %s" % (args.channel, args.joint, host))
    print("Ctrl-C at any time sends an immediate kill.\n")
    input("Press Enter when the subject is ready and the kill switch is in hand ...")

    seq += 1
    send(sock, host, args.port, [0.0] * CHANNELS, seq, arm=True)
    time.sleep(0.3)

    results = []
    try:
        for step in range(args.steps + 1):
            duty = args.max_duty * step / args.steps
            vec = [0.0] * CHANNELS
            vec[idx] = duty

            print("  duty %.2f  holding %.1fs ..." % (duty, args.dwell))
            seq = hold(sock, host, args.port, vec, seq, args.dwell)

            if pose is not None:
                joints, fresh = pose.latest()
                angle = joints.get(args.joint) if (joints and fresh) else None
                if angle is None:
                    print("    (pose stale - enter manually)")
                    angle = float(input("    observed angle (deg): "))
            else:
                prompt = ("    observed angle (deg)" if args.joint != "grip"
                          else "    grip 0=open 1=partial 2=full")
                angle = float(input(prompt + ": "))

            results.append({"duty": round(duty, 3), "angle": angle})
            print("    -> %.1f" % angle)

            # Rest between steps so fatigue does not contaminate the curve.
            seq = hold(sock, host, args.port, [0.0] * CHANNELS, seq, args.rest)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        for _ in range(5):
            seq += 1
            send(sock, host, args.port, [0.0] * CHANNELS, seq, kill=True)
        if pose is not None:
            pose.stop()
        print("Kill sent - all relays open.")

    out = os.path.join(ROOT, "calibration_ch%d.json" % args.channel)
    with open(out, "w") as fh:
        json.dump({"channel": args.channel, "joint": args.joint,
                   "max_duty": args.max_duty, "curve": results}, fh, indent=2)

    print("\nRecruitment curve (CH%d / %s)" % (args.channel, args.joint))
    print("  duty   angle")
    for r in results:
        print("  %.2f   %6.1f" % (r["duty"], r["angle"]))

    moved = [r for r in results if abs(r["angle"] - results[0]["angle"]) > 5]
    if moved:
        print("\n  activation threshold ~ duty %.2f" % moved[0]["duty"])
        print("  range achieved       ~ %.1f deg"
              % abs(results[-1]["angle"] - results[0]["angle"]))
    else:
        print("\n  WARNING: no movement detected. Check electrode placement,")
        print("  raise the hand-set intensity level, or try another muscle.")
    print("\nSaved %s" % out)


if __name__ == "__main__":
    main()
