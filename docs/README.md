# Documentation index

Everything written down about this project, and when you'd want it.

> **Read [`SAFETY.md`](SAFETY.md) first.** This system drives current through a
> person. Nothing else here matters until that one has been read.

---

## Start here

| Doc | Read it when |
|---|---|
| [`SAFETY.md`](SAFETY.md) | **Before connecting anything to anybody.** Safety layers, procedure, what the board enforces independently of the PC. |
| [`MY_SETUP.md`](MY_SETUP.md) | Day to day. Copy/paste commands for *this* machine, board and network — COM ports, board IP, firmware image. The other docs explain *why*; this one is just the commands. |
| [`TESTING.md`](TESTING.md) | Bringing hardware up for the first time, or after a change. Bench checks in the order they should be run. |

## Building and running

| Doc | Covers |
|---|---|
| [`DEPLOY.md`](DEPLOY.md) | Wi-Fi setup, wireless firmware deploy, powering the board from 5 V. |
| [`WIRING.md`](WIRING.md) | Relay modules, the dummy-load resistor that prevents the turn-on jolt, electrode placement. |
| [`CONTROL.md`](CONTROL.md) | Why PI and not PID, gain tuning, loop timing, how the deadband is sized from measured pose noise. |
| [`POSE_API.md`](POSE_API.md) | The pose ingest contract — the UDP message the controller expects on `:9090`. |
| [`INTEGRATION.md`](INTEGRATION.md) | Wiring the vision feed in `axon-main/` to the controller. |

## Background and review

| Doc | Covers |
|---|---|
| [`FEASIBILITY.md`](FEASIBILITY.md) | The original question — can a feedback controller drive a passive arm to selected poses with relay-actuated TENS? Written before the build. |
| [`BUILD_CONTEXT.md`](BUILD_CONTEXT.md) | Self-contained brief describing the system, for pasting into a fresh session as context. |
| [`CODE_REVIEW.md`](CODE_REVIEW.md) | Whole-tree review: correctness, safety, and where the docs and the code disagreed. |
| [`REVIEW_RESPONSE.md`](REVIEW_RESPONSE.md) | What was changed in response to that review. |
| [`PITCH_DECK.md`](PITCH_DECK.md) | Prompt and source material for generating the pitch deck. |

---

## Docs living with their subproject

These stay next to the code they describe, because each subproject carries its
own dependencies and can be run on its own.

| Where | Covers |
|---|---|
| [`../webapp_demo/README.md`](../webapp_demo/README.md) | The demo site — Muscle Mapper, Live Twin and Human Control, all served from one FastAPI app. |
| [`../axon-main/README.md`](../axon-main/README.md) | The pose service: MediaPipe landmarks, arm-angle extraction, the WebSocket and MJPEG endpoints. |
| [`../axon-main/docs/POSE_OUTPUT.md`](../axon-main/docs/POSE_OUTPUT.md) | The exact shape of what the pose service emits. |
| [`../eleven_labs/README.md`](../eleven_labs/README.md) | The Live Twin standalone: 3D anatomy, pad placement, session flow. |
| [`../eleven_labs/docs/VOICE_AGENT.md`](../eleven_labs/docs/VOICE_AGENT.md) | The conversational coach — agent config, client tools, and why it is deliberately *not* the emergency stop. |

## Root

| Doc | Covers |
|---|---|
| [`../README.md`](../README.md) | The system as a whole: architecture, pin map, quick start, keyboard controls. |
| [`../AGENTS.md`](../AGENTS.md) | Working agreements for agents and contributors on this repo. |
