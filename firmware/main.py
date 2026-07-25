# main.py - firmware entrypoint: the real-time actuator + safety loop.
#
# ROLE SPLIT (important):
#   PC   -> pose ingest, kinematics, PI control  ("what should the arm do")
#   ESP32-> PWM actuation + INDEPENDENT SAFETY    ("what is physically allowed")
#
# The board never trusts the PC. If commands stop, are malformed, or ask for
# too much, this loop clamps or opens every relay on its own.

from lib.hal import ticks_ms, ticks_diff, sleep_ms, platform_name
from lib.safety import SafetySupervisor
from lib.stim_array import StimArray
from lib import net_udp
from config import settings as S
from config import pins as P

try:
    from machine import Pin as _MPin
    _REAL_HW = True
except ImportError:
    _REAL_HW = False


def attach_estop(array, safety):
    """Wire the physical e-stop button. Returns the Pin, or None.

    WIRING: a normally-CLOSED pushbutton between GPIO and GND, using the
    internal pull-up.
        at rest   -> button closed -> pin pulled to GND      -> LOW  (running)
        pressed   -> button opens  -> pull-up takes it HIGH  -> HIGH (kill)
        wire cut  -> nothing holds it low -> pull-up -> HIGH -> HIGH (kill)

    That last line is the point of choosing normally-closed: a broken or
    unplugged e-stop lead trips the kill instead of silently disarming the
    safeguard. A momentary spring-return button is fine because safety.kill()
    LATCHES in software - it stays killed until an explicit re-arm.

    This is the only kill that works with the PC unplugged.
    """
    if not _REAL_HW or P.ESTOP_PIN is None:
        return None

    pin = _MPin(P.ESTOP_PIN, _MPin.IN, _MPin.PULL_UP)

    # NO INTERRUPT HANDLER. An edge-triggered IRQ fires on a spike lasting
    # microseconds, so a single burst of coupled noise from a switching relay
    # coil latched the kill and stopped the session - with the button never
    # touched. Debouncing the polled check did not help, because the IRQ
    # bypassed it entirely.
    #
    # The polled check in the main loop runs roughly every millisecond and
    # requires ESTOP_DEBOUNCE_SAMPLES consecutive "open" reads, which rejects
    # that noise. ~10 ms to react is irrelevant for a human-operated e-stop
    # (reaction time is >200 ms), and the subject's in-line physical switch
    # remains the primary safeguard regardless.
    print("[main] hardware e-stop armed on GPIO%d (debounced polling, %d samples)"
          % (P.ESTOP_PIN, S.ESTOP_DEBOUNCE_SAMPLES))

    # An edge-triggered IRQ only catches a TRANSITION. If the button is already
    # pressed at power-on, or the lead is broken before boot, no edge ever
    # happens and the fault goes unnoticed. So check the level once here, and
    # keep polling it in the main loop.
    if pin.value():
        safety.kill("hardware_estop_at_boot")
        array.all_off()
        print("[main] E-STOP ACTIVE AT BOOT (pressed, or lead disconnected)")
        print("[main]   -> stimulation blocked until released AND re-armed")
    return pin


def get_network():
    """Reuse the station connection boot.py already made, or fall back to AP.

    Reads lib/netstate rather than importing boot: MicroPython executes boot.py
    at startup WITHOUT registering it in sys.modules, so `import boot` here
    would re-run the whole file - reconnecting Wi-Fi, restarting WebREPL and
    duplicating the boot log.
    """
    wifi = None
    ip = None
    try:
        from lib import netstate
        wifi, ip = netstate.get_network()
    except Exception as exc:
        print("[main] no network state from boot: %s" % exc)
    if ip is None:
        # Hotspot unreachable: come up as our own AP so the demo still runs.
        ip = net_udp.start_ap()
    return wifi, ip


