// The brand mark: a dot orbiting a dashed ring. Two states:
//   "orbiting" (default) — landing hero, twin.html header
//   "settled"             — results page, once a session has ended
//
// The pad-placement "pointing" behavior during twin.html's setup flow is a
// SEPARATE thing — a Three.js material on the existing on-model marker, not
// this element. See highlightPad() in twin.html.
const TEMPLATE = `
<style>
  :host {
    display: inline-block;
    width: var(--gd-size, 22px);
    height: var(--gd-size, 22px);
    vertical-align: middle;
  }
  .ring {
    position: relative;
    width: 100%;
    height: 100%;
    border: 1px dashed var(--line, #232c36);
    border-radius: 50%;
    transition: border-style 200ms ease-out, opacity 200ms ease-out;
  }
  .orbit {
    position: absolute;
    inset: 0;
  }
  :host([state="orbiting"]) .orbit {
    animation: gd-orbit 3s linear infinite;
  }
  .dot {
    position: absolute;
    top: 0;
    left: 50%;
    width: var(--gd-dot, 8px);
    height: var(--gd-dot, 8px);
    margin-left: calc(var(--gd-dot, 8px) / -2);
    margin-top: calc(var(--gd-dot, 8px) / -2);
    border-radius: 50%;
    background: var(--accent, #2fbe93);
    box-shadow: 0 0 8px 2px var(--accent-dim, rgba(47, 190, 147, 0.5));
  }
  :host([state="settled"]) .ring {
    border-style: solid;
    opacity: 0.6;
  }
  :host([state="settled"]) .dot {
    top: 50%;
    left: 50%;
  }
  @keyframes gd-orbit {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    :host([state="orbiting"]) .orbit { animation: none; }
    :host([state="orbiting"]) .dot { top: 50%; left: 50%; }
  }
</style>
<div class="ring"><div class="orbit"><div class="dot"></div></div></div>
`;

export class GuideDot extends HTMLElement {
  connectedCallback() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" }).innerHTML = TEMPLATE;
    }
    if (!this.hasAttribute("state")) this.setAttribute("state", "orbiting");
  }
}

if (!customElements.get("guide-dot")) {
  customElements.define("guide-dot", GuideDot);
}
