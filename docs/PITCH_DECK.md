# Prompt for Claude Design — Pitch Deck

Copy everything below the line into Claude Design. Before running, attach / point it at:
`FES_Healthcare_Research_Brief.docx`, `Control_Feasibility_Memo.md`, the `slide_deck_design_concepts/` folder, and your own demo photos/video stills.

---

Build a **venture pitch deck as a self-contained web deck** — **HTML/CSS/JS, 16:9, presented in the browser**, navigable by arrow keys, with smooth slide-to-slide transitions (fade/slide). **Not a PowerPoint file.** Ideally a single self-contained file that works **offline** (no external dependencies at runtime) so it's reliable on unknown venue AV. ~13 slides.

Audience: **hackathon judges, several of whom are venture investors.** Pitch it like a seed-stage venture round — problem-first, huge market, sharp "why now," a fast wedge, a jaw-dropping live demo, and an honest line between what's a demo and what's the product. One idea per slide, minimal text, big confident statements.

**This is a pitch for the project, not the team.** Keep the focus entirely on the problem, the technology, the demo, and the market. **No team/bio/"why us" slides. Do not mention the competition, prizes, accelerators, or Y Combinator anywhere in the deck** — it should read as a standalone company pitch.

## What we are

A **closed-loop functional electrical stimulation (FES) system** that drives a person's own arm through precise, **prescribed** movements by safely stimulating their muscles and correcting the motion in real time with motion-sensor (IMU) feedback. Movements are commanded from a **3D digital twin of the arm**: set a pose on the twin and the real limb follows; push the twin's hand and the real hand grips. It's a wearable, affordable, at-home way to deliver the **high-repetition movement therapy that today depends on scarce therapist time** — no intent-reading, no EMG, no brain-computer interface. Beachhead: **hand and upper-limb rehabilitation after stroke**, with cervical spinal cord injury (SCI) as the fast-follow. (Product name: **[INSERT NAME]** — use a clean wordmark placeholder if none is given.)

## Source material (pull facts/figures from the attached report; key ones below)

- **>100M stroke survivors globally** (~101M); **12.2M new strokes/year**; **~US$890B/year** cost, projected to nearly double by 2050. *(WSO Global Stroke Fact Sheet)*
- **70–80% of stroke patients** have upper-limb impairment; hand/fine-motor recovery is the slowest.
- **~14.5M people live with spinal cord injury**; >50% affects the upper limb; restoring hand grasp is the **#1 recovery priority** for people with tetraplegia.
- Care is **chronically under-dosed**: guidelines call for ~45 min/day therapy 5×/week, rarely met, driven by a global therapist shortage.
- **FES device market ~US$3.1B by 2030 (~7.2% CAGR)**; reimbursement precedent exists (CMS covers FES; FDA-cleared IpsiHand got a CMS pathway).
- Clinical proof: FES-based rehabilitation improves upper-limb recovery (meta-analysis SMD ≈ 0.50); 2025 research validates closed-loop FES for restoring hand/limb movement.
- Competitive whitespace: incumbents are either **cheap-but-dumb** (open-loop foot-drop stimulators: Bioness/Ottobock L300) or **smart-but-expensive/clinic-bound** (EEG-BCI systems like Neurolutions IpsiHand; therapist-run MyndMove). The middle — **smart, autonomous, affordable, at-home upper-limb** — is empty. That's us.

## The demo (make this the centerpiece — it's our proof)

We built a working closed-loop FES stack and **control a blindfolded teammate's arm in real time**. An operator (or a judge) changes the pose of a **3D digital twin of the arm**, and the real limb is driven to match it, with the twin showing target-vs-actual tracking; pushing the twin's hand triggers a grip response. Blindfolded + randomized/judge-chosen poses = unfakeable proof that the **stimulation**, not the person, is producing the movement. Frame it as: "the hard part — safely and precisely driving a human limb to a commanded target under closed-loop control, on cheap hardware — already works." (No intent sensing, no EMG, no BCI — the target comes from the digital twin.)

## Slide-by-slide structure

1. **Title / hook** — product wordmark + one-line tagline (e.g., "Give movement back — without a prosthetic"). Cinematic.
2. **Problem** — 100M+ stroke survivors with a hand that won't work; recovery gated by therapist time that doesn't exist. Lead with the human + the scale.
3. **Why now** — cheap edge AI + wearable sensing + validated FES therapy have only just converged; 2025 research proves the approach, no one has productized it for the home.
4. **Insight** — recovery needs high-dose, repeated movement practice, but the standard of care can't deliver it: it's gated by therapist time. What if the movement were delivered automatically, at home, on the patient's own arm?
5. **Product** — a wearable that *executes prescribed movements on the patient's own limb* with safe stimulation and closed-loop precision — autonomous, at-home, a fraction of the cost of clinic or BCI systems. The **3D digital twin is the control surface**: author a movement, the arm performs it.
6. **How it works** — clean signal-chain diagram: *target pose (from the 3D digital twin / prescribed program) → closed-loop controller → electrical stimulation → the user's own muscles → IMU motion feedback → back to the controller.* Emphasize the correcting loop (it hits and holds the target) and the separate grip trigger. No intent input — the target is commanded.
7. **Live demo** — the blindfolded-arm control + 3D twin. Big visual. An operator/judge sets a pose on the twin; the real arm follows; pushing the twin's hand triggers the grip. "Not a mockup — our system, a real human arm."
8. **Why we win** — 2×2 competitive map (axes: dumb→intelligent, clinic/expensive→at-home/affordable); incumbents cluster in the corners, we own the empty middle.
9. **Market** — global TAM/SAM: stroke + SCI populations, FES device market size + growth. Simple bold charts.
10. **Business & regulatory** — at-home device model; Class II / FDA De Novo precedent (IpsiHand); CMS reimbursement path exists. Keep it credible, not hand-wavy.
11. **What's built** — the working stack: safe closed-loop FES + AI control + hardware fail-safes. Proof the technology is real and moving fast. Frame as *project traction*, not team bio — "here's what already works," not "here's who we are."
12. **Vision** — start with stroke hands → SCI → restoring movement wherever the body's wiring can be intelligently re-driven, without a prosthetic.
13. **Close** — the mission in one line + the near-term milestone / call to action, and a memorable closing statement. No prizes, accelerators, or the competition itself.

## Images to use / generate

- **Patient imagery:** dignified, hopeful, diverse stroke/SCI survivors doing hand/arm rehabilitation — never exploitative or clinical-grim.
- **System illustration:** an arm with surface electrodes + the wearable; the closed-loop signal-chain diagram; the 3D digital-twin pose-tracking visual.
- **Our own demo photos/stills** wherever possible (I'll supply) — prefer real over stock for the demo slide.
- **Charts:** market-growth bar, patient-population figures, the competitive 2×2.
- Use consistent, simple line iconography for the signal-chain stages.

## Design direction

Use the images in `slide_deck_design_concepts/` as the reference palette. Lead with the **dark, cinematic, premium deep-tech** direction (à la the Noxera concept) with **graphite/liquid-metal textures** (the chrome concept) and **glass/frosted cards** — high contrast, deep blacks/charcoals, generous negative space, large modern sans-serif type, monochrome photography with a single vivid accent color that evokes *signal/current* (electric cyan-teal or a charged orange). Keep it medically credible and restrained — futuristic, not gimmicky. (Alternate light option available in the White-&-Gold concept if a cleaner clinical look is preferred, but default to dark.) Every slide: one headline idea, minimal body text, a strong visual.

Tone: confident, deep-tech, honest. Big claims backed by the numbers above; clear about demo-vs-product.
