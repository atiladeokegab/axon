"""Tunables for the PC-side controller.

Control gains live here; SAFETY limits live on the ESP32 (firmware/config/
settings.py). That separation is deliberate - editing this file can make the
demo sloppy, but it cannot make the hardware unsafe.
"""

# ---- link to the ESP32 ----------------------------------------------------
# Both the PC and the board join the same hotspot (station mode), so the board
# gets a DHCP address. run.py auto-discovers it by UDP broadcast; this value is
# only the fallback if discovery fails (and matches the board's own SoftAP
# address, which it raises if the hotspot is unreachable).
ESP32_HOST = None              # None => auto-discover
ESP32_HOST_FALLBACK = "192.168.4.1"
ESP32_PORT = 8080
SEND_RATE_HZ = 30              # must be well inside the firmware's 500 ms watchdog

# Shared token sent on every control packet. Must match CONTROL_TOKEN in
# firmware/config/device_secrets.py. Without it any host on the hotspot could
# send {"arm":true,...} to port 8080 and stimulate the subject. This is a
# shared secret, not authentication - it stops accidents and casual
# interference, not someone sniffing the network.
CONTROL_TOKEN = "juno-fes-2026"

# ---- pose ingest (from teammate's estimator) ------------------------------
POSE_LISTEN_HOST = "0.0.0.0"
POSE_LISTEN_PORT = 9090
# A pose older than this is refused: driving a limb from a stale pose is the
# failure mode we most want to avoid.
POSE_STALE_MS = 300
# ---- Pose filtering -------------------------------------------------------
# Two stages, because vision noise is two different problems:
#
#   1. MEDIAN window - rejects landmark JUMPS (occasional large outliers when
#      the estimator mis-locates a joint). An exponential filter cannot remove
#      these, it only smears one across several frames. Must be ODD. Lag is
#      about half the window: at ~28 Hz, 5 samples is ~90 ms wide, ~35 ms lag.
#   2. EXPONENTIAL alpha - smooths the continuous jitter that remains.
#      Lower = smoother but laggier.
#
# MEASURE BEFORE TUNING THESE:  python tools/pose_noise.py
# It reports the actual noise and recommends a deadband. The deadband in GAINS
# must exceed ~3 sd of the FILTERED signal or the controller chases jitter.
# Window of 9 rather than 5: measured on the noisiest real capture, 9 took the
# elbow from 3.15 to 2.78 deg sd for 73 ms more lag. With the loop already at
# 150-300 ms and a demo that values a steady arm over a fast one, that is a
# good trade. It also survives outlier bursts up to 4 long instead of 2.
POSE_MEDIAN_WINDOW = 9
POSE_FILTER_ALPHA = 0.35       # only used when POSE_FILTER_MODE = "ema"

# Stage 2 is ADAPTIVE by default (the one-euro filter, see filters.py). A fixed
# alpha has to be one compromise for two opposite requirements - smooth while
# the arm is held still, responsive while it moves - so it is too noisy for the
# first and too laggy for the second. Making the cutoff track the signal's own
# speed gets both.
#
# Measured on a real held-posture capture (553 samples at 27.6 Hz, elbow):
#   raw                              sd 2.51    lag   0 ms   needs 7.5 deg band
#   median-5 + EMA 0.35 (the old)    sd 1.70    lag 145 ms   needs 5.0 deg band
#   median-5 + EMA 0.10              sd 1.14    lag 398 ms   needs 3.5 deg band
#   median-5 + one-euro 0.15/0.005   sd 1.06    lag 181 ms   needs 3.0 deg band
#
# The fixed filter had to buy that noise reduction with 398 ms of lag, which is
# more than the rest of the loop put together. The adaptive one gets there for
# 181 ms because it only smooths hard while the arm is actually still.
POSE_FILTER_MODE = "oneeuro"   # "oneeuro" | "ema" (ema = the old behaviour)

