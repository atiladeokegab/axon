# config/device_secrets.example.py
#
# COPY THIS TO device_secrets.py AND FILL IN YOUR OWN VALUES.
# device_secrets.py is gitignored - never commit real credentials.

# Wi-Fi network the board joins (station mode).
WIFI_SSID = "your-network-name"
WIFI_PASSWORD = "your-network-password"

# WebREPL password for wireless code deployment.
# MicroPython requires 4-9 characters.
WEBREPL_PASSWORD = "changeme"

# Optional: pin the board to a fixed IP so you never have to discover it.
# Leave as None to use DHCP (auto-discovery will find the board anyway).
#   STATIC_IP = ("192.168.43.50", "255.255.255.0", "192.168.43.1", "8.8.8.8")
STATIC_IP = None
