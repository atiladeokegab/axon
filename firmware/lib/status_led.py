# lib/status_led.py - on-board NeoPixel status indicator (Genesis Mini GPIO21).
#
# WHY THIS EXISTS. On the previous board the only way to know what the firmware
# was doing was to attach a terminal, and repeatedly during bring-up the same
# question cost minutes: is it armed, is it killed, did it drop off the
# network, did it reboot? Every one of those looks identical from across a
# bench - and identical to a subject or a judge watching a demo.
#
# The Genesis Mini has an addressable RGB LED on GPIO21, so that state is now
# visible from across a room.
#
# DESIGN RULES, all of them safety-motivated:
#
#   1. THE LED NEVER GATES ANYTHING. Nothing in this module can raise into the
#      control loop, and no control decision reads it. A failed LED must not be
#      able to stop stimulation OR to allow it. Every public call is wrapped.
#
#   2. IT IS NOT A SAFETY INDICATOR. A green LED is not permission to touch
#      anyone. The authoritative signals remain the subject's physical kill
#      switch and a meter on the relay contacts. This is a convenience, and
#      docs/SAFETY.md says so explicitly.
#
#   3. IT NEVER BLOCKS. No sleeps, no animation loops. Blinking is done by
#      sampling a clock inside tick(), which the caller invokes from the main
#      loop, so a 1 ms loop stays a 1 ms loop.
#
# COLOUR MAP - chosen so the DANGEROUS state is the one that stands out:
#
#   off / dim blue   booting, no network yet
#   blue             Wi-Fi connected, DISARMED (safe, idle)
#   yellow           armed, but no stimulation currently flowing
#   RED, pulsing     STIMULATING - current is flowing to the subject
#   magenta, fast    killed / e-stopped (latched; needs a deliberate re-arm)
#   orange           armed but the link has gone quiet (watchdog about to trip)
#
# Red for "current flowing" is deliberate. The instinct is to use green for
# "working", but the state a bystander most needs to recognise instantly is the
# one where a person is being stimulated.

try:
    from machine import Pin
    import neopixel
    _HAVE_NEOPIXEL = True
except ImportError:                     # desktop / test environment
    _HAVE_NEOPIXEL = False

from lib.hal import ticks_ms, ticks_diff

# (r, g, b) at low brightness. These are driven straight out, so keep them
# dim: a NeoPixel at full white is genuinely painful to look at and, at ~60 mA,
# is a non-trivial load on a battery-powered board.
BOOTING = (0, 0, 8)
IDLE_DISARMED = (0, 0, 40)
ARMED_QUIET = (40, 30, 0)
STIMULATING = (60, 0, 0)
KILLED = (50, 0, 50)
LINK_LOST = (60, 20, 0)
OFF = (0, 0, 0)


class StatusLED:
    """Best-effort status indicator. Never raises, never blocks."""

    def __init__(self, pin_num, enabled=True):
        self.enabled = bool(enabled) and _HAVE_NEOPIXEL
        self._np = None
        self._colour = OFF
        self._blink_ms = 0          # 0 = steady
        self._phase_at = ticks_ms()
        self._on_phase = True
        self._last_written = None

        if not self.enabled:
            return
        try:
            self._np = neopixel.NeoPixel(Pin(pin_num, Pin.OUT), 1)
            self._write(BOOTING)
        except Exception as exc:
            # A missing or miswired LED must not stop the firmware booting.
            print("[led] disabled (%s)" % exc)
            self.enabled = False
            self._np = None

    # ---- internals --------------------------------------------------------
    def _write(self, rgb):
        if self._np is None or rgb == self._last_written:
            return                                  # skip redundant bit-banging
        try:
            self._np[0] = rgb
            self._np.write()
            self._last_written = rgb
        except Exception:
            # Deliberately silent: this runs in the control loop and a per-pass
            # print on a broken LED would flood the console and slow the loop,
            # which is a real problem where a dark LED is not.
            self._np = None
            self.enabled = False

    # ---- public API -------------------------------------------------------
    def set(self, colour, blink_ms=0):
        """Set the target colour. blink_ms=0 is steady, otherwise half-period."""
        self._colour = colour
        self._blink_ms = blink_ms

    def from_state(self, armed, killed, stimulating, link_ok=True):
        """Map firmware state to a colour. The only mapping in the codebase."""
        if killed:
            self.set(KILLED, blink_ms=150)          # fast: demands attention
        elif not armed:
            self.set(IDLE_DISARMED)
        elif not link_ok:
            self.set(LINK_LOST, blink_ms=400)
        elif stimulating:
            self.set(STIMULATING, blink_ms=250)     # pulses while current flows
        else:
            self.set(ARMED_QUIET)

    def tick(self, now=None):
        """Advance any blink. Call once per main-loop pass; costs ~nothing."""
        if not self.enabled or self._np is None:
            return
        if self._blink_ms <= 0:
            self._write(self._colour)
            return
        now = ticks_ms() if now is None else now
        if ticks_diff(now, self._phase_at) >= self._blink_ms:
            self._phase_at = now
            self._on_phase = not self._on_phase
        self._write(self._colour if self._on_phase else OFF)

    def off(self):
        self._blink_ms = 0
        self._colour = OFF
        self._write(OFF)
