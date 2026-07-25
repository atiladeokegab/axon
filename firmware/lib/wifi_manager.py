# lib/wifi_manager.py - station-mode Wi-Fi with reconnect.
#
# STATION mode (not SoftAP) is deliberate: the board and the control PC both
# join the same hotspot, which means
#   * the board needs no USB cable - power it from the 5 V pin and walk away
#   * code can be deployed wirelessly over WebREPL
#   * the PC keeps its normal network while talking to the board
#
# The trade-off is that the board gets a DHCP address we don't know in advance.
# That is solved by the discovery responder in net_udp.py, so nothing has to be
# hard-coded or read off a serial console.

import time

try:
    import network
    _HAS_NET = True
except ImportError:
    network = None
    _HAS_NET = False


class WifiManager:
    def __init__(self, ssid, password, static_ip=None, hostname="juno-fes"):
        self.ssid = ssid
        self.password = password
        self.static_ip = static_ip
        self.hostname = hostname
        self._wlan = None

    def connect(self, timeout_s=20):
        """Join the network. Returns the IP address, or None."""
        if not _HAS_NET:
            print("[wifi] simulation - no radio")
            return None

        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)

        # A stable hostname makes the board easier to spot in the hotspot's
        # client list, and enables mDNS on firmwares that support it.
        try:
            self._wlan.config(hostname=self.hostname)
        except Exception:
            pass

        if self.static_ip:
            try:
                self._wlan.ifconfig(self.static_ip)
            except Exception as exc:
                print("[wifi] static IP rejected (%s); falling back to DHCP" % exc)

        if not self._wlan.isconnected():
            print("[wifi] connecting to '%s' ..." % self.ssid)
            self._wlan.connect(self.ssid, self.password)
            deadline = time.time() + timeout_s
            while not self._wlan.isconnected():
                if time.time() > deadline:
                    print("[wifi] TIMEOUT joining '%s'" % self.ssid)
                    return None
                time.sleep(0.25)

        ip = self._wlan.ifconfig()[0]
        print("[wifi] connected: %s" % ip)
        return ip

    def is_connected(self):
        if not _HAS_NET or self._wlan is None:
            return False
        try:
            return self._wlan.isconnected()
        except Exception:
            return False

    def ensure(self):
        """Reconnect if the link dropped. Safe to call from the main loop.

        NOTE: a dropped link means commands stop arriving, which trips the
        firmware watchdog and opens every relay. Reconnecting restores the
        link but NOT the armed state - the operator must re-arm deliberately.
        """
        if not _HAS_NET:
            return False
        if self.is_connected():
            return True
        try:
            print("[wifi] link lost - reconnecting")
            self._wlan.connect(self.ssid, self.password)
        except Exception:
            pass
        return False

    def ip(self):
        if not _HAS_NET or self._wlan is None:
            return None
        try:
            return self._wlan.ifconfig()[0]
        except Exception:
            return None


def start_webrepl(password):
    """Enable WebREPL so firmware can be updated with no USB cable attached."""
    try:
        import webrepl
        webrepl.start(password=password)
        print("[webrepl] started - wireless deploy available")
        return True
    except ImportError:
        print("[webrepl] module not present in this firmware build")
    except Exception as exc:
        print("[webrepl] failed to start: %s" % exc)
    return False
