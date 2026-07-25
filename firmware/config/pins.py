# config/pins.py - GPIO assignment for Goouuu Tech ESP32-S3-N16R8
#
# BOARD: ESP32-S3, 16 MB flash, 8 MB *OCTAL* PSRAM.
# Octal PSRAM is the important part: it consumes GPIO35-37 (and the flash takes
# 26-32), so that whole block is untouchable. Pins below are all clear of it.
#
# Traps on this board:
#   GPIO26-32  -> SPI flash. NEVER use.
#   GPIO33-37  -> OCTAL PSRAM on N16R8 variants. NEVER use.
#   GPIO0      -> strapping (BOOT button).
#   GPIO3,45,46-> strapping (JTAG / boot mode / SPI voltage).
#   GPIO19,20  -> native USB D-/D+ (leave alone if using USB CDC).
#   GPIO43,44  -> UART0 TX/RX (console / REPL).
#   GPIO48     -> onboard RGB LED on most Goouuu S3 boards.
#
# Everything assigned below is a plain, output-capable, general-purpose pin.

# --- 8 muscle stimulation channels (PWM-gated relay modules) ---------------
# Two 4-relay modules:
#   Module 1 (TONGLING JQC-3FF-S-Z)  -> CH1..CH4  (TENS unit 1)
#   Module 2 (SONGLE SRD-05VDC-SL-C) -> CH5..CH8  (TENS unit 2)
#
# Channel -> muscle map (see docs/WIRING.md for electrode placement):
#   CH1 biceps/brachialis   elbow flex        (agonist)
#   CH2 triceps             elbow extend      (antagonist)
#   CH3 anterior deltoid    shoulder flex     (agonist)
#   CH4 posterior deltoid   shoulder extend   (antagonist)
#   CH5 middle deltoid      shoulder abduct   (gravity returns / adducts)
#   CH6 SPARE               unused
#   CH7 finger flexors      grip close
#   CH8 finger extensors    grip release
CHANNEL_PINS = {
    "CH1": 4,
    "CH2": 5,
    "CH3": 6,
    "CH4": 7,
    "CH5": 15,
    "CH6": 16,
    "CH7": 17,
    "CH8": 18,
}

CHANNEL_ORDER = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8"]

# Antagonist pairs: muscles that oppose each other across the same joint.
# Driving both at once locks the joint, wastes current and fatigues the subject
# fast, so the firmware refuses it - see StimArray.apply().
#
# This is enforced HERE, on the board, and not only in the PC-side mapping.
# SAFETY.md states the firmware does not trust the controller; a rule that
# lives only in controller/mapping.py is a rule the board does not enforce.
ANTAGONIST_PAIRS = (
    ("CH1", "CH2"),      # biceps      / triceps          (elbow)
    ("CH3", "CH4"),      # ant deltoid / post deltoid      (shoulder flex)
    ("CH7", "CH8"),      # finger flexors / extensors      (grip)
)

CHANNEL_MUSCLE = {
    "CH1": "biceps/brachialis (elbow flex)",
    "CH2": "triceps (elbow extend)",
    "CH3": "anterior deltoid (shoulder flex)",
    "CH4": "posterior deltoid (shoulder extend)",
    "CH5": "middle deltoid (shoulder abduct)",
    "CH6": "SPARE (unused)",
    "CH7": "finger flexors (grip close)",
    "CH8": "finger extensors (grip release)",
}

# --- Timer keep-alive relay (HK4100F-DC3V-SHG) -----------------------------
# Wired across the TIMER button of BOTH AUVON units so one pulse pets both.
# NOTE: 3 V coil - drive through a transistor + flyback diode, not straight
# from the GPIO (see docs/WIRING.md).
TIMER_KEEPALIVE_PIN = 2

# --- Optional hardware e-stop (input) --------------------------------------
# A physical normally-closed button to GND. Opens all relays via interrupt,
# independent of the PC. Set to None if not fitted.
ESTOP_PIN = 8