# LOWER mincutoff = smoother when still; this sets the noise floor.
# HIGHER beta = less lag while moving, at the cost of noise during motion.
# Tune mincutoff first with beta = 0, then raise beta until motion feels prompt.
# Re-check any change against a saved capture, no subject needed:
#   py tools/pose_noise.py --replay --mincutoff 0.15 --beta 0.005
# mincutoff barely matters below ~0.15 (2.78 vs 2.83 deg sd across 0.05-0.15
# on real data) because beta dominates once anything moves, so there is no
# reason to pay the extra stationary lag of a lower value.
POSE_ONEEURO_MINCUTOFF = 0.10
POSE_ONEEURO_BETA = 0.005

# Stage 0: reject physically impossible joint velocities before anything else.
# A median only rejects an outlier while it is a minority of its window, so a
# BURST of bad samples becomes the median and passes through untouched. A real
# capture had bursts of 6 consecutive mis-located samples on shoulder
# abduction, with single-frame steps around 1000 deg/s while the subject was
# deliberately motionless.
#
# 400 deg/s is far above anything FES can drive a limb at, so nothing real is
# clipped. Set to 0 to disable.
POSE_MAX_RATE_DEG_S = 400.0

# ---- control loop ---------------------------------------------------------
CONTROL_RATE_HZ = 30

# Per-joint PI gains. Deliberately LOW: total loop delay is ~150-300 ms
# (pose estimation + link + electromechanical delay + relay PWM), which caps
# usable bandwidth near 0.5-1 Hz. High gains here just cause oscillation.
# i_limit is the maximum DUTY the integrator alone may contribute. It must be
# large enough to hold the joint against gravity at zero error, so it sits at
# (or just under) DUTY_MAX. See pid.py for why this is not a raw accumulator cap.
#
# DEADBAND sets steady-state accuracy: the arm settles roughly one deadband
# short of target, ~1:1. It used to be 5-6 deg because collapsing to zero
# output inside the band caused a relay limit cycle. Now that the controller
# HOLDS its learned duty inside the band (see pid.py), that chatter is gone -
# measured 0 relay transitions at every deadband from 0.5 deg upward - so the
# band can be much tighter and the offset correspondingly smaller.
#
# 3 deg is a deliberate compromise, NOT an optimum: on real hardware the band
# must still exceed the pose estimator's jitter, or the controller chases noise.
# Re-tune on hardware once you know the measured noise (see docs/CONTROL.md).
GAINS = {
    #                 Kp      Ki    deadband_deg  i_limit(duty)
    "elbow":         (0.020, 0.030, 3.0, 0.70),
    "shoulder_flex": (0.016, 0.024, 3.0, 0.70),
    "shoulder_abd":  (0.016, 0.024, 3.0, 0.70),
}

# ---- adaptive deadband ----------------------------------------------------
# The deadbands above are FLOORS, not fixed values. The controller raises them
# at runtime to whatever the live pose noise actually requires.
#
# WHY: repeated 20 s captures on the same rig gave elbow noise of 2.5, 3.3 and
# 7.3 deg on different runs - a 3x swing driven by lighting, posture, distance
# and clothing rather than by anything in this file. A constant tuned to the
# quiet run chatters the relays on the noisy one; a constant tuned to the noisy
# run makes the arm stop ~8 deg short on the quiet one. Neither is right,
# because the correct value is not a constant.
#
# So PoseReceiver measures the noise continuously (from the spread of
# successive differences, which is insensitive to the arm actually moving) and
# the controller sizes its deadband from that. Fix the camera setup and the
# deadband tightens on its own; let the setup degrade and it widens instead of
# chattering.
# DEFAULT OFF, after measuring it. The premise was "the deadband must exceed
# the measurement noise or the controller chases jitter" - which was true when
# the controller collapsed its output to zero inside the band, and stopped being
# true when it started HOLDING its learned duty there instead (see pid.py).
#
# Measured on the simulated plant with real noise levels injected, counting
# relay transitions over 25 s at the setpoint:
#
#   deadband     0.5   1.0   2.0   3.0   5.0   8.0   12.0
#   switches       0     0     0     0     0     0      0     <- at BOTH
#   steady err   0.3   0.3   0.3   0.2   2.0   4.2    7.6        noise levels
#
# Zero chatter everywhere. Widening the band bought nothing and cost accuracy:
# at 12 deg the arm never settled at all, and every keypress smaller than the
# band did nothing, which is what made the controls feel dead.
#
# The noise MEASUREMENT is still taken and still reported - it is a genuinely
# useful early warning that the camera setup is degrading. It just no longer
# silently changes how the arm behaves. Measure and report; do not auto-act.
DEADBAND_ADAPTIVE = False
DEADBAND_NOISE_SIGMA = 3.0     # only used when DEADBAND_ADAPTIVE is True
# Guard rail if it is ever re-enabled. Lowered from 12.0 because the table above
# shows the arm stops settling long before that, so a "safety" ceiling that
# permits it is not a safety ceiling.
DEADBAND_MAX_DEG = 6.0

