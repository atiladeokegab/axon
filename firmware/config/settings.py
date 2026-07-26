# config/settings.py - tunable control + safety constants.
#
# SAFETY VALUES ARE NOT PERFORMANCE KNOBS. The controller on the PC may ask for
# anything; this firmware clamps it. Changing these changes what the hardware
# will physically do to a person - review deliberately.

# ---- Build marker ---------------------------------------------------------
# Bump this whenever you change firmware. It is printed at boot AND returned in
# every status heartbeat, so "is the board actually running my new code?" is a
# one-second question instead of a guess. Deploys that silently fail, or a
# reboot that never happened, are otherwise invisible.
#
# The board name is IN the version string on purpose. The two boards take
# different MicroPython images and different pin maps, and mixing them up
# presents as silent hardware faults rather than errors - a channel that never
# clicks, or an e-stop wired to the battery monitor. Now the heartbeat says
# which board's firmware is actually running.
FIRMWARE_VERSION = "2026-07-26.11-bank-concurrency"

# ---- On-board status LED --------------------------------------------------
# Genesis Mini only: NeoPixel on GPIO21 (see lib/status_led.py for the colour
# map). Set False to keep the board dark - it changes nothing else, the LED is
# never read by any control or safety decision.
#
# NOT A SAFETY INDICATOR. A colour is a convenience for the operator; it is not
# permission to touch anyone. The authoritative checks remain the subject's
# physical kill switch and a meter on the relay contacts (docs/SAFETY.md).
STATUS_LED_ENABLED = True

# ---- Per-channel duty ceilings --------------------------------------------
# DUTY_MAX is the ceiling for everything not listed here. The grip channels are
# the one deliberate exception.
#
# WHY GRIP IS DIFFERENT. Every other channel is servoed: the controller
# modulates duty to place a joint, so duty is the force knob and 0.70 keeps a
# margin. Grip is TRIGGERED, treated as an end-effector with two states, and a
# partial grasp is not a useful state - it is a hand that drops the object. So
# CH7/CH8 are allowed a continuous 1.0.
#
# WHAT THIS DOES NOT RELAX. MAX_BURST_MS (4 s) and COOLDOWN_MS (2 s) still
# apply, so a held grasp releases after 4 seconds and needs 2 seconds of rest.
# That is a real limit on the demo: you cannot hold an object indefinitely.
# It is left in place because the finger flexors are small muscles on a lower
# intensity bank and fatigue fastest of anything on the arm.
#
# Side effect worth having: at duty 1.0 the relay stops switching entirely, so
# the capacitive turn-on jolt that the dummy load exists to absorb simply does
# not recur while the grip is held.
CHANNEL_DUTY_MAX = {
    "CH7": 1.0,      # finger flexors  - grip close
    "CH8": 1.0,      # finger extensors - grip release
}

# ---- Software PWM ---------------------------------------------------------
# Mechanical relays: ~10 ms close / ~5 ms open, clean repeatable ~20-30 ms.
# A 150 ms period gives ~6.7 Hz carrier and ~6 usable duty steps. The limb's
# inertia low-passes this into acceptably smooth POSITION even though FORCE
# ripples. (A PhotoMOS/SSR would allow 20-50 Hz and true smooth force.)
PWM_PERIOD_MS = 150
# Shorter than this and the relay cannot complete a clean transition, so the
# pulse is dropped entirely rather than half-actuating the contacts.
MIN_PULSE_MS = 25

# Sleep at the end of every main-loop pass. MUST be non-zero: a tight spin
# starves the Wi-Fi stack, the USB CDC serial and the REPL on this chip (the
# symptom is mpremote connecting but Ctrl-C barely responding). 1 ms still
# gives ~1000 service passes/second, which is far more than the 25 ms minimum
# pulse needs.
LOOP_YIELD_MS = 1

# ---- E-stop debounce ------------------------------------------------------
# The polled e-stop must see this many CONSECUTIVE "circuit open" samples
# before it latches a kill. The loop runs roughly every LOOP_YIELD_MS, so 10
# samples is ~10 ms - instant to a human, but long enough to reject contact
# bounce and the electrical noise that relay coils inject into nearby wiring.
#
# Without this a single spurious sample latches a permanent kill, and the
# symptom is "stimulation randomly stops and will not restart", which is
# extremely confusing to debug. The hardware IRQ is unaffected and still fires
# immediately on a real press.
ESTOP_DEBOUNCE_SAMPLES = 40

# 40 samples is ~40 ms of CONTINUOUSLY open circuit. Raised from 10 after a
# real false trip: relay coils switching near the e-stop lead coupled enough
# noise to satisfy a 10 ms window, killing a session with the button untouched.
#
# 40 ms is still imperceptible for a human-operated stop (reaction time is
# >200 ms) and a genuine press is held far longer. If false trips persist, fix
# it in HARDWARE - see docs/WIRING.md - rather than raising this further:
# beyond ~100 ms you are trading real safety margin for noise immunity.

# ---- Safety envelope (enforced here, independent of the PC controller) ----
# Duty ceiling: the muscle must always get rest within every PWM period.
DUTY_MAX = 0.70
# Longest a channel may stay continuously energised before a forced rest.
MAX_BURST_MS = 4000
# Rest enforced after MAX_BURST_MS of sustained stimulation on a channel.
COOLDOWN_MS = 2000
# No fresh command from the PC within this window -> open every relay.
# This is the backstop for a crashed app, an unplugged cable, or lost Wi-Fi.
COMMAND_TIMEOUT_MS = 500

# ---- Network --------------------------------------------------------------
# Raw UDP, as in the previous project: no TCP/MQTT handshakes, lowest latency,
# and packet loss is harmless because commands are sent continuously and a gap
# simply trips the watchdog (fail-safe).
UDP_PORT = 8080
COMMAND_RATE_HZ = 30          # expected PC -> ESP32 rate
STATUS_RATE_MS = 200          # ESP32 -> PC heartbeat interval

# ---- Wi-Fi ----------------------------------------------------------------
# SoftAP so the demo never depends on venue Wi-Fi.
AP_SSID = "juno-fes"
AP_PASSWORD = "juno12345"     # >= 8 chars required by the ESP32 AP stack
AP_IP = "192.168.4.1"
