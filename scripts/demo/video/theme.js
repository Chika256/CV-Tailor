// The one theme for the demo video: every colour, font, easing curve and spring lives here.
import { bezier } from "./motion.js";

export const theme = {
  video: { width: 1280, height: 800, fps: 25 },
  colors: {
    bg: "#0b1512",
    bgAlt: "#12201c",
    surface: "#172823",
    // The hero colour: at most one element per frame wears it (or glows).
    primary: "#4fd1a5",
    accent: "#f0b45c",
    text: "#eef3f0",
    textDim: "#9db1a9",
    rule: "rgba(238, 243, 240, 0.10)",
    shadow: "rgba(0, 0, 0, 0.4)",
    paper: "#f8f7f2", // the popup's own background, behind its screenshots
  },
  fonts: {
    // Geist is the extension's own font (extension/fonts, SIL OFL).
    display: '"Geist", "Segoe UI", sans-serif',
    body: '"Geist", "Segoe UI", sans-serif',
    mono: '"Cascadia Mono", Consolas, ui-monospace, monospace',
  },
  ease: {
    out: bezier(0.16, 1, 0.3, 1), // entrances (easeOutExpo)
    inOut: bezier(0.83, 0, 0.17, 1), // camera moves
    in: bezier(0.7, 0, 0.84, 0), // exits only
  },
  spring: {
    snappy: { damping: 14, stiffness: 160, mass: 0.6 }, // words, small UI
    card: { damping: 24, stiffness: 220, mass: 1 }, // screenshots and cards: big, so quick to settle
  },
  // Frame offsets between staggered siblings.
  stagger: { word: 2, item: 5, block: 6 },
};
