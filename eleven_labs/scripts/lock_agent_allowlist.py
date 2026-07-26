#!/usr/bin/env python3
"""
Restrict the conversational agent to the hosts we actually serve from.

A public agent with no allowlist can be used by anyone holding its id, and the
conversation minutes bill to us. This closes that without turning on full
authentication, which the browser client cannot do.

Needs ELEVENLABS_API_KEY with convai_read and convai_write — the TTS key used
by generate_voice.py is not enough and will fail with a clear 401.

    uv run python scripts/lock_agent_allowlist.py            # apply and verify
    uv run python scripts/lock_agent_allowlist.py --check    # verify only

--check exit codes: 0 all expected hosts present, 2 allowlist empty (agent is
open to anyone with its id), 3 one or more expected hosts missing.
"""
import argparse
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

AGENT_ID = os.environ.get("AXON_AGENT_ID", "agent_5601kydjajk9fnqtvwyn2ed4tmnd")
BASE = "https://api.elevenlabs.io/v1/convai/agents"

# Ports the UI is served on. Every host below is allowlisted on each of these.
PORTS = (8080, 8000)

# Hosts that are always correct regardless of network.
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def lan_ips():
    """This machine's private IPv4 addresses, for cross-device browsing.

    The allowlist gates the browser's Origin header. When the UI is opened from
    the laptop itself the origin is localhost and none of this matters; these
    entries exist for the case where the Jetson or a phone on the hotspot browses
    to the laptop by IP.

    Detected rather than hardcoded because the hotspot subnet is not stable
    across venues, and a stale entry fails as an auth error in the browser —
    which looks nothing like the networking problem it actually is.

    AXON_LAN_IP overrides detection entirely (comma-separated for several):
        set AXON_LAN_IP=192.168.137.1     (Windows)
        export AXON_LAN_IP=192.168.137.1  (shell)
    """
    override = os.environ.get("AXON_LAN_IP", "").strip()
    if override:
        return [ip.strip() for ip in override.split(",") if ip.strip()]

    found = set()
    try:
        # Returns every adapter address on Windows, which is what we want: the
        # hotspot adapter is not necessarily the default route, so the usual
        # connect-to-8.8.8.8 trick would report the wrong interface.
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    private = []
    for raw in found:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        # Public addresses would widen the allowlist beyond the local network,
        # and loopback is already covered by LOCAL_HOSTS.
        if addr.is_private and not addr.is_loopback and not addr.is_link_local:
            private.append(raw)
    return sorted(private)


def allowed_hosts():
    """host:port entries to assert. Add a deployed hostname here if the demo
    ever moves off the local network, or the browser will be refused too."""
    hosts = [*LOCAL_HOSTS, *lan_ips()]
    return [f"{h}:{p}" for h in hosts for p in PORTS]


def api_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        env = Path(".env")
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    return key


def request(method, url, key, payload=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read() or "{}")


def explain(exc):
    """Turn ElevenLabs' error envelope into one readable line."""
    try:
        detail = json.loads(exc.read()).get("detail", {})
        return detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    except Exception:
        return str(exc)


def current_allowlist(agent):
    platform = agent.get("platform_settings", {}) or {}
    auth = platform.get("auth", {}) or {}
    return [h.get("hostname") for h in auth.get("allowlist", []) or []]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report without changing anything")
    args = parser.parse_args()

    key = api_key()
    if not key:
        print("ELEVENLABS_API_KEY is not set — see .env.example", file=sys.stderr)
        return 1

    try:
        agent = request("GET", f"{BASE}/{AGENT_ID}", key)
    except urllib.error.HTTPError as exc:
        print(f"Could not read the agent: HTTP {exc.code} — {explain(exc)}", file=sys.stderr)
        if exc.code == 401:
            print("\nThe key needs convai_read and convai_write. The TTS key used for\n"
                  "voice generation does not have them; issue one that does.", file=sys.stderr)
        return 1

    expected = allowed_hosts()
    detected = lan_ips()
    before = current_allowlist(agent)
    print(f"agent      : {agent.get('name')} ({AGENT_ID})")
    print(f"allowlist  : {before or 'EMPTY — any host can connect'}")
    # Print what was detected so a wrong adapter is visible here rather than as a
    # browser auth failure later.
    print(f"this host  : {detected or 'no private IPv4 found — localhost only'}"
          f"{' (AXON_LAN_IP)' if os.environ.get('AXON_LAN_IP') else ''}")

    if args.check:
        if not before:
            return 2
        # Naming the missing entries is the point of --check as a pre-demo step:
        # a host that is absent fails as an auth error in the browser, which
        # looks nothing like the networking problem it actually is.
        missing = [h for h in expected if h not in before]
        if missing:
            print(f"missing    : {missing}")
            print("Run without --check to add them.")
            return 3
        print("ok         : every expected host is present")
        return 0

    # Merge rather than replace, so a hostname added by hand is not silently
    # dropped by running this.
    merged = list(dict.fromkeys([*before, *expected]))
    platform = agent.get("platform_settings", {}) or {}
    auth = dict(platform.get("auth", {}) or {})
    auth["allowlist"] = [{"hostname": h} for h in merged]
    # Blocks anything that sends no Origin at all — scripts, bots, curl.
    auth["enable_auth"] = auth.get("enable_auth", False)

    try:
        request("PATCH", f"{BASE}/{AGENT_ID}", key,
                {"platform_settings": {**platform, "auth": auth}})
    except urllib.error.HTTPError as exc:
        print(f"Could not update the agent: HTTP {exc.code} — {explain(exc)}", file=sys.stderr)
        return 1

    after = current_allowlist(request("GET", f"{BASE}/{AGENT_ID}", key))
    print(f"now        : {after}")
    print("\nApplied." if after else "\nPATCH returned 200 but the allowlist is still empty — check by hand.")
    print("Note: this also stops the test scripts working, since they send no Origin.")
    return 0 if after else 1


if __name__ == "__main__":
    sys.exit(main())
