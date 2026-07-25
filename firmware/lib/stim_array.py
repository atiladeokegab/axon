# lib/stim_array.py - the 8-channel muscle array + timer keep-alive.
#
# This is the ONLY module upstream code should need on the firmware side. It
# owns the eight PWM-gated channels, applies the safety supervisor's clamps,
# and keeps both AUVON units awake by pulsing their TIMER button.
#
# Like the previous project, we keep a SOFTWARE MIRROR of state because the
# AUVON gives no digital feedback whatsoever - we only ever know what we
# commanded, never what the device's LCD actually shows.

from lib.hal import Pin, ticks_ms, ticks_diff, sleep_ms
from lib.stim_channel import StimChannel
from config import pins as P
from config import settings as S


class StimArray:
    """Eight relay-gated muscle channels + auto-off keep-alive."""

    def __init__(self, safety):
        self.safety = safety
        self.channels = {}
        for name in P.CHANNEL_ORDER:
            gpio = P.CHANNEL_PINS[name]
            self.channels[name] = StimChannel(
                name, gpio,
                active_low=P.CHANNEL_ACTIVE_LOW,
                period_ms=S.PWM_PERIOD_MS,
                min_pulse_ms=S.MIN_PULSE_MS,
            )

        # Timer keep-alive relay (across the TIMER button of BOTH units).
        self._timer_on = 0 if P.TIMER_ACTIVE_LOW else 1
        self._timer_off = 1 if P.TIMER_ACTIVE_LOW else 0
        self._timer_pin = Pin(P.TIMER_KEEPALIVE_PIN, Pin.OUT,
                              value=self._timer_off)
        self._timer_last_ms = ticks_ms()
        self._timer_press_until = None
        self._timer_presses = 0

        self._last_seq = -1
        self._cocontraction_blocks = 0   # times an antagonist pair was refused

    # ======================================================================
    # COMMAND
    # ======================================================================
    def apply(self, duties, grip=False):
        """Apply a duty vector (list/dict of 8) with safety clamping.

        `grip` is a convenience flag: it drives the grip-close channel (CH7)
        and releases the extensor (CH8). Grip is TRIGGERED, never servoed.
        """
        if not self.safety.stim_allowed():
            self.all_off()
            return False

        if isinstance(duties, dict):
            seq = [duties.get(n, 0.0) for n in P.CHANNEL_ORDER]
        else:
            seq = list(duties) + [0.0] * (len(P.CHANNEL_ORDER) - len(duties))

        clamped = {}
        for i, name in enumerate(P.CHANNEL_ORDER):
            clamped[name] = self.safety.clamp_duty(seq[i])

        # ---- refuse antagonist co-contraction ------------------------------
        # Driving both muscles of a pair locks the joint, wastes current and
        # fatigues the subject quickly. The PC-side mapping already avoids it,
        # but the board must not depend on that: a controller bug, a stale
        # packet or a hostile sender could ask for it.
        #
        # Both sides are zeroed rather than picking a winner - a request to
        # co-contract means the commanding side is wrong, and the safe response
        # to a wrong command is no stimulation, not a guess at intent.
        for a, b in getattr(P, "ANTAGONIST_PAIRS", ()):
            if clamped.get(a, 0.0) > 0.0 and clamped.get(b, 0.0) > 0.0:
                clamped[a] = 0.0
                clamped[b] = 0.0
                self._cocontraction_blocks += 1

        for name in P.CHANNEL_ORDER:
            self.channels[name].set_duty(clamped[name])

        if grip:
            # Power grasp: close the flexors, make sure extensors are released.
            # Never co-contract an antagonist pair.
            self.channels["CH7"].set_duty(self.safety.clamp_duty(S.DUTY_MAX))
            self.channels["CH8"].set_duty(0.0)
        return True

    def all_off(self):
        """E-stop: de-energise every channel immediately."""
        for ch in self.channels.values():
            ch.off()
        self._timer_pin.value(self._timer_off)
        self._timer_press_until = None
        return True

    # ======================================================================
    # SERVICE (call in the tightest loop you can)
    # ======================================================================
    def service(self, now=None):
        """Advance PWM on all channels + handle the keep-alive pulse."""
        if now is None:
            now = ticks_ms()

        if not self.safety.stim_allowed(now):
            # Fail-safe: relays open whenever stimulation is not allowed.
            for ch in self.channels.values():
                if ch.is_on() or ch.duty() > 0.0:
                    ch.off()
        else:
            for ch in self.channels.values():
                ch.service(now,
                           max_burst_ms=S.MAX_BURST_MS,
                           cooldown_ms=S.COOLDOWN_MS)

        self._service_timer(now)

    def _service_timer(self, now):
        """Pulse the TIMER button every few minutes so the units don't auto-off.

        Non-blocking: we assert the relay, then release it on a later pass once
        TIMER_PRESS_MS has elapsed, so the PWM loop is never stalled.

        NOTE: verify on the bench that this press RESETS the auto-off countdown
        and does not cycle the timer duration or alter mode/intensity.
        """
        if self._timer_press_until is not None:
            if ticks_diff(self._timer_press_until, now) <= 0:
                self._timer_pin.value(self._timer_off)
                self._timer_press_until = None
                self._timer_last_ms = now
            return

        if not getattr(P, "TIMER_KEEPALIVE_ENABLED", True):
            return

        if ticks_diff(now, self._timer_last_ms) >= P.TIMER_KEEPALIVE_INTERVAL_MS:
            self._timer_pin.value(self._timer_on)
            self._timer_press_until = now + P.TIMER_PRESS_MS
            self._timer_presses += 1

    def press_timer_now(self):
        """Manual one-shot TIMER press (bring-up / bench testing).

        NON-BLOCKING: schedules the release for a later service() pass. The
        previous version slept for TIMER_PRESS_MS (250 ms), stalling the 1 ms
        control loop - which meant every channel froze mid-PWM and the command
        watchdog lost a quarter of its budget, on a board whose whole job is
        real-time switching.
        """
        now = ticks_ms()
        self._timer_pin.value(self._timer_on)
        self._timer_press_until = now + P.TIMER_PRESS_MS
        self._timer_presses += 1
        return True

    # ======================================================================
    # INTROSPECTION
    # ======================================================================
    def status(self):
        return {
            "channels": {n: c.state() for n, c in self.channels.items()},
            "muscles": P.CHANNEL_MUSCLE,
            "timer_presses": self._timer_presses,
            "cocontraction_blocks": self._cocontraction_blocks,
            "safety": self.safety.state(),
        }
