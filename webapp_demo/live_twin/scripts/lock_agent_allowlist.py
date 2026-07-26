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
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

AGENT_ID = os.environ.get("AXON_AGENT_ID", "agent_5601kydjajk9fnqtvwyn2ed4tmnd")
BASE = "https://api.elevenlabs.io/v1/convai/agents"

# Where the app is actually served from. Add a deployed hostname here if the
# demo ever moves off localhost, or the browser will be refused too.
ALLOWED_HOSTS = [
    "127.0.0.1:8080",
    "localhost:8080",
    "127.0.0.1:8000",
    "localhost:8000",
]


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

    before = current_allowlist(agent)
    print(f"agent      : {agent.get('name')} ({AGENT_ID})")
    print(f"allowlist  : {before or 'EMPTY — any host can connect'}")

    if args.check:
        return 0 if before else 2

    # Merge rather than replace, so a hostname added by hand is not silently
    # dropped by running this.
    merged = list(dict.fromkeys([*before, *ALLOWED_HOSTS]))
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