def run():
    print("[main] platform: %s" % platform_name())

    safety = SafetySupervisor(
        duty_max=S.DUTY_MAX,
        command_timeout_ms=S.COMMAND_TIMEOUT_MS,
        max_burst_ms=S.MAX_BURST_MS,
        cooldown_ms=S.COOLDOWN_MS,
    )
    array = StimArray(safety)
    estop_pin = attach_estop(array, safety)

    wifi, ip = get_network()
    token = None
    try:
        from config import device_secrets as _SEC
        token = getattr(_SEC, "CONTROL_TOKEN", None)
    except ImportError:
        pass
    link = net_udp.CommandLink(local_ip=ip, token=token)
    print("[main] control token %s" % ("REQUIRED" if token else "DISABLED (open port)"))

    print("[main] firmware %s" % S.FIRMWARE_VERSION)
    print("[main] ready - awaiting commands on %s:%d" % (ip or "0.0.0.0", S.UDP_PORT))
    print("[main] DISARMED at boot; PC must send {\"arm\":true} to enable stim")

    last_status = ticks_ms()
    last_wifi_check = ticks_ms()
    estop_hits = 0          # consecutive "circuit open" samples (debounce)

    while True:
        now = ticks_ms()

        # ---- 0a. e-stop level check (debounced) ---------------------------
        # The IRQ catches the press itself; this catches the states an edge
        # cannot see - already pressed at boot, a lead that comes loose mid-run,
        # or a missed interrupt. HIGH means "circuit open" = pressed OR broken.
        #
        # DEBOUNCED because a kill LATCHES: one noisy sample would otherwise
        # stop the session permanently and look like a random failure. Relay
        # coils switching a few centimetres away are more than capable of
        # inducing that single sample.
        if estop_pin is not None:
            if estop_pin.value():
                estop_hits += 1
                if estop_hits >= S.ESTOP_DEBOUNCE_SAMPLES and not safety.is_killed():
                    safety.kill("hardware_estop")
                    array.all_off()
                    print("[main] E-STOP: circuit open for %d samples -> killed"
                          % estop_hits)
            else:
                estop_hits = 0

        # ---- 0b. keep the link alive --------------------------------------
        # A dropped link stops commands, which trips the watchdog and opens
        # every relay. Reconnecting restores comms but NOT the armed state:
        # the operator must deliberately re-arm.
        if wifi is not None and ticks_diff(now, last_wifi_check) >= 3000:
            last_wifi_check = now
            wifi.ensure()

        # ---- 1. ingest the newest command ---------------------------------
        msg = link.poll()
        if msg is not None:
            if msg.get("kill"):
                safety.kill("remote_estop")
                array.all_off()
            elif msg.get("arm"):
                safety.arm()
            elif msg.get("disarm"):
                safety.disarm("remote_disarm")
                array.all_off()

            # Bench command: fire the TIMER keep-alive relay once, so the
            # auto-off behaviour can be verified without waiting 20 minutes.
            if msg.get("timer_press"):
                array.press_timer_now()

            # Any well-formed packet pets the watchdog, including a pure
            # heartbeat with zero duties.
            safety.note_command()

            duties = msg.get("duty")
            if duties is not None and not safety.is_killed():
                array.apply(duties, grip=bool(msg.get("grip")))

        # ---- 2. advance PWM + enforce safety ------------------------------
        # Runs every pass, command or not: this is what actually opens the
        # relays when the watchdog expires.
        array.service(now)

        # ---- 3. heartbeat back to the PC ----------------------------------
        if ticks_diff(now, last_status) >= S.STATUS_RATE_MS:
            last_status = now
            st = safety.state()
            st["watchdog_expired"] = safety.watchdog_expired(now)
            # Report the raw e-stop level: 1 = circuit OPEN (pressed, or the
            # lead is broken/miswired) and stimulation is blocked. Without this
            # a miswired e-stop looks identical to "the board is ignoring me".
            st["estop"] = estop_pin.value() if estop_pin is not None else None
            st["fw"] = S.FIRMWARE_VERSION
            # Keep-alive press count: if the board misbehaves on a ~5 minute
            # cadence, compare this against when it happened. A jump here at the
            # moment things break points at the TIMER relay drive (GPIO2), not
            # at the control path.
            st["timer_presses"] = array._timer_presses
            st["cocontract_blocks"] = array._cocontraction_blocks
            link.send_status(st)

        # ---- 4. YIELD -----------------------------------------------------
        # Without this the loop is a tight spin that starves everything else on
        # the chip: the Wi-Fi/lwIP stack, the USB CDC serial, and the REPL. The
        # visible symptom is that mpremote connects but Ctrl-C barely responds;
        # the dangerous symptom is a control link that stutters under load.
        #
        # 1 ms is far finer than we need. MIN_PULSE_MS is 25 ms and the PWM
        # period is 150 ms, so 1 ms of timing granularity costs well under 1%
        # of duty resolution while handing the scheduler ~1000 slices/second.
        sleep_ms(S.LOOP_YIELD_MS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[main] interrupted - forcing safe state")
        import boot
        boot.all_off()
