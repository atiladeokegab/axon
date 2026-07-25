# lib/net_udp.py - raw UDP command link + SoftAP.
#
# WHY UDP AND NOT TCP/HTTP: this is a real-time control link. We want the
# newest command, not a guaranteed-delivery replay of stale ones. Dropped
# packets are harmless because the PC streams continuously at ~30 Hz; a real
# outage simply stops the stream, which trips the watchdog and opens the
# relays. Loss IS the safety signal.
#
# WHY SOFTAP: the demo must not depend on venue Wi-Fi.

import json

try:
    import socket
    import network
    _HAS_NET = True
except ImportError:          # CPython simulation
    import socket
    network = None
    _HAS_NET = False

from lib.hal import ticks_ms
from config import settings as S


def start_ap(ssid=None, password=None):
    """Fallback SoftAP, used only if the hotspot is unavailable.

    Normal operation is STATION mode (see lib/wifi_manager.py) so the board can
    run untethered on 5 V while code is deployed over Wi-Fi.
    """
    if not _HAS_NET or network is None:
        print("[net] simulation: no AP started")
        return None
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ssid or S.AP_SSID, password=password or S.AP_PASSWORD)
    while not ap.active():
        pass
    ip = ap.ifconfig()[0]
    print("[net] fallback SoftAP '%s' up at %s" % (ssid or S.AP_SSID, ip))
    return ip


class CommandLink:
    """Non-blocking UDP receiver for control packets + status replies.

    Expected inbound JSON (PC -> ESP32):
        {"duty":[0..1 x8], "grip":bool, "kill":bool, "arm":bool, "seq":int}

    Outbound status (ESP32 -> PC), sent to the last peer:
        {"state":..., "fault":..., "last_seq":..., "uptime_ms":...}
    """

    def __init__(self, port=None, local_ip=None, token=None):
        self.port = port or S.UDP_PORT
        self.local_ip = local_ip
        # Shared token required on control packets. Any host on the hotspot can
        # otherwise send {"arm":true,...} and stimulate the subject. Not real
        # authentication - it stops accidents and casual interference.
        self.token = token
        self._rejected = 0
        self._peer = None
        self._last_seq = -1
        self._rx_count = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind(("0.0.0.0", self.port))
        print("[net] UDP listening on :%d" % self.port)

    def poll(self):
        """Return a merged command dict, or None. Never blocks.

        Two different merge rules, and the distinction is safety-critical:

        * DUTY / GRIP use the NEWEST packet only. Acting on a backlog of stale
          targets would be laggy and would drive the limb toward positions the
          operator has already moved on from.

        * CONTROL FLAGS (kill / arm / disarm / timer_press) are OR-ed across
          EVERY packet in the batch. An earlier version returned only the
          newest packet, which meant a 'kill' arriving in the same drain window
          as a routine duty update was silently discarded. Dropping an e-stop
          because a duty packet landed 1 ms later is exactly the failure mode
          this system must not have.

        * KILL WINS. If any packet in the batch carries kill, the merged result
          carries kill, regardless of what arrived afterwards.

        Also answers discovery pings, which is how the controller finds this
        board's DHCP address without anyone reading it off a serial console.
        """
        newest = None
        flags = {}
        while True:
            try:
                data, addr = self._sock.recvfrom(512)
            except Exception:
                break
            if not data:
                break
            try:
                msg = json.loads(data.decode() if isinstance(data, bytes) else data)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            # --- discovery ping: reply and DO NOT treat as a command --------
            # Deliberately does not pet the watchdog: a discovery broadcast is
            # not evidence that a controller is actively driving the arm.
            if msg.get("discover"):
                self._reply_discovery(addr)
                continue

            # Reject anything without the shared token BEFORE it can latch a
            # control flag or set a duty. Discovery (handled above) stays open
            # so the board can still be found.
            if self.token and msg.get("tok") != self.token:
                self._rejected += 1
                continue

            self._peer = addr

            # Latch control flags from EVERY packet before any seq filtering,
            # so an e-stop is never lost to reordering or to a newer packet.
            for key in ("kill", "arm", "disarm", "timer_press"):
                if msg.get(key):
                    flags[key] = True

            seq = msg.get("seq")
            if isinstance(seq, int):
                if seq < self._last_seq:
                    # A BACKWARD jump has two very different causes:
                    #
                    #  * a restarted controller - every tool begins counting at
                    #    1 again, so switching from bench.py to run.py looks
                    #    like a huge step backwards. This MUST be accepted, or
                    #    the new client is silently ignored for hundreds of
                    #    packets while its counter climbs back. Symptom: the
                    #    board arms (flags are latched above) but no duty ever
                    #    takes effect.
                    #
                    #  * genuine UDP reordering - only ever a packet or two out
                    #    of order, and worth dropping so a stale target cannot
                    #    briefly overwrite a fresher one.
                    #
                    # Distinguish by size of the jump.
                    if (self._last_seq - seq) > 5:
                        self._last_seq = seq        # new client: resync
                    else:
                        continue                    # small reorder: drop
                else:
                    self._last_seq = seq
            newest = msg
            self._rx_count += 1

        if newest is None and not flags:
            return None

        merged = dict(newest) if newest else {}
        merged.update(flags)
        # Kill outranks arm even if the arm packet arrived later in the batch.
        if flags.get("kill"):
            merged["arm"] = False
            merged["kill"] = True
        return merged

    def _reply_discovery(self, addr):
        try:
            self._sock.sendto(json.dumps({
                "juno": True,
                "role": "fes-controller",
                "port": self.port,
                "ip": self.local_ip,
            }).encode(), addr)
        except Exception:
            pass

    def send_status(self, payload):
        if self._peer is None:
            return False
        try:
            payload = dict(payload)
            payload["last_seq"] = self._last_seq
            payload["uptime_ms"] = ticks_ms()
            self._sock.sendto(json.dumps(payload).encode(), self._peer)
            return True
        except Exception:
            return False

    def stats(self):
        return {"rx": self._rx_count, "last_seq": self._last_seq,
                "peer": str(self._peer)}
