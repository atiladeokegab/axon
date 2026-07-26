"""Human Control - the teleoperation loop, served as a tab of the Axon site.

WHAT THIS IS. controller/run.py drives a person's arm from the keyboard: it
reads a pose estimate, runs a PI loop per joint, maps joint effort onto eight
muscle channels and sends duties to the ESP32 over UDP. This package puts the
same loop behind a web UI so the whole product is one application.

TWO THINGS IT DELIBERATELY DOES NOT DO.

1. It does not FORK the control code. Every safety-relevant decision - the PI
   gains, the joint limits, the channel mapping, the pose filtering - is
   imported from the existing `controller/` package rather than copied. We have
   already been bitten once in this repo by three near-identical copies of the
   vision backend drifting apart; the control loop is the last place that
   should happen, because a divergence there is a divergence in how a person's
   arm is driven. controller/run.py keeps working unchanged, and both entry
   points behave identically because they are running the same code.

2. It does not become a safety layer. The ESP32 still clamps duty, enforces
   burst/cooldown, refuses antagonist co-contraction, and opens every relay if
   these packets stop. A browser is a WORSE emergency stop than a terminal - a
   tab can lose focus, a page can hang, a laptop can sleep - so the on-screen
   stop is a convenience on top of the subject's physical kill switch and the
   board's watchdog, never a replacement. See docs/SAFETY.md.

POSE COMES FROM THE SHARED CAMERA. Live Twin already owns a LiveRuntime that
holds one camera and one MediaPipe model for however many clients are watching.
This subscribes to that same broadcaster in-process, so the two tabs cannot
fight over the webcam and there is no UDP hop. The separate 9090 path that
controller/run.py uses still exists and is untouched.
"""

import sys
from pathlib import Path

# The control code lives at the repository root, one level above the site.
# Adding it to sys.path rather than vendoring a copy is what keeps a single
# source of truth. Checked for module-name collisions against the site first:
# controller/ contributes settings, mapping, pid, kinematics, filters, link,
# pose_api and control_loop, none of which the webapp defines.
_CONTROLLER_DIR = Path(__file__).resolve().parents[2] / "controller"

if not _CONTROLLER_DIR.is_dir():
    raise RuntimeError(
        "Cannot find the controller package at %s. The Human Control tab "
        "imports the real control loop from there rather than keeping its own "
        "copy, so the site must be run from inside the repository."
        % _CONTROLLER_DIR
    )

if str(_CONTROLLER_DIR) not in sys.path:
    # Appended, not inserted at 0: the site's own modules must always win if a
    # name is ever added on both sides.
    sys.path.append(str(_CONTROLLER_DIR))

__all__ = ["_CONTROLLER_DIR"]
