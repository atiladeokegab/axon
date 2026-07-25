"""Teleoperation entrypoint.

    Arrow keys  - move the hand:
                    LEFT / RIGHT  -> shoulder abduction (hand out / in)
                    UP   / DOWN   -> raise / lower the hand (shoulder + elbow)
    G           - toggle GRIP open/closed
    A           - ARM   (stimulation enabled)
    D           - DISARM
    X           - EMERGENCY STOP (latched; press A to re-arm)
    ?           - show the key list again
    Q           - quit (disarms on the way out)

    NOTE: grip is 'G', not Shift. A terminal cannot detect a bare Shift press.

Run modes (activate the venv first; on Windows use `py` if your system does not
recognise `python`):
    python run.py                        # auto-discover the board
    python run.py --host 192.168.137.154 # skip discovery (firewall-proof)
    python run.py --sim                  # simulated arm + no board; dry run
    python run.py --sim-hw               # simulated arm DRIVING REAL RELAYS
    python run.py --no-board             # real pose input, nothing stimulated

--sim-hw is hardware-in-the-loop: the virtual arm closes the control loop, but
the duties it produces go to the real board, so relays and PWM actually switch.
It needs no pose estimator, which makes it the way to verify the whole chain
end to end before anyone is wired up. NOTHING MUST BE CONNECTED TO A PERSON.

SAFETY: this program is the PERFORMANCE layer. The ESP32 independently clamps
duty, enforces burst/cooldown, and opens every relay if these packets stop.
Never rely on this process for safety - the subject's physical kill switch and
the firmware watchdog are the real protections.
"""

import argparse
import shutil
import sys
import time

import settings as C
import control_loop
import link as link_mod
import mapping
import pose_api


# ---------------------------------------------------------------------------
# Cross-platform non-blocking key reader
# ---------------------------------------------------------------------------
class KeyReader:
    """Reads keys without blocking. Windows uses msvcrt; POSIX uses termios."""

    def __init__(self):
        self.win = sys.platform.startswith("win")
        self._fd = None
        self._old = None
        if not self.win:
            import termios, tty  # noqa
            self._termios = termios
            self._tty = tty

    def __enter__(self):
        if not self.win:
            self._fd = sys.stdin.fileno()
            self._old = self._termios.tcgetattr(self._fd)
            self._tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc):
        if not self.win and self._old is not None:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._old)

    def get(self):
        """Return a key name or None. Arrow keys -> 'UP'/'DOWN'/'LEFT'/'RIGHT'."""
        if self.win:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):        # extended (arrows)
                code = msvcrt.getch()
                return {b"H": "UP", b"P": "DOWN",
                        b"K": "LEFT", b"M": "RIGHT"}.get(code)
            try:
                return ch.decode("utf-8", "ignore").upper()
            except Exception:
                return None
        else:
            import select
            if not select.select([sys.stdin], [], [], 0)[0]:
                return None
            ch = sys.stdin.read(1)
            if ch == "\x1b":                     # ANSI escape (arrows)
                rest = sys.stdin.read(2) if select.select(
                    [sys.stdin], [], [], 0.01)[0] else ""
                return {"[A": "UP", "[B": "DOWN",
                        "[D": "LEFT", "[C": "RIGHT"}.get(rest)
            return ch.upper()


# ---------------------------------------------------------------------------
def build(args):
    if args.sim_hw:
        # HARDWARE-IN-THE-LOOP: the virtual arm closes the control loop, but the
        # duties it produces are sent to the REAL board, so relays actually
        # switch. Exercises the full chain - PI -> mapping -> UDP -> firmware
        # PWM -> relay contacts - with no pose estimator and nobody connected.
        pose = pose_api.SimulatedPoseSource()
        board = link_mod.EspLink(host=args.host)
        print("[run] SIM + HARDWARE: virtual arm, REAL relays switching")
        print("[run] board link -> %s:%d" % (board.host, board.port))
        print("[run] *** RELAYS WILL FIRE. Nothing should be connected to a"
              " person. ***")
    elif args.sim:
        pose = pose_api.SimulatedPoseSource()
        board = link_mod.NullLink()
        print("[run] SIMULATION: virtual arm, no hardware driven")
    else:
        pose = pose_api.PoseReceiver().start()
        if args.no_board:
            board = link_mod.NullLink()
            print("[run] pose input live; board link DISABLED (nothing stimulated)")
        else:
            # host=None -> auto-discover; an explicit --host skips discovery,
            # which is the reliable route when a firewall blocks inbound UDP.
            board = link_mod.EspLink(host=args.host)
            print("[run] board link -> %s:%d" % (board.host, board.port))
    return control_loop.ArmController(board, pose), pose


