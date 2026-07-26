#!/usr/bin/env python3
"""
Point the agent at the same voice the pre-rendered cues use.

The cues in frontend/assets/voice/ are rendered by generate_voice.py with
VOICE_ID; if the agent speaks as someone else, a session sounds like two
different coaches talking to the same patient. This aligns the agent to the
cues, which is the cheap direction — the alternative is re-rendering 56 clips.

    uv run python scripts/set_agent_voice.py            # align to the cue voice
    uv run python scripts/set_agent_voice.py --voice X  # set an explicit voice id

Needs a key with convai_read and convai_write.
Exit codes: 0 set (or already correct), 1 could not read or write the agent.
"""
import argparse
import sys
import urllib.error

from lock_agent_allowlist import AGENT_ID, BASE, api_key, explain, request

# Imported rather than copied so the two can never drift apart.
from generate_voice import VOICE_ID as CUE_VOICE_ID


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default=CUE_VOICE_ID,
                        help=f"voice id to set (default: the cue voice, {CUE_VOICE_ID})")
    args = parser.parse_args()

    key = api_key()
    if not key:
        print("ELEVENLABS_API_KEY is not set — see .env.example", file=sys.stderr)
        return 1

    try:
        agent = request("GET", f"{BASE}/{AGENT_ID}", key)
    except urllib.error.HTTPError as exc:
        print(f"Could not read the agent: HTTP {exc.code} — {explain(exc)}", file=sys.stderr)
        return 1

    config = agent.get("conversation_config", {}) or {}
    tts = dict(config.get("tts", {}) or {})
    before = tts.get("voice_id")

    print(f"agent      : {agent.get('name')} ({AGENT_ID})")
    print(f"voice was  : {before}")
    if before == args.voice:
        print(f"already set to {args.voice} — nothing to do")
        return 0

    tts["voice_id"] = args.voice

    # Spread the existing config so this only touches voice_id. A bare
    # {"tts": {"voice_id": ...}} would drop model and stability settings.
    payload = {**config, "tts": tts}

    # GET returns the tools both expanded and as ids, but PATCH rejects being
    # given both ("Cannot specify both tools and tool IDs"). Echoing the GET
    # back therefore always 400s. Drop the expanded copy and keep the ids,
    # which are what the agent actually references.
    prompt = dict((payload.get("agent", {}) or {}).get("prompt", {}) or {})
    if prompt.get("tools") is not None and prompt.get("tool_ids"):
        prompt.pop("tools")
        payload["agent"] = {**(payload.get("agent", {}) or {}), "prompt": prompt}

    try:
        request("PATCH", f"{BASE}/{AGENT_ID}", key, {"conversation_config": payload})
    except urllib.error.HTTPError as exc:
        print(f"Could not update the agent: HTTP {exc.code} — {explain(exc)}", file=sys.stderr)
        if exc.code == 401:
            print("\nThe key needs convai_write, not just convai_read.", file=sys.stderr)
        return 1

    after = ((request("GET", f"{BASE}/{AGENT_ID}", key)
              .get("conversation_config", {}) or {}).get("tts", {}) or {}).get("voice_id")
    print(f"voice now  : {after}")
    if after != args.voice:
        print("PATCH returned 200 but the voice did not change — set it in the dashboard.",
              file=sys.stderr)
        return 1
    print("\nAligned with the pre-rendered cues.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
