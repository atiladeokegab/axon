# config/pins.py - GPIO assignment for the Axiometa Genesis Mini v1 rev 2
#
# BOARD: ESP32-S3-Mini-1-N4R2  ->  4 MB flash, 2 MB *QUAD* (QSPI) PSRAM.
#
# The PSRAM type is the thing that changed when we moved off the Goouuu
# N16R8. That board had OCTAL PSRAM, which consumes GPIO33-37; this one is
# QUAD, so those pins are electrically free. We still do not use them, because
# the Genesis Mini does not route them to a header and GPIO34 is taken by the
# battery monitor anyway - but it is why the firmware image is different:
#
#   Goouuu N16R8  -> ESP32_GENERIC_S3-SPIRAM_OCT-*.bin
#   Genesis Mini  -> ESP32_GENERIC_S3-*.bin          (standard; auto-detects)
#
# Flashing the OCT image onto this board is the single most likely way to end
# up with a device that enumerates but will not boot. See docs/DEPLOY.md.
#
# ---------------------------------------------------------------------------
# WHAT IS ACTUALLY AVAILABLE
#
# The Genesis Mini exposes 12 GPIO across four AX22 ports. Those 12 are the
# ONLY pins available for this project; everything else on the module is
# either committed to an on-board function or is not brought out.
#
#   Port | IO0 | IO1 | IO2
#   -----+-----+-----+-----
#   P1   |  4  |  3  |  2
#   P2   |  7  |  6  |  5
#   P3   |  9  | 16  | 15
#   P4   |  1  | 17  | 18
#
# We need 10 of the 12: eight channels, the timer keep-alive, and the e-stop.
#
# PORTING NOTE - 9 of the 10 assignments below are UNCHANGED from the Goouuu
# board, because those pins happen to be exactly the ones the Genesis Mini
# brings out. Only the e-stop had to move. So the electrode/relay harness
# rewires almost one-for-one; see docs/WIRING.md for the two-wire delta.
#
# ---------------------------------------------------------------------------
# TRAPS ON THIS BOARD (different from the old one - read before reassigning)
#
#   GPIO21       -> on-board NeoPixel. Used deliberately, see NEOPIXEL_PIN.
#   GPIO45       -> on-board user button (and an ESP32-S3 strapping pin).
#   GPIO8,34,46  -> battery sense / status / enable. GPIO8 was our E-STOP on
#                   the old board - it is NOT free here, hence the remap.
#   GPIO10,11    -> I2C (STEMMA QT connector).
#   GPIO12,13,14 -> SPI bus.
#   GPIO26-32    -> SPI flash. Never use.
#   GPIO0,3,45,46-> ESP32-S3 strapping pins. GPIO3 IS on port P1, so it is
#                   tempting - but it selects the JTAG source at reset, and a
#                   relay module's pull-down on it can change how the board
#                   boots. Left unassigned on purpose.
#   GPIO19,20    -> native USB D-/D+ (the USB-C CDC/JTAG interface).
#   GPIO43,44    -> UART0 TX/RX (serial console).

BOARD = "Axiometa Genesis Mini v1r2 (ESP32-S3-Mini-1-N4R2)"

# Every GPIO the AX22 ports bring out, by port. Used by the self-check below
# to prove each assignment is on a pin that physically exists on a header.
AX22_PORTS = {
    "P1": (4, 3, 2),
    "P2": (7, 6, 5),
    "P3": (9, 16, 15),
    "P4": (1, 17, 18),
}
AX22_PINS = tuple(p for port in AX22_PORTS.values() for p in port)

# --- 8 muscle stimulation channels (PWM-gated relay modules) ---------------
# Two 4-relay modules:
#   Module 1 (TONGLING JQC-3FF-S-Z)  -> CH1..CH4  (TENS unit 1)   ports P1/P2
#   Module 2 (SONGLE SRD-05VDC-SL-C) -> CH5..CH8  (TENS unit 2)   ports P3/P4
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
    "CH1": 4,      # P1.IO0   (unchanged from the Goouuu board)
    "CH2": 5,      # P2.IO2   (unchanged)
    "CH3": 6,      # P2.IO1   (unchanged)
    "CH4": 7,      # P2.IO0   (unchanged)
    "CH5": 15,     # P3.IO2   (unchanged)
    "CH6": 16,     # P3.IO1   (unchanged)
    "CH7": 17,     # P4.IO1   (unchanged)
    "CH8": 18,     # P4.IO2   (unchanged)
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

