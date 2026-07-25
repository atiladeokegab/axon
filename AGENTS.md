# Hardware Hackathon
Hardware Hackathon with Aximeta + anthropic + Bambu labs + elevenlabs + seedcamp
Keep things simple and demoable with clear goals throughout the roadmap `../ROADMAP.md`

# Project Summary
Building a **Neuromuscular Aim Assistant**: an AI that plays Assault Cube *through a human's arm* by electrically stimulating their muscles. (Concept demo: Mr Homeless — https://www.youtube.com/watch?v=9alJwQG-Wbk)

**The money shot:** crosshair passes over an enemy → the wearer's finger is forced to pull the trigger. Aim-nudge left/right is the stretch goal; up/down is out of scope.

## Signal chain (all on "Short Network" mobile access point)
1. **Jetson Orin Nano** ("ShortAi" — access via `ssh shortai`, passwordless sudo) — runs Assault Cube (compiled from source, ARM64) and a vision model (YOLO + TensorRT) detecting enemies on screen. A UDP telemetry hook patched into the game source provides auto-labeled training data and a fallback demo path.
2. **Axiometa Genesis Mini** (ESP32-S3-MINI-1-N4R2, 4MB flash/2MB PSRAM) — 3.3V logic, USB-C, 4× AX22 ports (3 GPIO + ADC + I2C/SPI/UART each) + STEMMA QT. Arduino/MicroPython; SoftAP + raw UDP supported. Receives stim commands over UDP running as its own AP (no venue WiFi, no TCP/MQTT). Docs/schematic: https://www.axiometa.io/products/axiometa-genesis-mini
3. **Relay board** — ESP32 GPIO → IRLZ44N (flyback diodes on coils) → mechanical relays wired **in series** with one electrode lead per TENS channel, normally open. Relays chosen for galvanic isolation: the TENS unit (AUVON 4-channel, B08FM4KS1R — datasheet: `../docs/AUVON-AS8016-datasheet.pdf`) stays battery-powered and fully floating;

## Safety invariants (non-negotiable, also the demo narration)
- Normally-open relays: any crash, power loss, or WiFi drop = stim off.
- ESP32 firmware watchdog: no UDP packet for 500ms → all relays open. Max ~300ms stim per burst with forced cooldown.
- Physical kill switch in the electrode lead, held by the wearer.
- Current never crosses the chest.

## Demo tiers (build in this order — each independently demoable)
1. ESP32 + relays + web test-fire page (per-channel manual stim + calibration tool)
2. Game telemetry hook → stim fires when enemy visible (full loop, no ML)
3. Vision model replaces telemetry (headline demo) + on-screen bounding-box overlay

## BOIL THE OCEAN
This project is very bold, we need to rebuild and interact multiple systems together, especialy with configuration programs. 

When planning don't be afraid to suggest insance ideas. For example when traininng the visual model suggest reading the implementation of assault cube and pulling the memory references to the enemies and maps then implementing a program to directly interpret the memmory understanding what enemies are visible and where there respective heads/bodies are.


## Let agents build what they need
Avoid feature creep, and assume things should be highly demoable. This seems to counter `BOIL THE OCEAN` pricipl but it doesn't. We are in a new age of software engineering and hardware engineering planning.

For example should we patch Assault Cube for enemy location and visibility information for the visual traning. YES absolutely, as it is easy to implement in software and saves a signficant amount of time to label data by hand and is significantly more accurate.

Should we build mouse software controls, probably not, this is not what we are building and looks less impressive in the demo, even if it is trivial for an ai agent to build.


## Fight for the "obvious' solution
We should avoid being clever and doing things because they are exact and smart, everything should be so obvious people think we are stupid.

Never hesitate to push back and suggest way to be more obvious. Note simple and obvious don't align, sometimes the obvious solution is more complex. 


# General rules
These are some general rules to steer you in the correct direction. They are not hardset but default to following them. 
- When discussing hardware implications always consider the implications within the circuit for the risk of components.
- Always use the inital prompt as a guide of where in the project we are targeting
- Prefer python as the language used with a virtual environment.
- Ensure to research before installing packages to the jetson, it is a sensitive system.
- When in doubt, make the obvious agent assumption true