# Duty requested by the controller is additionally clamped by the firmware.
DUTY_MAX = 0.70

# ---- Actuator dead zone ---------------------------------------------------
# The firmware DROPS any pulse shorter than MIN_PULSE_MS (25 ms), because a
# mechanical relay cannot complete a clean transition in less. With a 150 ms
# PWM period that makes every duty below 25/150 = 0.167 produce *no output at
# all* - the relay never moves.
#
# So the actuator's usable range is 0.167-0.70, not 0-0.70, and a controller
# that is unaware of this silently does nothing whenever it asks for a small
# effort. That is exactly what happened in hardware-in-the-loop: the simulated
# arm needed only ~0.08-0.11 to hold position, so nothing ever fired.
#
# Keep in sync with firmware/config/settings.py (MIN_PULSE_MS / PWM_PERIOD_MS).
MIN_EFFECTIVE_DUTY = 25.0 / 150.0     # = 0.167

# Requests below MIN_EFFECTIVE_DUTY are snapped to it (so they actually do
# something) or to zero (so tiny efforts do not cause a twitch), whichever is
# nearer. Set False to disable and command raw duty.
DEADZONE_COMPENSATION = True

# ---- joint limits (degrees) - refused targets, plus a firmware ROM backstop
#
# AXIS -> KEY -> MUSCLE, so the naming cannot drift from the electrodes:
#   elbow          W / S          CH1 biceps     / CH2 triceps
#   shoulder_flex  LEFT / RIGHT   CH3 anterior   / CH4 posterior deltoid
#   shoulder_abd   UP / DOWN      CH5 middle deltoid / gravity (no channel)
#
# "shoulder_flex" is the FORWARD-BACK axis and "shoulder_abd" is ELEVATION -
# how high the arm is. The names are anatomical rather than operational, which
# has caused confusion; the mapping above is the authoritative version.
JOINT_LIMITS = {
    "elbow":         (5.0, 140.0),    # 0 = straight arm
    # Backward travel widened from -20 to -40: the posterior deltoid drives
    # this axis negative, and -20 clipped the backward swing well short of the
    # ~45-60 deg a shoulder extends through. A target the arm cannot be
    # commanded to reach looks like a dead control, not a limit.
    "shoulder_flex": (-40.0, 110.0),  # + = forward, - = back
    "shoulder_abd":  (0.0, 90.0),     # + = raised out to the side
}

# ---- teleoperation --------------------------------------------------------
JOG_STEP_DEG = 3.0             # per key repeat

# Grip runs at a FULL duty cycle, unlike every servoed channel. It is triggered
# rather than modulated - the hand is an end-effector with two useful states,
# and a partial grasp is a hand that drops the object. The firmware permits this
# only for CH7/CH8 (firmware/config/settings.py CHANNEL_DUTY_MAX) and still
# clamps everything else to DUTY_MAX.
#
# At 1.0 the relay stops switching, so a held grasp is continuous contact rather
# than a 6.7 Hz buzz. MAX_BURST_MS still applies: the grip releases after 4 s and
# needs 2 s of rest, so it cannot hold an object indefinitely.
GRIP_KEY_DUTY = 1.0

# ---- arm geometry (metres) - only used for Cartesian jog / display --------
UPPER_ARM_M = 0.30
FOREARM_M = 0.26
