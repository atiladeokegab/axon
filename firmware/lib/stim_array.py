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

        # ---- mutually exclusive group scheduling --------------------------
        # Commanded duties are kept here; what actually reaches the relays is
        # whatever the current time slice permits. Keeping the two separate is
        # what lets the controller go on asking for shoulder AND elbow at once
        # without either the controller or the operator having to know that the
        # board is interleaving them.
        self._exempt = tuple(getattr(P, "CONCURRENCY_EXEMPT", ()))
        self._max_concurrent = int(getattr(P, "MAX_CONCURRENT_ARM_CHANNELS", 99))
        self._wanted = {name: 0.0 for name in P.CHANNEL_ORDER}
        self._slot = 0                   # which rotation of channels is live
        self._slot_started = ticks_ms()
        self._group_switches = 0

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

        if grip:
            # Power grasp: close the flexors, make sure extensors are released.
            # Never co-contract an antagonist pair.
            #
            # Passed with its channel name so the per-channel ceiling applies:
            # grip is triggered rather than servoed, and a partial grasp is a
            # hand that drops the object, so CH7 is allowed a continuous 1.0
            # where every servoed channel stays at DUTY_MAX. See
            # settings.CHANNEL_DUTY_MAX.
            clamped["CH7"] = self.safety.clamp_duty(
                getattr(S, "CHANNEL_DUTY_MAX", {}).get("CH7", S.DUTY_MAX),
                channel="CH7")
            clamped["CH8"] = 0.0

        # Record what was ASKED FOR. _service_groups decides what is allowed to
        # reach a relay this instant.
        self._wanted = clamped
        self._push_wanted(ticks_ms())
        return True

    # ======================================================================
    # MUTUALLY EXCLUSIVE GROUPS
    # ======================================================================
    def _arm_channels_wanting(self):
        """Non-exempt channels currently asking for current, in fixed order."""
        return [n for n in P.CHANNEL_ORDER
                if n not in self._exempt and self._wanted.get(n, 0.0) > 0.0]

    def _slot_count(self):
        """How many rotations are needed to service everything that asked.

        1 means everything fits under the cap and runs concurrently, which is
        the common case: with the antagonist rule allowing at most one channel
        per current bank, only a simultaneous three-joint move exceeds a cap
        of two.
        """
        want = len(self._arm_channels_wanting())
        if want <= self._max_concurrent or self._max_concurrent <= 0:
            return 1
        # ceil division without importing math
        return (want + self._max_concurrent - 1) // self._max_concurrent

    def _channels_in_slot(self, slot):
        """Which requesting channels are live during `slot`."""
        want = self._arm_channels_wanting()
        n = self._slot_count()
        if n <= 1:
            return want
        # Deal round-robin so a channel is never starved: slot i takes every
        # nth channel starting at i, rather than a contiguous block, which
        # keeps the split stable as channels come and go.
        return [c for k, c in enumerate(want) if k % n == slot % n]

    def _push_wanted(self, now=None, restart=False):
        """Send commanded duties to the channels, masked by the current slot.

        Exempt channels (grip) always pass through. Non-exempt channels pass
        through only if this slot includes them.

        SLOT COMPENSATION. When the cap forces a rotation, a channel owns only
        1/N of the wall clock and would deliver 1/N of the force. The integrator
        cannot recover that - it is already saturated holding the limb against
        gravity - so the in-slot duty is scaled by N. What the ceiling bounds is
        the charge a muscle receives over time, and that is the TIME-AVERAGE:

            average = min(1.0, wanted * N) / N  <=  wanted  <=  DUTY_MAX

        so this can never exceed the average the channel would have received
        without any rotation at all. When N is 1, which is now the usual case,
        the scaling vanishes entirely.
        """
        n = self._slot_count()
        live = set(self._channels_in_slot(self._slot)) if n > 1 else None
        for name in P.CHANNEL_ORDER:
            ch = self.channels[name]
            if name in self._exempt or n <= 1:
                ch.set_duty(self._wanted.get(name, 0.0))
                continue
            if name not in live:
                ch.set_duty(0.0)
                continue
            d = self._wanted.get(name, 0.0) * n
            ch.set_duty(1.0 if d > 1.0 else d)
            if restart:
                # Align this channel's PWM period with the start of the slot it
                # has just been granted, so its pulse lands INSIDE the slot.
                # Guarded by `restart` because apply() also calls this method at
                # 30 Hz, and restarting the period that often would pin the
                # relay closed - see StimChannel.restart_cycle.
                ch.restart_cycle(now)

    def _service_groups(self, now):
        """Advance the rotation when more channels are wanted than the cap.

        Does nothing at all in the common case, so the usual two-joint move runs
        continuously with no interleaving penalty.
        """
        n = self._slot_count()
        if n <= 1:
            if self._slot != 0:
                self._slot = 0
                self._push_wanted(now, restart=True)
            return
        if ticks_diff(now, self._slot_started) >= S.PWM_PERIOD_MS:
            self._slot = (self._slot + 1) % n
            self._slot_started = now
            self._group_switches += 1
            self._push_wanted(now, restart=True)

    def active_group_names(self):
        """Channels currently permitted to draw current. For tests and status."""
        if self._slot_count() <= 1:
            return list(P.CHANNEL_ORDER)
        return list(self._exempt) + self._channels_in_slot(self._slot)

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
            # Rotate the exclusive slot BEFORE servicing, so a channel that has
            # just lost its turn is de-energised in the same pass rather than
            # staying closed for one more period. Doing it after would allow a
            # brief window where both groups are live, which is the exact thing
            # the groups exist to prevent.
            self._service_groups(now)
            for ch in self.channels.values():
                ch.service(now,
                           max_burst_ms=S.MAX_BURST_MS,
                           cooldown_ms=S.COOLDOWN_MS)

        self._service_timer(now)

    def any_on(self):
        """True if any channel relay is currently CLOSED onto the subject.

        Reads the relay state itself, not the commanded duty. During PWM a
        channel spends most of its period open, so a duty-based answer would
        report "stimulating" continuously and the indicator would never show
        the pulsing that makes current flow recognisable.
        """
        for ch in self.channels.values():
            if ch.is_on():
                return True
        return False

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
