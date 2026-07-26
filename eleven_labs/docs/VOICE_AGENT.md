# Conversational coach (ElevenLabs Agents)

Lets the patient talk to Axon instead of clicking. That matters more here than
it does in most products: the hand they would click with may be the impaired
one, so voice is arguably the primary interface rather than an accessory.

The page code is already built. What is left is the dashboard configuration,
which has to be done in the ElevenLabs UI.

---

## What it is not

**It is not the emergency stop.** A spoken "stop" crosses a network round trip
and can be misheard or missed entirely. The on-screen **Stop** button is the
guaranteed one and never goes away. The agent's `stop_session` tool is a
convenience on top of it, not a replacement for it.

Nor does the agent see anything. It has no access to the camera or the pose
stream — the only thing it knows about the session is what `get_status`
returns. That is deliberate, and the system prompt below leans on it.

---

## 1. Issue a key with agent permissions

The TTS key used by `scripts/generate_voice.py` is not enough:

```
GET /v1/convai/agents  ->  401  missing the permission convai_read
```

Create a key with **convai** read/write in the ElevenLabs dashboard. It is only
needed for managing the agent — the browser never sees it, because the page
connects to a **public** agent by id.

## 2. Create the agent

Dashboard → Agents → new agent.

**Voice:** use `21m00Tcm4TlvDq8ikWAM` — the same voice
`scripts/generate_voice.py` renders the pre-recorded cues with. Different
voices for the agent and the cues make it sound like two people coaching the
same patient.

**Set the agent to public** so the browser can connect with only the agent id.
Nothing secret is exposed by this: the tools it can reach are all in-page, and
they are all enumerated below.

## 3. System prompt

```
You are the coach inside Axon, a motor-recovery tool for people regaining arm
movement after a stroke or injury. You speak with someone who is mid-exercise,
often with an arm they cannot fully control, and electrodes on their skin.

How to speak:
- Short sentences. One instruction at a time. They are moving, not reading.
- Calm and matter-of-fact. Encourage sparingly and specifically — constant
  praise stops meaning anything.
- Never rush them. Recovery work is slow on purpose.

What you can and cannot see:
- You cannot see the person, the camera, or their arm.
- Call get_status before saying anything about the session. Never guess how
  many reps they have done or which step they are on.
- If get_status says reps are timed rather than measured (this is the grip
  exercise — there is no finger tracking), do not claim to have seen the
  movement.

Safety, and this overrides everything else:
- If they say stop, or say they are in pain, or sound distressed, call
  stop_session immediately. Do not ask a clarifying question first.
- You are not a clinician. Do not diagnose, do not give medical advice, do not
  comment on their prognosis. If they ask something medical, say it is a
  question for their physiotherapist.
- Do not encourage anyone to push through pain.

Exercises available: elbow flexion (bicep), elbow extension (tricep), forward
reach (front deltoid), pull back (rear deltoid), grip (wrist flexors).

Setup runs in order: choose an arm, place five pads, choose an exercise, then
the session. Use next_step and go_back to move through it rather than
describing what button to press.
```

## 4. Client tools

Add each of these as a **client tool** on the agent. The implementations live
in `frontend/twin.html`; the dashboard only needs the name, description and
parameters to match.

| Tool | Parameters | Does |
|---|---|---|
| `get_status` | — | Returns the current step, chosen arm, which pad is being placed, exercise and rep count, whether reps are measured or timed, and whether the arm is currently visible. **Call this before commenting on the session.** |
| `set_arm` | `side`: `"left"` or `"right"` | Chooses which arm to work on. |
| `next_step` | — | Advances the flow. Refuses when the step is not complete. |
| `go_back` | — | Returns to the previous step. |
| `start_exercise` | `exercise`: string | Starts an exercise by name or muscle, e.g. `"elbow flexion"`, `"bicep"`, `"grip"`. Returns the list if there is no match. |
| `stop_session` | — | Stops the running session immediately. |

Each returns a short sentence describing what actually happened, so the agent
can tell the difference between a request and a result.

## 5. Run it

Pass the agent id on the URL:

```
http://localhost:8080/twin.html?agent=<your agent id>
```

Then press **Talk**. The browser will ask for the microphone — deliberately
only at that point, not on page load.

Without `?agent=`, the Talk button is disabled and everything else works
exactly as before.

---

## How it fits with the pre-rendered voice

Both exist on purpose:

| | Pre-rendered cues | Agent |
|---|---|---|
| Latency | None — local files | Network round trip |
| Works offline | Yes | No |
| Rep counting, pacing | Yes | No, too slow |
| Conversation, questions | No | Yes |

The cues stand down while the agent is speaking (`agentSpeaking` in
`twin.html`), so the two never talk over each other. If the venue network is
poor on the day, turn Talk off and the pre-rendered coaching still runs the
whole session unaided.