HELP = """
+--------------------------------------------------------------------------+
|  CONTROLS                                                                |
|    A            ARM  - enable stimulation (nothing moves until you do)   |
|    D            disarm                                                   |
|    X            EMERGENCY STOP (latched - press A to re-arm)             |
|    UP / DOWN    raise / lower the hand   (shoulder flex + elbow)         |
|    RIGHT        hand OUT to the side     (abduction, 0-90 deg)           |
|    LEFT         hand back IN toward body (only works if abd > 0)         |
|    G            toggle GRIP  (it is 'G', NOT Shift)                      |
|    ?            show this help again                                     |
|    Q            quit                                                     |
|                                                                          |
|  READING THE STATUS LINE                                                 |
|    act=  where the arm ACTUALLY is now (from the pose estimator)         |
|    tgt=  where you have COMMANDED it to go (moves with the arrow keys)   |
|    The controller stimulates muscles until act catches up to tgt.        |
|                                                                          |
|    stim: which channels are firing and at what duty (0.00-0.70).         |
|          'idle' means nothing is being stimulated.                       |
|                                                                          |
|  THE board: FIELD - what the BOARD itself reports (hardware modes only)  |
|    board:ok         healthy, armed, listening                            |
|    board:DISARMED   board is not armed - press A                         |
|    board:KILLED(..) e-stop or kill latched - press A to re-arm           |
|    board:REBOOTED   the board restarted (power/crash). Press A to re-arm |
|    board:LOST 3s    no heartbeat - Wi-Fi dropped or board powered off    |
|    board:NO-REPLY   never heard from it - wrong IP, or firewall          |
|  You do NOT need bench.py to see this (running both would fight over     |
|  the link). Everything you need is on this line.                         |
|                                                                          |
|  IF NOTHING MOVES, CHECK THE STATE FLAG:                                 |
|    DISARMED     -> press A                                               |
|    KILLED       -> press A to re-arm                                     |
|    pose:STALE   -> no data from the pose estimator (run with --sim to    |
|                    use a virtual arm instead)                            |
|                                                                          |
|  A KEY CAN ALSO LOOK DEAD FOR TWO NORMAL REASONS - both are announced:   |
|    at a joint limit  -> e.g. LEFT at abduction 0 deg cannot go lower     |
|    inside the deadband -> each press is 3 deg but the controller ignores |
|       errors under the 3 deg deadband (which stops the arm buzzing), so |
|       press an arrow 2-3 times before expecting movement                 |
+--------------------------------------------------------------------------+
"""