# --- Constant-current banks (the real electrical constraint) ----------------
# Each AS8016 shares ONE constant-current source across A1/A2, and another
# across B1/B2. Two channels on the same bank are therefore PARALLELED across a
# single source: closing both splits the current by electrode impedance, so
# neither muscle receives its set value, and if one pad lifts the other takes
# the whole current. That is the hazard worth designing around.
#
# Bank-to-bank, and unit-to-unit, the outputs are galvanically isolated. Two
# channels on DIFFERENT banks share nothing, so firing them together is
# electrically unremarkable.
#
# This is derived from how the jacks are patched, not from anatomy - so it must
# be updated if the leads are ever moved. assert_no_conflicts() checks it.
CURRENT_BANKS = (
    ("CH1", "CH2"),      # unit 1, bank A (A1/A2)
    ("CH3", "CH4"),      # unit 1, bank B (B1/B2)
    ("CH5", "CH6"),      # unit 2, bank A (A1/A2)
    ("CH7", "CH8"),      # unit 2, bank B (B1/B2)
)

# Channels with no electrodes attached. A bank whose second channel is unused
# cannot be paralleled, so it needs no further rule.
CHANNEL_UNUSED = ("CH6",)

# WHY THERE IS NO LONGER A JOINT-BASED EXCLUSION.
#
# An earlier version forbade the shoulder and the elbow from firing in the same
# instant. Measured on the simulated arm, that cost roughly 0.9 s on a
# two-joint move - the dominant term in the arm feeling sluggish.
#
# It turned out to be the wrong rule. The leads are patched so that each
# ANTAGONIST PAIR sits on ONE bank, which means every pair sharing a current
# source is already a pair the firmware refuses outright. Biceps and anterior
# deltoid are on isolated banks and share nothing, so blocking them bought no
# electrical safety and simply halved the available force.
#
# The rule that remains is the one the topology actually justifies:
#   * ANTAGONIST_PAIRS  - refuses same-bank co-firing (and joint locking)
#   * MAX_CONCURRENT_*  - bounds how much of the limb can be live at once
#
# IF YOU RE-PATCH THE JACKS, RE-CHECK THIS. Putting biceps and anterior deltoid
# on the same bank would make them paralleled, and nothing above would stop them
# firing together. assert_no_conflicts() enforces the invariant that every bank
# is either an antagonist pair or has at most one channel in use.

# --- Concurrency cap --------------------------------------------------------
# Isolated channels are safe to run together, but "safe" is not "unlimited".
# Three simultaneous paths through one limb means a fast, forceful, whole-arm
# movement that a blindfolded subject cannot anticipate, and triples the charge
# delivered per unit time.
#
# Two lets the common demo motion - elbow flexion plus shoulder flexion - run
# concurrently at full duty, which is what the speed complaint was about, while
# keeping the worst case bounded. With the antagonist rule allowing at most one
# channel per bank, the cap only binds when all three arm joints move at once.
#
# Enforced by TIME-SLICING, not refusal: the extra channels take turns rather
# than being dropped, so a three-joint move still completes.
MAX_CONCURRENT_ARM_CHANNELS = 2

# Grip is exempt from the cap. CH7/CH8 are on their own bank of the second unit,
# the forearm pads are far from the upper-arm pads, and a grasp that let go
# whenever the arm moved would defeat the point of treating the hand as an
# end-effector.
CONCURRENCY_EXEMPT = ("CH7", "CH8")

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
# P1.IO2. Wired across the TIMER button of BOTH AUVON units so one pulse pets
# both. Unchanged from the old board.
#
# NOTE: 3 V coil - drive through a transistor + flyback diode, NOT straight
# from the GPIO. This matters more here than it did before: see the warning on
# TIMER_KEEPALIVE_ENABLED below, and docs/WIRING.md.
TIMER_KEEPALIVE_PIN = 2

# --- Hardware e-stop (input) -----------------------------------------------
# *** MOVED FOR THIS BOARD: was GPIO8, which is battery sense here. ***
#
# GPIO9 = P3.IO0. Chosen over the other two free port pins because:
#   GPIO3 (P1.IO1) is a strapping pin - a normally-closed button holds it LOW,
#         which is exactly the state that changes JTAG source at reset.
#   GPIO1 (P4.IO0) is fine, and is the spare if GPIO9 is ever needed.
#
# A physical normally-closed button to GND, polled with debouncing in main.py.
# Set to None if not fitted.
ESTOP_PIN = 9

# --- On-board NeoPixel status LED ------------------------------------------
# The Goouuu board had no usable indicator; this one does, and it earns its
# place: the single most common confusion in testing was not knowing whether
# the board was armed, disarmed, killed or off the network without attaching a
# terminal. Now it is visible across a room.
#
# See lib/status_led.py for the colour map. Driving it is best-effort and
# never allowed to raise into the control loop.
NEOPIXEL_PIN = 21

# The on-board user button. NOT used as an e-stop, deliberately: GPIO45 is a
# strapping pin (SPI flash voltage select), so a button held during reset can
# change how the board powers its flash. Recorded here so nobody assigns it.
USER_BUTTON_PIN = 45

