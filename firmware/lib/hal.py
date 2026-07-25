# lib/hal.py - hardware abstraction layer
#
# WHY: the same driver + safety code must run in two places:
#   1. on the ESP32-S3 under MicroPython (real machine.Pin)
#   2. on a PC under CPython in SIMULATION, so the control logic and the
#      safety layer can be tested with no board and no TENS unit attached.
#
# Pattern carried over from the previous project - it paid for itself there and
# it matters more now, because this build drives a person.

try:
    from machine import Pin as _MachinePin  # noqa: F401
    import time as _time

    IS_SIMULATION = False
    Pin = _MachinePin

    def sleep_ms(ms):
        _time.sleep_ms(ms)

    def ticks_ms():
        return _time.ticks_ms()

    def ticks_diff(a, b):
        return _time.ticks_diff(a, b)

except ImportError:
    # ---- CPython simulation fallback --------------------------------------
    import time as _time

    IS_SIMULATION = True

    class Pin:
        """Minimal machine.Pin stand-in that records state."""
        IN = "IN"
        OUT = "OUT"
        PULL_UP = "PULL_UP"
        PULL_DOWN = "PULL_DOWN"

        _log_enabled = False   # noisy at PWM rates; enable for debugging
        history = []           # (ticks_ms, gpio, value) for tests

        def __init__(self, num, mode=None, value=0, pull=None):
            self._num = num
            self._mode = mode
            self._value = value

        def value(self, v=None):
            if v is None:
                return self._value
            if v != self._value:
                self._value = v
                Pin.history.append((ticks_ms(), self._num, v))
                if Pin._log_enabled:
                    print("[SIM] Pin(%s) -> %s" % (self._num, v))

        def on(self):
            self.value(1)

        def off(self):
            self.value(0)

    def sleep_ms(ms):
        _time.sleep(ms / 1000.0)

    def ticks_ms():
        return int(_time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


def platform_name():
    return "simulation (CPython)" if IS_SIMULATION else "MicroPython (ESP32-S3)"
