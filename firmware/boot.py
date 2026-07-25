# boot.py - runs before main.py on every reset.
#
# Order matters here:
#   1. FORCE EVERY RELAY SAFE. Before Wi-Fi, before anything. A crash-and-reboot
#      mid-session must never leave a channel stimulating.
#   2. Join Wi-Fi and start WebREPL, so the board is reachable for wireless code
#      updates EVEN IF main.py later crashes. This is what makes it safe to run
#      the board untethered on 5 V with no USB cable attached.

import sys

sys.path.insert(0, "/")

from lib.hal import Pin, platform_name
from config import pins as P


def all_off():
    """Force every driven line to its safe (de-energised) level."""
    off_level = 1 if P.CHANNEL_ACTIVE_LOW else 0
    for name in P.CHANNEL_ORDER:
        Pin(P.CHANNEL_PINS[name], Pin.OUT, value=off_level)
    timer_off = 1 if P.TIMER_ACTIVE_LOW else 0
    Pin(P.TIMER_KEEPALIVE_PIN, Pin.OUT, value=timer_off)


print("[boot] platform: %s" % platform_name())
P.assert_no_conflicts()
all_off()
print("[boot] all channels de-energised (safe state)")

# ---- network (station mode) ------------------------------------------------
WIFI = None
WIFI_IP = None
try:
    from lib.wifi_manager import WifiManager, start_webrepl
    # No fallback import here: 'device_secrets.example.py' is not a valid
    # module name, so the old `from config import device_secrets_example`
    # could never succeed - it just turned a clear error into a confusing one.
    from config import device_secrets as SEC

    WIFI = WifiManager(SEC.WIFI_SSID, SEC.WIFI_PASSWORD,
                       static_ip=getattr(SEC, "STATIC_IP", None))
    WIFI_IP = WIFI.connect()

    # Publish through a real module so main.py can pick it up WITHOUT
    # `import boot`, which would re-execute this whole file (MicroPython runs
    # boot.py without registering it in sys.modules).
    from lib import netstate
    netstate.set_network(WIFI, WIFI_IP)

    if WIFI_IP:
        # Started here, not in main.py, so a crash in main still leaves the
        # board reachable for a wireless fix.
        start_webrepl(getattr(SEC, "WEBREPL_PASSWORD", "juno2026"))
        print("[boot] deploy wirelessly to %s (WebREPL)" % WIFI_IP)
    else:
        print("[boot] Wi-Fi unavailable - main.py will fall back to SoftAP")
except Exception as exc:
    print("[boot] network setup skipped: %s" % exc)
