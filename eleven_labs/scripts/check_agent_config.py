#!/usr/bin/env python3
"""
Pre-demo check: does the agent's dashboard config match what the page expects?

twin.html implements six client tools and connects to a public agent by id. If
the dashboard disagrees — agent private, a tool missing or renamed — the failure
surfaces in the browser as the agent going quiet or apologising, with nothing in
the server log. This reads the agent and names the mismatch instead.

Read-only. It never writes to the agent.

    uv run python scripts/check_agent_config.py

Exit codes: 0 config is usable, 1 could not read the agent, 3 mismatches found.
"""
import sys

# Same directory, so this resolves when run as `python scripts/check_agent_config.py`.
from lock_agent_allowlist import AGENT_ID, BASE, api_key, explain, request

import urllib.error

# Must match agentTools() in frontend/twin.html. The dashboard needs a client
# tool of each name; the page supplies the implementation.
EXPECTED_TOOLS = {
    "get_status": [],
    "set_arm": ["side"],
    "next_step": [],
    "go_back": [],
    "start_exercise": ["exercise"],
    "stop_session": [],
}

# The voice generate_voice.py renders the pre-recorded cues with. A different
# agent voice makes it sound like two people coaching the same patient.
EXPECTED_VOICE = "21m00Tcm4TlvDq8ikWAM"


def dig(obj, *path, default=None):
    for key in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
        if obj is None:
            return default
    return obj


def tool_names(agent):
    """Client tools, across the couple of shapes the API has used for them.

    Returns (named, ids). The API reports the same tool twice — once under
    `tools` with its name and parameters, once under `tool_ids` as an opaque
    `tool_xxx` handle. They are not separate tools, so the ids are kept apart
    from the named set rather than being mistaken for unhandled extras.
    """
    prompt = dig(agent, "conversation_config", "agent", "prompt", default={}) or {}
    named = {}
    for entry in (prompt.get("tools") or []):
        if isinstance(entry, dict) and entry.get("name"):
            params = dig(entry, "parameters", "properties", default={}) or {}
            named[entry["name"]] = sorted(params)
    ids = [str(e) for e in (prompt.get("tool_ids") or [])]
    return named, ids


def main():
    key = api_key()
    if not key:
        print("ELEVENLABS_API_KEY is not set — see .env.example", file=sys.stderr)
        return 1

    try:
        agent = request("GET", f"{BASE}/{AGENT_ID}", key)
    except urllib.error.HTTPError as exc:
        print(f"Could not read the agent: HTTP {exc.code} — {explain(exc)}", file=sys.stderr)
        if exc.code == 401:
            print("\nThe key needs convai_read.", file=sys.stderr)
        return 1

    problems = []
    print(f"agent      : {agent.get('name')} ({AGENT_ID})")

    # The browser connects with the id alone and no signed URL, so a private
    # agent refuses it — this is the single most common reason Talk does nothing.
    access = dig(agent, "platform_settings", "auth", "enable_auth")
    public = access is False
    print(f"public     : {public}  (enable_auth={access})")
    if not public:
        problems.append("Agent requires auth. twin.html connects with the id alone; "
                        "set it public in the dashboard.")

    voice = dig(agent, "conversation_config", "tts", "voice_id")
    print(f"voice      : {voice}")
    if voice and voice != EXPECTED_VOICE:
        problems.append(f"Voice is {voice}, cues are rendered with {EXPECTED_VOICE} — "
                        "they will sound like two different coaches.")

    prompt = dig(agent, "conversation_config", "agent", "prompt", "prompt", default="") or ""
    print(f"prompt     : {len(prompt)} chars")
    if len(prompt) < 200:
        problems.append("System prompt looks empty or stub. The safety rules "
                        "(stop on distress, no medical advice) live there — "
                        "see docs/VOICE_AGENT.md section 3.")

    found, ids = tool_names(agent)
    print(f"tools      : {sorted(found) or 'NONE'}")
    if ids:
        print(f"tool ids   : {len(ids)} (opaque handles for the same tools)")
    for name, params in EXPECTED_TOOLS.items():
        if name not in found:
            problems.append(f"Client tool '{name}' is not on the agent.")
        elif sorted(params) != found[name]:
            problems.append(f"Client tool '{name}' takes {found[name]}, "
                            f"twin.html sends {sorted(params)}.")
    for extra in sorted(set(found) - set(EXPECTED_TOOLS)):
        print(f"note       : agent has extra tool '{extra}' with no handler in twin.html")
    # A count mismatch here means a tool exists as a handle but was not expanded
    # into the named list, which would hide a genuinely missing implementation.
    if ids and len(ids) != len(found):
        print(f"note       : {len(found)} named tools but {len(ids)} ids — "
              "check the dashboard for a tool the page cannot service")

    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        print("\nAll of these are fixed in the ElevenLabs dashboard, not in code.")
        return 3

    print("\nok — dashboard config matches twin.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