# --- Driver polarity -------------------------------------------------------
# CRITICAL SAFETY SETTING. Get this wrong and the de-asserted ("safe") state
# energises the relay instead of releasing it.
#
# The relay is wired COM -> NO = electrodes on the SUBJECT, COM -> NC = the
# ~1k dummy resistor (see docs/WIRING.md). A de-energised relay rests on NC.
# So the safe, idle state must be a relay that is NOT energised.
#
#   CHANNEL_ACTIVE_LOW = False  ->  idle drives GPIO LOW  (relay released, NC,
#                                    current through the resistor)   <-- OURS
#   CHANNEL_ACTIVE_LOW = True   ->  idle drives GPIO HIGH
#
# Our modules are HIGH-level trigger: GPIO HIGH energises. With ACTIVE_LOW=True
# the idle level was HIGH, which ENERGISED every relay and connected the
# SUBJECT to a live TENS output at boot, on watchdog expiry, on e-stop and
# between every PWM pulse - the exact inverse of the intended fail-safe, and
# with the dummy load never in circuit.
#
# HOW TO VERIFY (do this before every session - docs/TESTING.md 1.3):
#   at boot, meter COM-NO on each channel = OPEN, COM-NC = CLOSED.
# If COM-NO reads closed at boot, this flag is wrong for your modules.
CHANNEL_ACTIVE_LOW = False

# Same question for the TIMER keep-alive relay, driven from GPIO2. Idle must
# leave the coil DE-ENERGISED, i.e. the AUVON's TIMER button NOT held down.
# Driven directly from a GPIO, HIGH energises -> idle must be LOW.
# Verify: at boot the timer relay must be silent and the button unpressed.
TIMER_ACTIVE_LOW = False

# --- Timing ----------------------------------------------------------------
# Button press duration that the AS8016 reliably registers (from the previous
# project: 150-350 ms works, 250 ms is solid).
TIMER_PRESS_MS = 250
# How often to pet the auto-off timer. Device auto-off is 20 min; 5 min is
# comfortably inside it.
TIMER_KEEPALIVE_INTERVAL_MS = 5 * 60 * 1000

# Master switch for the automatic keep-alive.
#
# SET THIS False WHILE BENCH-TESTING WITHOUT THE TENS UNITS CONNECTED. There is
# nothing to keep awake, and it removes a periodic current spike from the
# picture while you are debugging something else.
#
# It also matters if GPIO2 drives a BARE relay coil: an HK4100F-DC3V coil pulls
# ~60-70 mA, well past the ESP32-S3's ~20 mA per-pin rating, and an unclamped
# coil kicks back into the pin on release. Symptom is the board misbehaving or
# resetting on a ~5 minute cadence - exactly the keep-alive interval. Drive the
# coil through a transistor + flyback diode (or a relay module with its own
# driver) before enabling this.
TIMER_KEEPALIVE_ENABLED = True

# --- Reserved / unusable pins (documented, never assigned) ------------------
RESERVED = {
    0: "strapping (BOOT button)",
    3: "strapping (JTAG source select)",
    19: "native USB D-", 20: "native USB D+",
    26: "SPI flash", 27: "SPI flash", 28: "SPI flash", 29: "SPI flash",
    30: "SPI flash", 31: "SPI flash", 32: "SPI flash",
    33: "octal PSRAM", 34: "octal PSRAM", 35: "octal PSRAM",
    36: "octal PSRAM", 37: "octal PSRAM",
    43: "UART0 TX (console)", 44: "UART0 RX (console)",
    45: "strapping (SPI voltage)", 46: "strapping (boot mode)",
    48: "onboard RGB LED",
}


def all_output_pins():
    """Every GPIO this firmware drives, for conflict-checking / boot reset."""
    pins = list(CHANNEL_PINS.values())
    pins.append(TIMER_KEEPALIVE_PIN)
    return pins


def assert_no_conflicts():
    """Raise if any assigned pin collides with a reserved pin or is duplicated."""
    used = all_output_pins()
    dupes = set(p for p in used if used.count(p) > 1)
    if dupes:
        raise ValueError("Duplicate pin assignment: %s" % sorted(dupes))
    clash = set(used) & set(RESERVED.keys())
    if clash:
        raise ValueError("Assigned pin(s) collide with reserved: %s"
                         % sorted(clash))
    return True
