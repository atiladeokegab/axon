#!/usr/bin/env python3
"""
Renders the coaching vocabulary to audio files, once, ahead of time.

The cues are a small fixed set, so there is no reason to call a network API
during a session. Pre-rendering means a cue plays with zero latency and cannot
fail because the venue wifi is bad — which matters when the thing the cue is
competing with is a person mid-exercise who is not looking at the screen.

Run once, and again whenever the wording changes:

    uv run python scripts/generate_voice.py            # only missing files
    uv run python scripts/generate_voice.py --force    # re-render everything

Needs ELEVENLABS_API_KEY (see .env.example). Writes to
frontend/assets/voice/ plus a manifest the frontend reads. The whole
vocabulary is well under 1000 characters, so a full re-render is cheap.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Hardcoded rather than looked up: hackathon keys are commonly scoped without
# voices_read, and a fixed voice is what you want anyway — the coach should not
# change character between runs.
VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
MODEL_ID = "eleven_flash_v2_5"   # lowest latency; these are short utterances
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "frontend" / "assets" / "voice"

# key -> spoken text. Keys are what the frontend asks for, so renaming one is a
# code change on both sides; changing the *text* is just a re-render.
CUES = {
    # Getting set up
    "ready":            "Ready when you are.",
    "begin":            "Let's begin.",
    "complete":         "Session complete. Well done.",

    # Framing — these exist because the visual prompt is useless to someone
    # who is mid-movement and not looking at the screen.
    "no_person":        "Step into view of the camera.",
    "arm_left":         "Bring your left arm into the camera.",
    "arm_right":        "Bring your right arm into the camera.",
    "arm_generic":      "Bring your arm into the camera.",
    "reacquired":       "Got you.",

    # The five motions the backend can drive
    "motion_grip":          "Close your hand.",
    # Named for the muscle each pad drives, NOT for the motion key. MOTION_RAISE
    # fires the bicep (elbow flexion) and MOTION_LOWER the tricep (extension) —
    # cueing "raise"/"lower your arm" would tell the patient to move their
    # shoulder while the electrodes bend their elbow.
    "motion_raise":         "Bend your elbow.",
    "motion_lower":         "Straighten your arm.",
    "motion_push_forward":  "Push forward.",
    "motion_pull_back":     "Pull back.",

    # Rep cadence
    "hold":             "Hold.",
    "relax":            "And relax.",
    "again":            "Again.",
    "last_one":         "Last one.",
    "three_more":       "Three more.",
    "two_more":         "Two more.",
    "one_more":         "One more.",

    # Encouragement — sparing on purpose; constant praise reads as hollow
    "good":             "Good.",
    "nice_work":        "Nice work.",

    # Guided pad placement. Worded as body landmarks a person can find on
    # themselves without a mirror — "front of your upper arm", not "biceps
    # brachii" — since they are placing these one-handed on their own limb.
    "setup_start":      "Let's place the pads. I'll show you each one.",
    "pad_bicep":        "First pad: the front of your upper arm, over the muscle.",
    "pad_tricep":       "Next: the back of your upper arm.",
    "pad_front_delt":   "Now the front of your shoulder.",
    "pad_rear_delt":    "And the back of your shoulder.",
    "pad_wrist_flex":   "Last one: the inside of your forearm, below the elbow.",
    "pad_done":         "All pads placed. Nice.",
    "setup_confirm":    "Say next, or press the button, when it's on.",

    # Step transitions
    "choose_exercise":  "Choose an exercise.",
    "watch_first":      "Watch the model first.",
    "your_turn":        "Your turn.",

    # Per-exercise walkthrough. Each one gets: what it trains and why it
    # matters (people stick with rehab they understand), then a form cue for
    # the mistake that exercise actually invites.
    "intro_elbow_flexion":  "Elbow flexion. This trains the bicep to bend your arm. Keep your upper arm still and let the forearm do the work.",
    "intro_elbow_extension": "Elbow extension. This trains the tricep to straighten your arm. After a stroke this is usually the harder direction, so take it slowly.",
    "intro_shoulder_flexion": "Forward reach. This is the movement behind reaching for a cup or a door handle. Lead with your hand, and keep your shoulder down.",
    "intro_shoulder_extension": "Pull back. This works the back of your shoulder, and balances the forward reach. Draw your elbow behind you.",
    "intro_grip":           "Grip. Closing and opening your hand. I can't see your fingers, so I'll pace you and you count with me.",

    # Encouragement mid-set. Deliberately plain — over-praising every rep stops
    # meaning anything, so these are used sparingly and never twice running.
    "enc_good_form":        "That's it. Same again.",
    "enc_keep_going":       "Good. Keep that rhythm.",
    "enc_halfway":          "Halfway. You're doing well.",
    "enc_almost":           "Nearly there.",
    "enc_strong_finish":    "Strong finish.",

    # When a rep does not register, which is information rather than failure.
    "try_fuller":           "Try a fuller movement — go a little further.",
    "take_a_moment":        "Take a moment. Start again when you're ready.",

    # Safety
    "stopping":         "Stopping.",

    # Counting reps aloud
    **{f"count_{i}": word for i, word in enumerate(
        ["one", "two", "three", "four", "five",
         "six", "seven", "eight", "nine", "ten"], start=1)},
}


def synthesise(text, api_key):
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
        data=json.dumps({
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {
                # Steady and calm: this is instruction during physical effort,
                # not performance. High stability keeps delivery consistent
                # across cues so the coach does not sound erratic.
                "stability": 0.65,
                "similarity_boost": 0.75,
                "speed": 0.95,
            },
        }).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true",
                        help="re-render cues that already exist")
    args = parser.parse_args()

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        # Try .env without requiring a dependency for it.
        env = Path(".env")
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        print("ELEVENLABS_API_KEY is not set — see .env.example", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    rendered = skipped = 0
    total_chars = 0

    for key, text in CUES.items():
        path = OUTPUT_DIR / f"{key}.mp3"
        # Hash the text so a reworded cue re-renders while untouched ones don't.
        digest = hashlib.sha256(f"{VOICE_ID}|{MODEL_ID}|{text}".encode()).hexdigest()[:12]
        stamp = OUTPUT_DIR / f".{key}.stamp"
        unchanged = stamp.is_file() and stamp.read_text().strip() == digest

        if path.is_file() and unchanged and not args.force:
            skipped += 1
        else:
            try:
                audio = synthesise(text, api_key)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                print(f"  FAILED {key}: HTTP {exc.code} {detail}", file=sys.stderr)
                return 1
            except urllib.error.URLError as exc:
                print(f"  FAILED {key}: {exc.reason}", file=sys.stderr)
                return 1
            path.write_bytes(audio)
            stamp.write_text(digest)
            rendered += 1
            total_chars += len(text)
            print(f"  rendered {key:20} {len(audio):>7} bytes  \"{text}\"")

        manifest[key] = {"file": f"{path.name}", "text": text}

    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(f"\n{rendered} rendered, {skipped} unchanged, {len(CUES)} cues total")
    print(f"{total_chars} characters used this run")
    print(f"manifest: {OUTPUT_DIR / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
