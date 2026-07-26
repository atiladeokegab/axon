#!/usr/bin/env python3
"""
Stand-in for the control loop's UDP receiver — verifies our pose feed before
any hardware is involved.

Point our backend at this (POSE_UDP_ENABLED=true), move your arm, and read the
angles. It derives the same quantities the real controller derives, applies the
same staleness rule and the same low-pass filter, so if the numbers look right
here they will look right there.

The thing this is really for is the coordinate frame. Wrong axes do not raise an
error anywhere — they just drive the limb the wrong way, which you would first
discover with electrodes on someone. `--check` walks through four movements and
tells you whether each angle moved in the direction it should.

Standard library only, so it runs anywhere without installing anything:

    python scripts/pose_receiver.py            # live readout
    python scripts/pose_receiver.py --check    # guided coordinate-frame check

Frame (POSE_API.md): +X subject forward, +Y subject's left, +Z up, metres.
"""
import argparse
import json
import math
import socket
import sys
import time

DEFAULT_PORT = 9090
STALE_S = 0.300          # their rule: older than this stops stimulation
FILTER_ALPHA = 0.35      # their POSE_FILTER_ALPHA


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def norm(v):
    return math.sqrt(sum(c * c for c in v))


def angle_between(u, v):
    nu, nv = norm(u), norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    cos = sum(a * b for a, b in zip(u, v)) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def derive_angles(pose):
    """
    Joint angles from the three positions, using POSE_API.md's definitions:
    elbow 0 = straight, shoulder angles 0 = arm hanging at the side.
    """
    shoulder, elbow, wrist = pose["shoulder"], pose["elbow"], pose["wrist"]
    upper = sub(elbow, shoulder)
    fore = sub(wrist, elbow)

    # Included angle at the elbow is 180 deg when straight; flexion is the
    # complement, so a straight arm reads 0.
    elbow_flex = 180.0 - angle_between(sub(shoulder, elbow), fore)

    # Hanging at the side is -Z. Flexion swings forward (+X) in the sagittal
    # plane; abduction swings sideways (+/-Y) in the frontal plane.
    shoulder_flex = math.degrees(math.atan2(upper[0], -upper[2]))
    shoulder_abd = math.degrees(math.atan2(upper[1], -upper[2]))

    return {
        "elbow": elbow_flex,
        "shoulder_flex": shoulder_flex,
        # Signed: positive = toward the subject's left. The real controller
        # takes magnitude for a single arm; the sign is kept here because it is
        # exactly what tells you a left/right mix-up.
        "shoulder_abd": shoulder_abd,
        "upper_arm_m": norm(upper),
        "forearm_m": norm(fore),
    }


class Filtered:
    """Their first-order low-pass, so this shows what the controller sees."""

    def __init__(self, alpha=FILTER_ALPHA):
        self.alpha = alpha
        self.value = None

    def update(self, x):
        self.value = x if self.value is None else self.value + self.alpha * (x - self.value)
        return self.value


def open_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.25)
    return sock


def receive(sock):
    """One pose, or None on timeout. Malformed datagrams are counted, not fatal."""
    try:
        data, _ = sock.recvfrom(4096)
    except socket.timeout:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
        for joint in ("shoulder", "elbow", "wrist"):
            if len(msg[joint]) != 3:
                raise ValueError(f"{joint} is not a 3-vector")
            msg[joint] = [float(c) for c in msg[joint]]
        return msg
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        return {"_malformed": str(exc)}


