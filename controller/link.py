"""UDP link to the ESP32-S3.

Fire-and-forget datagrams at a fixed rate. We never wait for an ack: the
firmware's watchdog means SILENCE IS THE FAIL-SAFE, so a dropped packet
degrades toward "stimulation off", which is exactly where we want to fail.
"""

import json
import socket
import time

import settings as C


def _local_ipv4():
    """The address of the DEFAULT route (may not be the hotspot interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))      # no traffic sent; just picks a route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def local_ipv4_addresses():
    """Every local IPv4 address, so we can sweep EVERY attached network.

    A laptop on a phone hotspot usually also has ethernet, corporate Wi-Fi or a
    VPN attached. Sweeping only the default route searches the wrong subnet and
    silently finds nothing - which is exactly the failure this avoids.
    """
    found = []

    default = _local_ipv4()
    if default:
        found.append(default)

    # Everything the host knows itself by (picks up secondary interfaces).
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass

    # Drop loopback and link-local (169.254.x) - no board will be there.
    return [ip for ip in found
            if not ip.startswith("127.") and not ip.startswith("169.254.")]


# Subnets used by OS "share my connection" hotspots. These live on a SEPARATE
# virtual adapter from the machine's normal connection, and the default route
# still points at the upstream network - so the hotspot subnet is easy to miss
# when enumerating interfaces. Probing them explicitly costs nothing.
#   192.168.137.x  Windows Mobile Hotspot / Internet Connection Sharing
#   192.168.43.x   Android tethering
#   172.20.10.x    iPhone Personal Hotspot
#   192.168.4.x    the board's own fallback SoftAP
WELL_KNOWN_HOTSPOT_SUBNETS = ("192.168.137", "192.168.43", "172.20.10",
                              "192.168.4")


def _listen(sock, deadline, verbose):
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(512)
        except (socket.timeout, OSError):
            return None
        try:
            if json.loads(data.decode()).get("juno"):
                if verbose:
                    print("[link] found board at %s" % addr[0])
                return addr[0]
        except Exception:
            continue
    return None


def discover(port=None, timeout_s=3.0, verbose=True, scan=True):
    """Find the board's IP on the network.

    The board joins the hotspot by DHCP, so its address is not known ahead of
    time. Reading it off a serial console would defeat the point of running
    untethered, so we ask the network instead.

    Two strategies, in order:
      1. UDP broadcast - fast, one packet.
      2. Subnet sweep  - unicast ping to every host in the local /24.
    The sweep exists because mobile hotspots frequently drop broadcast traffic
    or enable client isolation, which makes strategy 1 silently useless.

    Returns an IP string, or None.
    """
    port = port or C.ESP32_PORT
    payload = json.dumps({"discover": True}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.3)

    if verbose:
        print("[link] searching for the board on the network ...")
    try:
        # --- 1. broadcast ---------------------------------------------------
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        deadline = time.monotonic() + min(timeout_s, 1.5)
        while time.monotonic() < deadline:
            for target in ("255.255.255.255", "192.168.4.1"):
                try:
                    sock.sendto(payload, (target, port))
                except OSError:
                    pass          # broadcast unavailable on this interface
            found = _listen(sock, min(deadline, time.monotonic() + 0.4), verbose)
            if found:
                return found

        # --- 2. subnet sweep, across EVERY attached network ------------------
        locals_ = local_ipv4_addresses() if scan else []
        subnets = []
        for ip in locals_:
            base = ip.rsplit(".", 1)[0]
            if base not in subnets:
                subnets.append(base)

        # Always try the standard hotspot ranges too. When THIS machine is the
        # hotspot, the shared network sits on a virtual adapter that interface
        # enumeration frequently misses, while the default route still points
        # at the upstream link.
        if scan:
            for base in WELL_KNOWN_HOTSPOT_SUBNETS:
                if base not in subnets:
                    subnets.append(base)

        if subnets and verbose:
            print("[link] broadcast found nothing; sweeping %s ..."
                  % ", ".join("%s.0/24" % b for b in subnets))

        for base in subnets:
            for host in range(1, 255):
                try:
                    sock.sendto(payload, ("%s.%d" % (base, host), port))
                except OSError:
                    continue
            found = _listen(sock, time.monotonic() + 1.5, verbose)
            if found:
                return found
    finally:
        sock.close()

    if verbose:
        print("[link] no board responded.")
        if locals_:
            print("[link] this PC is on: %s" % ", ".join(locals_))
        print("[link] Check: (a) is the PC joined to the SAME network as the")
        print("[link]        board (the hotspot)?  (b) is the board powered?")
        print("[link] The board prints its IP at boot - pass it directly with")
        print("[link]   --host <board-ip>")
    return None


class EspLink:
    def __init__(self, host=None, port=None, auto_discover=True):
        self.port = port or C.ESP32_PORT
        self.host = host or C.ESP32_HOST
        if self.host is None and auto_discover:
            self.host = discover(self.port) or C.ESP32_HOST_FALLBACK
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._seq = 0
        self._last_status = None
        self._sent = 0

    # ---- send -------------------------------------------------------------
    def _send(self, payload):
        self._seq += 1
        payload["seq"] = self._seq
        tok = getattr(C, "CONTROL_TOKEN", None)
        if tok:
            payload["tok"] = tok
        try:
            self._sock.sendto(json.dumps(payload).encode(), (self.host, self.port))
            self._sent += 1
            return True
        except OSError:
            return False

    def send_duties(self, duty_list, grip=False):
        return self._send({"duty": duty_list, "grip": bool(grip)})

    def arm(self):
        return self._send({"arm": True, "duty": [0.0] * 8})

    def disarm(self):
        return self._send({"disarm": True, "duty": [0.0] * 8})

    def kill(self):
        """Latching e-stop. Sent repeatedly by the caller for good measure."""
        return self._send({"kill": True, "duty": [0.0] * 8})

    # ---- receive ----------------------------------------------------------
    def poll_status(self):
        """Drain any heartbeat packets; return the newest, or None."""
        newest = None
        while True:
            try:
                data, _ = self._sock.recvfrom(512)
            except (OSError, BlockingIOError):
                break
            if not data:
                break
            try:
                newest = json.loads(data.decode())
            except Exception:
                continue
        if newest is not None:
            self._last_status = newest
            self._last_status["_rx_at"] = time.monotonic()
        return newest

    def last_status(self):
        return self._last_status

    def stats(self):
        return {"sent": self._sent, "seq": self._seq}


class NullLink:
    """Stand-in for dry runs with no board attached."""

    def __init__(self):
        self.last_duties = [0.0] * 8
        self.armed = False
        self.killed = False

    def send_duties(self, duty_list, grip=False):
        self.last_duties = duty_list
        return True

    def arm(self):
        self.armed = True
        self.killed = False
        return True

    def disarm(self):
        self.armed = False
        return True

    def kill(self):
        self.killed = True
        self.armed = False
        return True

    def poll_status(self):
        return None

    def last_status(self):
        return {"armed": self.armed, "killed": self.killed, "fault": None}

    def stats(self):
        return {"sent": 0, "seq": 0}
