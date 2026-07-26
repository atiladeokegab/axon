"""Joint control effort -> 8 stimulation channel duties.

CHANNEL MAP (must match firmware/config/pins.py and the electrode placement in
docs/WIRING.md):

  CH1 biceps/brachialis   elbow flex        agonist    (+elbow effort)
  CH2 triceps             elbow extend      antagonist (-elbow effort)
  CH3 anterior deltoid    shoulder flex     agonist    (+flex effort)
  CH4 posterior deltoid   shoulder extend   antagonist (-flex effort)
  CH5 middle deltoid      shoulder abduct   agonist    (+abd effort)
  CH6 SPARE               unused            -          (gravity adducts)
  CH7 finger flexors      grip close        triggered
  CH8 finger extensors    grip release      triggered

TWO RULES THAT MATTER:

1. NEVER CO-CONTRACT. A signed effort drives exactly one muscle of an
   antagonist pair; the other is held at zero. Driving both simultaneously
   wastes current, fatigues the subject fast, and can lock the joint solid.

2. ABDUCTION IS ONE-DIRECTIONAL. There is no adductor channel: the natural
   adductor is pectoralis major, which would mean chest electrodes with
   current near the heart - forbidden. Gravity lowers the arm instead, so a
   negative abduction effort simply means "stop stimulating CH5".
"""

CHANNEL_ORDER = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8"]

# joint -> (agonist_channel, antagonist_channel_or_None)
JOINT_CHANNELS = {
    "elbow":         ("CH1", "CH2"),
    "shoulder_flex": ("CH3", "CH4"),
    "shoulder_abd":  ("CH5", None),    # gravity handles adduction
}

GRIP_CLOSE = "CH7"
GRIP_RELEASE = "CH8"


def _apply_deadzone(duty, min_effective, enabled):
    """Snap a duty out of the actuator's dead zone.

    The firmware discards pulses shorter than MIN_PULSE_MS, so any duty below
    min_effective produces NO relay movement whatsoever. Commanding 0.10 and
    commanding 0.0 are physically identical - which makes the bottom of the
    control range silently inert.

    Rounding to the nearer end keeps the mapping monotonic and predictable:
      * below half the threshold -> 0    (a tiny effort should do nothing)
      * above half the threshold -> min_effective (make it actually fire)
    """
    if not enabled or duty <= 0.0 or duty >= min_effective:
        return duty
    return min_effective if duty >= (min_effective / 2.0) else 0.0


def efforts_to_duties(efforts, grip=False, release=False, duty_max=0.70,
                      min_effective=0.0, deadzone=False, grip_duty=None):
    """Convert signed per-joint efforts into a duty vector for the 8 channels.

    efforts: {"elbow": +0.4, "shoulder_flex": -0.2, ...}
             positive = increase the angle (agonist)
             negative = decrease the angle (antagonist, or gravity if none)

    grip_duty exists because grip is the one channel that is NOT servoed. The
    arm joints modulate duty to place a limb, so duty is their force knob and
    duty_max keeps a margin. The hand is an end-effector with two useful states,
    and a half-closed grasp is just a hand that drops the object, so it runs at a
    higher ceiling. Defaults to duty_max if not given, so an old caller cannot
    accidentally obtain the higher value.

    The board clamps this again either way (firmware settings.CHANNEL_DUTY_MAX),
    and will refuse anything above DUTY_MAX for a channel not on its exempt
    list. Nothing here is trusted.
    """
    duties = {ch: 0.0 for ch in CHANNEL_ORDER}

    for joint, (agonist, antagonist) in JOINT_CHANNELS.items():
        effort = float(efforts.get(joint, 0.0))
        if effort > 0.0:
            duties[agonist] = _apply_deadzone(
                min(effort, duty_max), min_effective, deadzone)
        elif effort < 0.0 and antagonist is not None:
            duties[antagonist] = _apply_deadzone(
                min(-effort, duty_max), min_effective, deadzone)
        # effort < 0 with no antagonist -> leave both at 0 and let gravity work

    gd = duty_max if grip_duty is None else float(grip_duty)
    if grip:
        duties[GRIP_CLOSE] = gd
        duties[GRIP_RELEASE] = 0.0
    elif release:
        duties[GRIP_RELEASE] = gd
        duties[GRIP_CLOSE] = 0.0

    return duties


def duties_to_list(duties):
    """Ordered list for the wire protocol."""
    return [round(duties.get(ch, 0.0), 3) for ch in CHANNEL_ORDER]


def describe(duties):
    """Human-readable one-liner for the console UI."""
    active = [(ch, d) for ch, d in duties.items() if d > 0.001]
    if not active:
        return "idle"
    return " ".join("%s:%.2f" % (ch, d) for ch, d in sorted(active))