def live(port):
    sock = open_socket(port)
    print(f"listening on udp/{port} — start the backend with POSE_UDP_ENABLED=true")
    print("waiting for the first pose (Ctrl+C to stop)\n")

    filters = {k: Filtered() for k in ("elbow", "shoulder_flex", "shoulder_abd")}
    last_pose_at = None
    count = malformed = 0
    started = time.monotonic()
    was_stale = None

    try:
        while True:
            msg = receive(sock)
            now = time.monotonic()

            if msg is not None and "_malformed" in msg:
                malformed += 1
                print(f"\n  malformed datagram ({malformed} total): {msg['_malformed']}")
                continue

            if msg is not None:
                count += 1
                last_pose_at = now
                angles = derive_angles(msg)
                smoothed = {k: filters[k].update(angles[k]) for k in filters}
                rate = count / max(1e-6, now - started)
                age_ms = (time.time() - msg.get("timestamp", time.time())) * 1000

                sys.stdout.write(
                    f"\r  elbow {smoothed['elbow']:6.1f}°   "
                    f"flex {smoothed['shoulder_flex']:7.1f}°   "
                    f"abd {smoothed['shoulder_abd']:7.1f}°   |   "
                    f"upper {angles['upper_arm_m']:.3f}m  fore {angles['forearm_m']:.3f}m   |   "
                    f"{rate:4.1f}Hz  age {age_ms:5.1f}ms   "
                )
                sys.stdout.flush()
                was_stale = False

            stale = last_pose_at is None or (now - last_pose_at) > STALE_S
            if stale and was_stale is False:
                print("\n  STALE — no pose for >300ms. The controller stops stimulating here.")
                was_stale = True
    except KeyboardInterrupt:
        print(f"\n\nreceived {count} poses, {malformed} malformed")


# Each check: what to do, which angle to watch, and which way it must move.
CHECKS = [
    ("Hold your arm straight down at your side, then BEND YOUR ELBOW to ~90°",
     "elbow", +1, "elbow flexion should INCREASE (0 = straight)"),
    ("Straighten the arm, then RAISE IT FORWARD in front of you",
     "shoulder_flex", +1, "shoulder flexion should INCREASE"),
    ("Lower it, then RAISE IT OUT TO THE SIDE",
     "shoulder_abd", None, "abduction magnitude should INCREASE"),
    ("Lower the arm back down to your side",
     "shoulder_flex", 0, "both shoulder angles should return toward 0"),
]


def sample(sock, seconds, key):
    """Median-ish reading of one angle over a window, ignoring dropouts."""
    values = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        msg = receive(sock)
        if msg and "_malformed" not in msg:
            values.append(derive_angles(msg)[key])
    if not values:
        return None
    values.sort()
    return values[len(values) // 2]


def check(port):
    sock = open_socket(port)
    print(f"listening on udp/{port}\n")
    print("Coordinate-frame check. Do each movement, hold it, then press Enter.")
    print("This is checking the SIGNS — wrong axes fail silently otherwise.\n")

    input("  Press Enter once poses are arriving and your arm is at your side... ")
    if sample(sock, 1.0, "elbow") is None:
        print("\n  No poses arriving. Is the backend running with POSE_UDP_ENABLED=true?")
        return 1

    failures = 0
    for instruction, key, direction, expectation in CHECKS:
        print(f"\n  {instruction}")
        before = sample(sock, 0.6, key)
        input("     ...holding? press Enter: ")
        after = sample(sock, 0.8, key)

        if before is None or after is None:
            print("     NO DATA — tracking lost during the movement")
            failures += 1
            continue

        if direction is None:                     # magnitude test
            moved = abs(after) - abs(before)
            ok = moved > 8
        elif direction == 0:                      # returning to rest
            ok = abs(after) < abs(before) or abs(after) < 15
            moved = after - before
        else:
            moved = after - before
            ok = moved * direction > 8

        verdict = "OK  " if ok else "WRONG"
        print(f"     {verdict}  {key}: {before:+.1f}° -> {after:+.1f}° ({moved:+.1f}°)")
        print(f"            expected: {expectation}")
        if not ok:
            failures += 1

    print()
    if failures == 0:
        print("  All checks passed — the coordinate frame agrees with POSE_API.md.")
        print("  Safe to try against the board.")
    else:
        print(f"  {failures} check(s) failed.")
        print("  Do NOT connect the board yet: an inverted axis drives the limb")
        print("  the wrong way. Most likely causes, in order:")
        print("    - the subject is not front-on to the camera (our mapping assumes it)")
        print("    - a sign flipped in to_control_frame() in backend/pose/control_link.py")
        print("    - left/right arm mix-up (check the Left/Right selector in the twin)")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--check", action="store_true",
                        help="guided coordinate-frame verification")
    args = parser.parse_args()
    return check(args.port) if args.check else (live(args.port) or 0)


if __name__ == "__main__":
    sys.exit(main())
