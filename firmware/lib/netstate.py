# lib/netstate.py - shared handle for the network brought up in boot.py.
#
# WHY THIS EXISTS: main.py needs the WifiManager that boot.py created, but it
# must NOT do `import boot` to get it. MicroPython *executes* boot.py at
# startup without registering it in sys.modules, so importing it later
# re-executes the entire file - reconnecting Wi-Fi, restarting WebREPL and
# re-printing the whole boot banner. The symptom is a duplicated boot log.
#
# A tiny module both sides import is registered normally, so it is shared
# rather than re-run.

WIFI = None      # lib.wifi_manager.WifiManager instance, or None
WIFI_IP = None   # str IP address, or None if the join failed


def set_network(wifi, ip):
    global WIFI, WIFI_IP
    WIFI = wifi
    WIFI_IP = ip


def get_network():
    return WIFI, WIFI_IP
