# lib/stim_channel.py - one PWM-gated stimulation channel.
#
# Each channel is a relay in series with one TENS electrode lead. We do NOT
# control stimulation amplitude from software (the AUVON's intensity is set by
# hand before a run); we modulate the *average* delivered force by gating the
# channel on and off - burst PWM. Muscle is a mechanical low-pass filter, so
# average force tracks duty cycle.
#
# Why non-blocking: eight channels share one CPU. service() does only what is
# due right now and returns, so all eight PWM phases advance together while the
# network and safety loops keep running.

from lib.hal import Pin, ticks_ms, ticks_diff


class StimChannel:
    """One relay-gated muscle channel driven by software burst-PWM."""

    def __init__(self, name, gpio, active_low=True,
                 period_ms=150, min_pulse_ms=25):
        self.name = name
        self.gpio = gpio
        self._on_level = 0 if active_low else 1
        self._off_level = 1 if active_low else 0
        self._period_ms = period_ms
        self._min_pulse_ms = min_pulse_ms

        self._duty = 0.0
        self._is_on = False
        self._cycle_start = ticks_ms()
        self._on_ms = 0

        # Continuous-activity tracking for burst/cooldown enforcement.
        # "Active" = commanded non-zero duty, not merely relay-closed, because
        # a 60% duty burst is still continuous stimulation from the muscle's
        # point of view.
        self._active_since = None
        self._cooling_until = None
        self._last_service = ticks_ms()

        # Construct OPEN (de-energised) - safe default. The relay module's own
        # input pull-up also holds it open while the pin floats at boot.
        self._pin = Pin(gpio, Pin.OUT, value=self._off_level)

    # ---- command ----------------------------------------------------------
    def set_duty(self, duty):
        """Request a duty cycle 0.0-1.0 (safety layer clamps before calling)."""
        if duty < 0.0:
            duty = 0.0
        elif duty > 1.0:
            duty = 1.0
        self._duty = duty
        if duty == 0.0:
            # Command released -> stop counting a burst.
            self._active_since = None
        return self._duty

    def restart_cycle(self, now=None):
        """Begin a fresh PWM period right now, latching the current duty.

        Needed by the exclusive-group scheduler. `_on_ms` is normally latched
        only when a period rolls over, and each channel's period free-runs from
        whenever it was constructed - so a channel that has just been granted a
        time slice would otherwise carry the `_on_ms = 0` it latched while it
        was masked, and sit idle for most of the slot it just won. Measured
        before this existed, a channel commanded 0.60 delivered a 0.05 average.

        MUST ONLY BE CALLED ON AN ACTUAL SLOT CHANGE. Calling it repeatedly
        (for example from apply(), which arrives at 30 Hz) would restart the
        period faster than it can elapse, so `elapsed` would never reach
        `_on_ms` and the relay would stay closed continuously - a silent jump to
        100% duty on every channel.
        """
        if now is None:
            now = ticks_ms()
        self._cycle_start = now
        self._on_ms = int(self._duty * self._period_ms)
        if self._on_ms < self._min_pulse_ms:
            self._on_ms = 0

    def off(self):
        """Immediately de-energise (e-stop / watchdog / cooldown)."""
        self._pin.value(self._off_level)
        self._is_on = False
        self._on_ms = 0
        self._duty = 0.0
        self._active_since = None

    # ---- periodic service -------------------------------------------------
    def service(self, now=None, max_burst_ms=None, cooldown_ms=0):
        """Advance this channel's PWM phase. Call as often as possible.

        Returns True if the relay is currently energised.
        """
        if now is None:
            now = ticks_ms()
        self._last_service = now

        # --- forced cooldown after a long continuous burst -----------------
        if self._cooling_until is not None:
            if ticks_diff(self._cooling_until, now) > 0:
                if self._is_on:
                    self._pin.value(self._off_level)
                    self._is_on = False
                return False
            # Cooldown finished; allow stimulation again.
            self._cooling_until = None
            self._active_since = None

        # --- burst limiting -------------------------------------------------
        if self._duty > 0.0:
            if self._active_since is None:
                self._active_since = now
            elif max_burst_ms and ticks_diff(now, self._active_since) >= max_burst_ms:
                # Sustained too long -> force a rest period.
                self._pin.value(self._off_level)
                self._is_on = False
                self._on_ms = 0
                self._cooling_until = now + cooldown_ms
                return False
        else:
            self._active_since = None

        # --- PWM phase ------------------------------------------------------
        elapsed = ticks_diff(now, self._cycle_start)
        if elapsed >= self._period_ms:
            self._cycle_start = now
            elapsed = 0
            self._on_ms = int(self._duty * self._period_ms)
            # A pulse shorter than the relay can cleanly complete is dropped:
            # half-actuated contacts buzz without delivering useful force.
            if self._on_ms < self._min_pulse_ms:
                self._on_ms = 0

        should_be_on = (self._on_ms > 0) and (elapsed < self._on_ms)
        if should_be_on != self._is_on:
            self._pin.value(self._on_level if should_be_on else self._off_level)
            self._is_on = should_be_on

        return self._is_on

    # ---- introspection ----------------------------------------------------
    def is_on(self):
        return self._is_on

    def duty(self):
        return self._duty

    def cooling(self):
        return self._cooling_until is not None

    def state(self):
        return {
            "name": self.name,
            "gpio": self.gpio,
            "duty": round(self._duty, 3),
            "on": self._is_on,
            "cooling": self.cooling(),
        }
