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
# Vision-derived angles are jittery; alpha of an exponential low-pass filter.
# Lower = smoother but more lag. 0.35 is a reasonable starting point.
POSE_FILTER_ALPHA = 0.35

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
JOINT_LIMITS = {
    "elbow":         (5.0, 140.0),    # 0 = straight arm
    "shoulder_flex": (-20.0, 110.0),  # + = arm forward/up
    "shoulder_abd":  (0.0, 90.0),     # + = arm out to the side
}

# ---- teleoperation --------------------------------------------------------
JOG_STEP_DEG = 3.0             # per key repeat
GRIP_KEY_DUTY = DUTY_MAX

# ---- arm geometry (metres) - only used for Cartesian jog / display --------
UPPER_ARM_M = 0.30
FOREARM_M = 0.26
