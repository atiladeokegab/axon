# lib/safety.py - the independent safety layer.
#
# DESIGN RULE: the PC-side controller is for PERFORMANCE. This module is for
# SAFETY, and it must hold even if the controller is buggy, hung, malicious, or
# gone. Nothing here depends on the control loop behaving correctly.
#
# Layers, in order of how much we trust them:
#   1. Subject's physical in-line kill switch  (works if EVERYTHING else fails)
#   2. Hardware e-stop button -> GPIO interrupt (works if the PC dies)
#   3. Command watchdog on this board          (works if the app hangs / link drops)
#   4. Duty ceiling + burst/cooldown clamps    (works even on valid commands)
#   5. Operator X-key e-stop on the PC         (convenience only)
#
# The AUVON intensity level is capped BY HAND before a run. Because the device
# is a constant-CURRENT source, that hand-set cap physically bounds peak
# current; PWM here can only ever lower the average, never exceed it.

from lib.hal import ticks_ms, ticks_diff


class SafetySupervisor:
    """Owns arm/disarm state, the command watchdog, and duty clamping."""

    def __init__(self, duty_max=0.70, command_timeout_ms=500,
                 max_burst_ms=4000, cooldown_ms=2000):
        self.duty_max = duty_max
        self.command_timeout_ms = command_timeout_ms
        self.max_burst_ms = max_burst_ms
        self.cooldown_ms = cooldown_ms

        self._armed = False
        self._killed = False          # latched; requires explicit re-arm
        self._fault = None
        self._last_command_ms = None

    # ---- arm / disarm -----------------------------------------------------
    def arm(self):
        """Explicitly enable stimulation. Clears a latched kill."""
        self._armed = True
        self._killed = False
        self._fault = None
        self._last_command_ms = ticks_ms()
        return self.state()

    def disarm(self, reason="disarmed"):
        self._armed = False
        self._fault = reason
        return self.state()

    def kill(self, reason="estop"):
        """Latching emergency stop. Only arm() clears it."""
        self._armed = False
        self._killed = True
        self._fault = reason
        return self.state()

    # ---- watchdog ---------------------------------------------------------
    def note_command(self):
        """Call on every valid command packet - this pets the watchdog."""
        self._last_command_ms = ticks_ms()

    def watchdog_expired(self, now=None):
        """True if the PC has gone quiet for longer than the timeout.

        A missing command is treated as loss of control, not as 'hold last
        value' - stale commands driving a person is exactly what we refuse.
        """
        if self._last_command_ms is None:
            return True
        if now is None:
            now = ticks_ms()
        return ticks_diff(now, self._last_command_ms) > self.command_timeout_ms

    # ---- gate -------------------------------------------------------------
    def stim_allowed(self, now=None):
        """Single question every service loop asks before energising anything."""
        if self._killed or not self._armed:
            return False
        if self.watchdog_expired(now):
            if self._fault is None:
                self._fault = "watchdog"
            return False
        return True

    def clamp_duty(self, duty):
        """Clamp a requested duty into the safe envelope."""
        try:
            d = float(duty)
        except (TypeError, ValueError):
            return 0.0
        if d != d:            # NaN
            return 0.0
        if d < 0.0:
            return 0.0
        if d > self.duty_max:
            return self.duty_max
        return d

    # ---- introspection ----------------------------------------------------
    def is_armed(self):
        return self._armed

    def is_killed(self):
        return self._killed

    def fault(self):
        return self._fault

    def state(self):
        return {
            "armed": self._armed,
            "killed": self._killed,
            "fault": self._fault,
            "duty_max": self.duty_max,
        }