def render(ctl, sim_mode, hw=False):
    s = ctl.status()
    if s["killed"]:
        state = "KILLED"
    elif not s["armed"]:
        state = "DISARMED"
    else:
        state = "ARMED"

    # Say WHY nothing is happening, rather than leaving the user guessing.
    if s["killed"]:
        why = " <- press A to re-arm"
    elif not s["armed"]:
        why = " <- press A to arm"
    elif not s["pose_ok"]:
        why = " <- NO POSE DATA (try --sim)"
    else:
        why = ""

    if sim_mode:
        # Make it unmistakable when real relays are switching.
        pose_flag = "SIM+HW" if hw else "pose:SIM"
    else:
        pose_flag = "pose:OK" if s["pose_ok"] else "pose:STALE"

    # What the BOARD says about itself. Shown so a reboot / disarm / link loss
    # is visible here, without needing bench.py alongside (which would fight
    # over the UDP link).
    bd = ""
    if hw or not sim_mode:
        age = ctl.board_age_s()
        bs = ctl.board_status
        if age is None:
            bd = " | board:NO-REPLY"
        elif age > 2.0:
            bd = " | board:LOST %.0fs" % age
        elif ctl.board_rebooted:
            bd = " | board:REBOOTED(re-arm)"
        elif bs and bs.get("killed"):
            bd = " | board:KILLED(%s)" % bs.get("fault")
        elif bs and not bs.get("armed"):
            bd = " | board:DISARMED"
        else:
            bd = " | board:ok"

    # NOTE: board state goes near the FRONT. It is the most important field
    # when something stops working, and the line gets truncated to the terminal
    # width - anything at the end can vanish exactly when you need it.
    line = ("[%-8s] %-9s%-22s| elbow act%6.1f tgt%6.1f | flex act%6.1f tgt%6.1f "
            "| abd act%6.1f tgt%6.1f | grip:%-6s | stim: %s%s"
            % (state, pose_flag, bd,
               s["measured"]["elbow"], s["targets"]["elbow"],
               s["measured"]["shoulder_flex"], s["targets"]["shoulder_flex"],
               s["measured"]["shoulder_abd"], s["targets"]["shoulder_abd"],
               "CLOSED" if s["grip"] else "open",
               mapping.describe(s["duties"]), why))

    # Must never exceed the terminal width. A line longer than the window wraps,
    # and then '\r' only returns to the start of the LAST wrapped row - so the
    # status line appears duplicated and smeared instead of refreshing in place.
    # Reserve one column: some terminals auto-wrap on writing the final cell.
    width = max(40, shutil.get_terminal_size((120, 24)).columns - 1)
    sys.stdout.write("\r" + line[:width].ljust(width))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="FES arm teleoperation")
    ap.add_argument("--sim", action="store_true",
                    help="simulated arm, no hardware driven")
    ap.add_argument("--sim-hw", action="store_true",
                    help="simulated arm BUT drive the real board: relays and "
                         "PWM actually switch. No pose estimator needed. "
                         "Nothing must be connected to a person.")
    ap.add_argument("--no-board", action="store_true",
                    help="use real pose input but do not drive the board")
    ap.add_argument("--host", default=None,
                    help="board IP, e.g. 192.168.137.154. Default: auto-discover "
                         "(needs inbound UDP through the firewall)")
    args = ap.parse_args()

    ctl, pose = build(args)
    sim_arm = pose if isinstance(pose, pose_api.SimulatedPoseSource) else None

    print(HELP)
    if sim_arm is not None:
        print("SIMULATION MODE - a virtual arm, no hardware is driven.\n")
    print("Press A to arm, then use the arrow keys.\n")

    period = 1.0 / C.CONTROL_RATE_HZ
    notice = ""          # transient message printed above the status line
    notice_until = 0.0

    def say(msg):
        """Print a message above the live status line without corrupting it."""
        width = max(40, shutil.get_terminal_size((120, 24)).columns - 1)
        sys.stdout.write("\r" + " " * width + "\r")
        print(msg)

    try:
        with KeyReader() as keys:
            while True:
                loop_start = time.monotonic()

                key = keys.get()
                jogged = False
                jog_joint = None
                jog_moved = True

                if key == "Q":
                    break
                elif key == "X":
                    ctl.kill()
                    say("[X] EMERGENCY STOP - latched. Press A to re-arm.")
                elif key == "A":
                    ctl.arm()
                    say("[A] ARMED - stimulation enabled.")
                elif key == "D":
                    ctl.disarm()
                    say("[D] disarmed.")
                elif key == "?":
                    say(HELP)
                elif key == "G":
                    # Toggle, not hold: a terminal cannot report key-release,
                    # so a held grip is not detectable. Press G again to open.
                    ctl.set_grip(not ctl.grip)
                    say("[G] grip %s" % ("CLOSED" if ctl.grip else "open"))
                elif key == "UP":
                    a = ctl.jog("shoulder_flex", C.JOG_STEP_DEG)
                    b = ctl.jog("elbow", C.JOG_STEP_DEG * 0.5)
                    jogged, jog_joint, jog_moved = True, "shoulder_flex", (a or b)
                elif key == "DOWN":
                    a = ctl.jog("shoulder_flex", -C.JOG_STEP_DEG)
                    b = ctl.jog("elbow", -C.JOG_STEP_DEG * 0.5)
                    jogged, jog_joint, jog_moved = True, "shoulder_flex", (a or b)
                elif key == "RIGHT":
                    jog_moved = ctl.jog("shoulder_abd", C.JOG_STEP_DEG)
                    jogged, jog_joint = True, "shoulder_abd"
                elif key == "LEFT":
                    jog_moved = ctl.jog("shoulder_abd", -C.JOG_STEP_DEG)
                    jogged, jog_joint = True, "shoulder_abd"

                # A keypress that appears to do nothing is the most confusing
                # thing about this UI, so name the reason. Three distinct cases:
                if jogged and time.monotonic() > notice_until:
                    if not jog_moved:
                        lo, hi = C.JOINT_LIMITS[jog_joint]
                        say("      (%s is already at its limit - range is "
                            "%.0f to %.0f deg)" % (jog_joint, lo, hi))
                        notice_until = time.monotonic() + 2.0
                    elif not ctl.armed:
                        say("      (target moved, but stimulation is %s "
                            "- press A)"
                            % ("KILLED" if ctl.killed else "DISARMED"))
                        notice_until = time.monotonic() + 3.0
                    else:
                        inside, band = ctl.within_deadband(jog_joint)
                        if inside:
                            say("      (target is within the %.0f deg deadband "
                                "- press again to move further)" % band)
                            notice_until = time.monotonic() + 2.0

                # Advance the virtual arm using what we are commanding.
                if sim_arm is not None:
                    sim_arm.step(ctl.efforts, period)

                ctl.step()

                # Announce TIMER keep-alive presses with a timestamp, so a
                # stimulation dropout can be correlated against them exactly.
                if ctl.timer_pressed_at is not None:
                    say("[%s] TIMER keep-alive relay fired (press #%s)"
                        % (time.strftime("%H:%M:%S"),
                           ctl._last_timer_presses))
                    ctl.timer_pressed_at = None

                if ctl.board_rebooted:
                    say("[%s] *** BOARD REBOOTED *** (uptime went backwards)"
                        % time.strftime("%H:%M:%S"))
                    say("      Power/brownout or a crash. Press A to re-arm.")
                    ctl.board_rebooted = False

                render(ctl, sim_arm is not None, hw=args.sim_hw)

                slack = period - (time.monotonic() - loop_start)
                if slack > 0:
                    time.sleep(slack)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[run] shutting down - disarming")
        try:
            ctl.kill()
        except Exception:
            pass
        if hasattr(pose, "stop"):
            pose.stop()


if __name__ == "__main__":
    main()