# --- Driver polarity -------------------------------------------------------
# CRITICAL SAFETY SETTING. Get this wrong and the de-asserted ("safe") state
# energises the relay instead of releasing it. Unchanged by the board swap -
# it is a property of the RELAY MODULES, not of the microcontroller.
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
# RE-VERIFY AFTER THE BOARD SWAP even though the setting has not changed: the
# check is of the wiring you just redid, not of this constant.
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
# *** READ THIS BEFORE RE-ENABLING ON THE NEW BOARD ***
# If GPIO2 drives a BARE relay coil, an HK4100F-DC3V coil pulls ~60-70 mA -
# roughly three times the ESP32-S3's ~20 mA per-pin continuous rating - and an
# unclamped coil kicks back into the pin on release. Sustained over-current on
# a GPIO driver is a plausible way to cook a module, and this fires every five
# minutes. If the previous board ran with a coil wired directly to GPIO2, treat
# that as a prime suspect for the overheating and DO NOT reproduce it: drive
# the coil through a transistor + flyback diode, or through a relay module that
# has its own driver. docs/WIRING.md has the circuit.
TIMER_KEEPALIVE_ENABLED = True

# --- Reserved / unusable pins (documented, never assigned) ------------------
# Board-specific entries are marked; the rest are ESP32-S3 intrinsics.
RESERVED = {
    0: "strapping (BOOT button)",
    3: "strapping (JTAG source select) - on port P1 but do not use",
    8: "BOARD: battery sense",
    10: "BOARD: I2C SDA (STEMMA QT)",
    11: "BOARD: I2C SCL (STEMMA QT)",
    12: "BOARD: SPI", 13: "BOARD: SPI", 14: "BOARD: SPI",
    19: "native USB D-", 20: "native USB D+",
    21: "BOARD: NeoPixel (used by status_led)",
    26: "SPI flash", 27: "SPI flash", 28: "SPI flash", 29: "SPI flash",
    30: "SPI flash", 31: "SPI flash", 32: "SPI flash",
    34: "BOARD: battery status",
    43: "UART0 TX (console)", 44: "UART0 RX (console)",
    45: "BOARD: user button / strapping (SPI voltage)",
    46: "BOARD: battery enable / strapping (boot mode)",
}


def all_output_pins():
    """Every GPIO this firmware drives, for conflict-checking / boot reset."""
    pins = list(CHANNEL_PINS.values())
    pins.append(TIMER_KEEPALIVE_PIN)
    return pins


def all_assigned_pins():
    """Outputs AND inputs.

    The e-stop input was previously excluded from conflict checking, so
    ESTOP_PIN = 8 passed silently - and GPIO8 is the battery sense pin on this
    board. That is exactly the class of mistake a board swap creates, and it
    would have presented as a mysterious hardware fault rather than an error.
    """
    pins = all_output_pins()
    if ESTOP_PIN is not None:
        pins.append(ESTOP_PIN)
    return pins


def assert_no_conflicts():
    """Raise if any assigned pin is duplicated, reserved, or not on a header.

    The third check is new for this board. The Genesis Mini only brings out 12
    GPIO, so an assignment can be perfectly legal for the ESP32-S3 and still be
    unreachable - a pin that exists in the datasheet but on no connector. That
    fails as "the relay never clicks", which is slow to diagnose.
    """
    used = all_assigned_pins()

    dupes = set(p for p in used if used.count(p) > 1)
    if dupes:
        raise ValueError("Duplicate pin assignment: %s" % sorted(dupes))

    clash = set(used) & set(RESERVED.keys())
    if clash:
        raise ValueError(
            "Assigned pin(s) collide with reserved: %s"
            % sorted("GPIO%d (%s)" % (p, RESERVED[p]) for p in clash))

    off_header = set(used) - set(AX22_PINS)
    if off_header:
        raise ValueError(
            "Assigned pin(s) are not on any AX22 port, so nothing can be "
            "wired to them: %s" % sorted(off_header))

    assert_banks_safe()
    return True


def assert_banks_safe():
    """Every constant-current bank must be un-parallelable.

    Two channels on one bank are wired across a single current source. The only
    reason it is safe to drop the old joint-based exclusion is that each bank
    holds an ANTAGONIST PAIR, which the firmware already refuses to co-fire - so
    no two channels sharing a source can ever be closed together.

    That invariant depends on how the leads are patched, which is exactly the
    kind of thing that changes on a bench at 2am without the code being touched.
    So it is checked at boot rather than trusted. A bank is acceptable if it is
    a declared antagonist pair, or if at most one of its channels is in use.
    """
    pairs = set(frozenset(p) for p in ANTAGONIST_PAIRS)
    unused = set(CHANNEL_UNUSED)
    for bank in CURRENT_BANKS:
        live = [c for c in bank if c not in unused]
        if len(live) < 2:
            continue                      # cannot be paralleled
        if frozenset(bank) in pairs:
            continue                      # co-firing already refused
        raise ValueError(
            "Channels %s share one constant-current source but are not an "
            "antagonist pair, so nothing prevents them being closed together. "
            "Either re-patch them onto different banks, or add them to "
            "ANTAGONIST_PAIRS." % (list(bank),))
    return True
