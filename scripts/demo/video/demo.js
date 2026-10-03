// The CV Tailor demo as a frame-driven composition. Every visual is a pure function of the frame number;
// render_video.mjs calls window.renderFrame(n) for each frame and screenshots the stage.
//
// Content comes from a real run, saved by capture.mjs in scripts/demo/out/capture: the job page, the
// popup at each state (with the positions of the parts highlighted here) and the plan the model returned.
import { mix, interpolate, spring } from "./motion.js";
import { theme } from "./theme.js";

const { width, height, fps } = theme.video;
const { colors, ease } = theme;
const sec = (seconds) => Math.round(seconds * fps);
const EXIT = sec(0.3); // exits run in 0.3 s; entrances settle in about 0.6 s
// Each scene's caption has landed by then; only after it does anything on the right move, so no frame
// redraws both halves of the picture at once (it keeps the GIF small).
const VISUAL = sec(1.7);
const CAPTURE = "../out/capture/";

const capture = await (await fetch(`${CAPTURE}capture.json`)).json();
const plan = await (await fetch(`${CAPTURE}result.json`)).json();
const marks = Object.fromEntries(capture.shots.map((shot) => [shot.name, shot.marks]));
const edit = plan.replacements[0];

// ---- Timeline (seconds), one entry per scene ------------------------------------------------------
const SCENES = [
  ["hook", 3.4], ["send", 6.6], ["plan", 5.6], ["ask", 7.0], ["checks", 6.0], ["report", 9.0], ["cta", 4.8],
];
const T = {};
let cursor = 0;
for (const [name, seconds] of SCENES) {
  T[name] = { from: cursor, to: cursor + sec(seconds) };
  cursor = T[name].to;
}
const durationInFrames = cursor;

// ---- Theme into CSS custom properties ----------------------------------------------------------------
const vars = {
  bg: colors.bg, "bg-alt": colors.bgAlt, surface: colors.surface, primary: colors.primary, accent: colors.accent,
  text: colors.text, "text-dim": colors.textDim, rule: colors.rule, shadow: colors.shadow, paper: colors.paper,
  "font-display": theme.fonts.display, "font-body": theme.fonts.body, "font-mono": theme.fonts.mono,
  width: `${width}px`, height: `${height}px`,
};
for (const [name, value] of Object.entries(vars)) document.documentElement.style.setProperty(`--${name}`, value);

// ---- DOM helpers ---------------------------------------------------------------------------------------
const stage = document.getElementById("stage");
function el(parent, className = "", text = "", tag = "div") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  parent.append(node);
  return node;
}
function at(node, x, y) {
  node.classList.add("abs");
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
  return node;
}
/** Split text into word spans, keeping the spaces inside them so wrapping is unchanged. */
function words(parent, text, heroWord) {
  return text.split(" ").map((word, i, all) => {
    const span = el(parent, "w", word + (i < all.length - 1 ? " " : ""), "span");
    if (word === heroWord) span.classList.add("hero-word");
    return span;
  });
}
const checkIcon = (color) =>
  `<svg width="22" height="22" viewBox="0 0 22 22"><circle cx="11" cy="11" r="10" fill="none" stroke="${color}"` +
  ` stroke-opacity="0.35" stroke-width="1.5"/><path class="tick" d="M6.5 11.5l3 3 6-7" fill="none" stroke="${color}"` +
  ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="14" stroke-dashoffset="14"/></svg>`;
const crossIcon = (color) =>
  `<svg width="18" height="18" viewBox="0 0 18 18"><path d="M5 5l8 8M13 5l-8 8" fill="none" stroke="${color}"` +
  ` stroke-width="2" stroke-linecap="round"/></svg>`;

// ---- Motion helpers --------------------------------------------------------------------------------------
/**
 * Show `node` from `start` to `end`: a spring entrance animating opacity, rise and scale together, and a
 * faster eased exit. Returns the resulting opacity.
 */
function show(node, frame, start, end, { dx = 0, dy = 26, scale = 0.96, config = theme.spring.snappy } = {}) {
  // Snap the last 2% of a spring: it moves less than a pixel there, but would make every frame differ.
  const raw = spring(frame - start, fps, config);
  const enter = Math.abs(1 - raw) < 0.02 && frame - start > 2 ? 1 : raw;
  const leave = end === undefined ? 0 : interpolate(frame, [end - EXIT, end], [0, 1], ease.in);
  // Hidden by opacity, not visibility: opacity also hides children that are shown on their own.
  if (frame < start || (end !== undefined && frame >= end) || enter <= 0) {
    node.style.opacity = "0";
    return 0;
  }
  const opacity = Math.min(1, enter) * (1 - leave);
  node.style.opacity = opacity.toFixed(3);
  const x = dx * (1 - enter);
  const y = dy * (1 - enter) - 14 * leave;
  node.style.transform = `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px) scale(${mix(scale, 1, enter).toFixed(4)})`;
  return opacity;
}
/** The words of a wrapped text grouped by rendered line (transforms do not change offsetTop). */
function linesOf(spans) {
  if (!spans.lines) {
    const rows = new Map();
    for (const span of spans) rows.set(span.offsetTop, [...(rows.get(span.offsetTop) || []), span]);
    spans.lines = [...rows.values()];
  }
  return spans.lines;
}
/** Reveal a wrapped text line by line, each line's words moving as one; returns when the last line starts. */
function staggerLines(spans, frame, start, end, options) {
  const lines = linesOf(spans);
  lines.forEach((line, i) => line.forEach((span) => show(span, frame, start + i * theme.stagger.block, end, options)));
  return start + (lines.length - 1) * theme.stagger.block;
}
/** Staggered entrances for a list of nodes, sharing one exit. */
function stagger(nodes, frame, start, end, per, options) {
  nodes.forEach((node, i) => show(node, frame, start + i * per, end, options));
}
const wordMotion = { dy: 22, scale: 0.94, config: theme.spring.snappy };
const cardMotion = { dy: 24, scale: 0.97, config: theme.spring.card };

// ---- Layer 1: background mesh (static, so held frames stay identical) --------------------------------------
const bg = el(stage, "layer");
bg.id = "bg";
el(bg, "blob").id = "blob-a";
el(bg, "blob").id = "blob-b";

// ---- Layer 2: assets, the real captures -------------------------------------------------------------------
const assets = el(stage, "layer");
const PAGE = { x: 500, y: 163, crop: 200, scale: 521 / 880 };
const page = at(el(assets, "card"), PAGE.x, PAGE.y);
page.id = "page";
const pageImage = el(page, "", "", "img");
pageImage.src = `${CAPTURE}page.png`;
pageImage.style.width = `${1280 * PAGE.scale}px`;
pageImage.style.height = `${800 * PAGE.scale}px`;
pageImage.style.marginLeft = `${-PAGE.crop * PAGE.scale}px`;

const POPUP = { x: 796, y: 143 };
const popup = at(el(assets, "card"), POPUP.x, POPUP.y);
popup.id = "popup";
const shotImages = {};
for (const { name } of capture.shots) {
  const image = el(popup, "", "", "img");
  image.src = `${CAPTURE}${name}.png`;
  image.style.visibility = "hidden";
  shotImages[name] = image;
}

// ---- Layer 3: graphics and type ----------------------------------------------------------------------------
const graphics = el(stage, "layer");

const hook = at(el(graphics), 96, 236);
hook.id = "hook";
const hookPill = el(hook, "pill label");
el(hookPill, "dot");
el(hookPill, "", "CV Tailor", "span");
const hookLine1 = el(hook, "hook-line");
hookLine1.style.marginTop = "34px";
const hookWords1 = words(hookLine1, "Tailor your CV to the job,");
const hookLine2 = el(hook, "hook-line dim");
const hookWords2 = words(hookLine2, "without letting an AI invent experience.", "without");
const hookSub = el(hook, "sub", "A browser extension and a companion on your machine. Open source.");
hookSub.style.marginTop = "30px";

/** The caption column: step label, title (revealed word by word) and an optional line under it. */
function caption(step, title, sub, top = 228) {
  const box = at(el(graphics), 64, top);
  box.style.width = "410px";
  const label = el(box, "label", `Step ${step} of 5`);
  const heading = el(box, "title");
  heading.style.marginTop = "18px";
  const titleWords = words(heading, title);
  const line = sub ? el(box, "sub", sub) : null;
  if (line) line.style.marginTop = "20px";
  return { box, label, titleWords, line };
}
function playCaption(c, frame, scene) {
  show(c.label, frame, scene.from + sec(0.1), scene.to);
  const lastLine = staggerLines(c.titleWords, frame, scene.from + sec(0.25), scene.to, wordMotion);
  if (c.line) show(c.line, frame, lastLine + theme.stagger.block, scene.to);
}

const captions = {
  send: caption(1, "Send the job you're reading",
    "The extension captures the listing and passes it to a small companion running on your machine."),
  plan: caption(2, "An AI drafts a plan. It can't touch your file.",
    "Through OpenCode it sends back JSON: which paragraph, its exact current text, the rewrite and why."),
  ask: caption(3, "Missing evidence? It asks instead of guessing.",
    "Your answer is kept with its question, so you aren't asked twice."),
  checks: caption(4, "Code checks every edit against your real CV", "", 190),
  report: caption(5, "Every change comes with its reason",
    "What your CV can't back up is listed for you, not invented."),
};

const CHECKS = [
  "The quoted original must match your CV exactly",
  "Only editable paragraphs can change",
  "Every rewrite has a length cap",
  "Your master CV's hash is verified after each stage",
];
const checkList = el(captions.checks.box, "checks");
const checkItems = CHECKS.map((text) => {
  const row = el(checkList, "check");
  row.innerHTML = checkIcon(colors.text);
  el(row, "", text, "span");
  return row;
});

// The plan excerpt: real keys and values from result.json, cut to fit.
const clip = (text, length) => (text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text);
const code = at(el(graphics, "code"), 470, 132);
const codeLines = [
  ["{", ""],
  ['  "paragraph_id": ', JSON.stringify(edit.paragraph_id)],
  ['  "original_text": ', JSON.stringify(clip(edit.original_text, 22))],
  ['  "new_text": ', JSON.stringify(clip(edit.new_text, 27))],
  ['  "reason": ', JSON.stringify(clip(edit.reason, 29))],
  ["}", ""],
].map(([key, value]) => {
  const line = el(code, "line");
  el(line, "key", key, "span");
  el(line, "", value, "span");
  return line;
});

// The report card: the first edit before and after, and the requirements left unsupported.
const report = at(el(graphics, "report"), 500, 128);
const reportHead = el(report, "head");
el(reportHead, "tag", "Before");
el(reportHead, "label", edit.paragraph_id);
const before = el(report, "before", edit.original_text);
const strikes = [];
const afterTag = el(report, "tag", "After");
afterTag.style.marginTop = "18px";
afterTag.style.color = "var(--primary)";
const after = el(report, "after");
const afterWords = words(after, edit.new_text);
const why = el(report, "why", `Why: ${edit.reason}`);
el(report, "rule");
const gapsTag = el(report, "tag", "Not supported by your CV, so not claimed");
gapsTag.style.color = "var(--accent)";
const gaps = plan.unsupported_requirements.slice(0, 3).map((text) => {
  const row = el(report, "gap");
  row.innerHTML = crossIcon(colors.accent);
  el(row, "", text, "span");
  return row;
});

const cta = at(el(graphics), 0, 214);
cta.style.width = `${width}px`;
cta.style.textAlign = "center";
const ctaLine1 = el(cta, "hook-line", "The model proposes.");
const ctaLine2 = el(cta, "hook-line");
const ctaWords2 = words(ctaLine2, "Code decides.", "decides.");
const ctaInstall = el(cta, "cta-install", 'pip install "git+https://github.com/Chika256/CV-Tailor@v0.1.0"');
ctaInstall.style.marginTop = "44px";
const ctaLink = el(cta, "sub", "github.com/Chika256/CV-Tailor · MIT licence");
ctaLink.style.marginTop = "22px";

const ring = at(el(graphics), 0, 0);
ring.id = "ring";

// ---- Layers 4 and 5: grade, grain, vignette -----------------------------------------------------------------
const grade = el(stage, "layer");
el(grade, "fill").id = "grade-tint";
el(grade, "fill").id = "grade-fall";
// ?grain=0 leaves the grain out: the GIF cannot afford noise in every region that moves.
if (new URLSearchParams(location.search).get("grain") !== "0") el(stage, "layer").id = "grain";
el(stage, "layer").id = "vignette";

// ---- Popup screenshots and the highlight ring ---------------------------------------------------------------
const SHOTS = [
  [T.send.from, "01-ready"], [T.send.from + sec(4.2), "02-sent"], [T.plan.from + VISUAL, "03-running"],
  [T.ask.from + VISUAL, "04-questions"], [T.ask.from + sec(4.4), "05-answer"], [T.checks.from + VISUAL, "06-done"],
];
const SWAP = sec(0.44);
function drawPopup(frame) {
  let current = 0;
  while (current + 1 < SHOTS.length && frame >= SHOTS[current + 1][0]) current++;
  const [swapAt, name] = SHOTS[current];
  const previous = current > 0 ? SHOTS[current - 1][1] : null;
  const p = current > 0 ? interpolate(frame, [swapAt, swapAt + SWAP], [0, 1], ease.inOut) : 1;
  for (const [shot, image] of Object.entries(shotImages)) {
    if (shot === name) {
      // A wipe from the top rather than a crossfade: each pixel changes fully, once. A fade's last, faint
      // steps are skipped by the lossy WebP encoder and leave the old screenshot showing through.
      image.style.visibility = "visible";
      image.style.clipPath = `inset(0 0 ${(100 * (1 - p)).toFixed(2)}% 0)`;
      image.style.zIndex = "2";
    } else if (shot === previous && p < 1) {
      image.style.visibility = "visible";
      image.style.clipPath = "none";
      image.style.zIndex = "1";
    } else {
      image.style.visibility = "hidden";
    }
  }
}

const union = (...boxes) => {
  const x = Math.min(...boxes.map((b) => b.x)), y = Math.min(...boxes.map((b) => b.y));
  return { x, y, w: Math.max(...boxes.map((b) => b.x + b.w)) - x, h: Math.max(...boxes.map((b) => b.y + b.h)) - y };
};
// Where the ring sits, in popup coordinates: [from, to, box]. Consecutive targets in one scene glide.
const RINGS = [
  [T.send.from + sec(2.8), T.send.from + sec(4.2), marks["01-ready"].send],
  [T.plan.from + sec(3.4), T.plan.to, union(marks["03-running"].badge, marks["03-running"].message)],
  [T.ask.from + sec(2.5), T.ask.from + sec(4.4), marks["04-questions"].questions],
  [T.ask.from + sec(4.4), T.ask.to, marks["05-answer"].answer],
  [T.checks.from + sec(4.2), T.checks.to, union(marks["06-done"].badge, marks["06-done"].message)],
];
const PAD = 6;
const POPUP_HEIGHT = 514;
/** Keep a highlighted box, ring included, inside the popup's visible area. */
function inPopup(box) {
  const y = Math.max(box.y, PAD + 2);
  return { x: box.x, y, w: box.w, h: Math.min(box.y + box.h, POPUP_HEIGHT - PAD - 2) - y };
}
function drawRing(frame) {
  const index = RINGS.findIndex(([from, to]) => frame >= from && frame < to);
  if (index < 0) {
    ring.style.opacity = "0";
    return;
  }
  const [from, to, raw] = RINGS[index];
  const box = inPopup(raw);
  const glidesIn = index > 0 && RINGS[index - 1][1] === from;
  const glidesOut = index + 1 < RINGS.length && RINGS[index + 1][0] === to;
  let target = box;
  if (glidesIn) {
    const p = interpolate(frame, [from, from + sec(0.5)], [0, 1], ease.inOut);
    const old = inPopup(RINGS[index - 1][2]);
    target = { x: mix(old.x, box.x, p), y: mix(old.y, box.y, p), w: mix(old.w, box.w, p), h: mix(old.h, box.h, p) };
  }
  ring.style.left = `${POPUP.x + target.x - PAD}px`;
  ring.style.top = `${POPUP.y + target.y - PAD}px`;
  ring.style.width = `${target.w + 2 * PAD}px`;
  ring.style.height = `${target.h + 2 * PAD}px`;
  // A press on "Send current job" just before the popup changes.
  const press = index === 0 ? interpolate(frame, [to - sec(0.9), to - sec(0.7), to - sec(0.5)], [1, 0.95, 1], ease.inOut) : 1;
  show(ring, frame, glidesIn ? -1e6 : from, glidesOut ? undefined : to, { dy: 0, scale: 1.12 });
  if (press !== 1) ring.style.transform += ` scale(${press.toFixed(4)})`;
}

// ---- The frame function --------------------------------------------------------------------------------------
function renderFrame(frame) {
  // Hook
  show(hookPill, frame, sec(0.1), T.hook.to);
  stagger(hookWords1, frame, sec(0.3), T.hook.to, theme.stagger.word, wordMotion);
  stagger(hookWords2, frame, sec(0.3) + hookWords1.length * theme.stagger.word + sec(0.15), T.hook.to,
    theme.stagger.word, wordMotion);
  show(hookSub, frame, sec(1.9), T.hook.to);

  // Steps 1 to 5: captions, job page, popup
  for (const name of ["send", "plan", "ask", "checks", "report"]) playCaption(captions[name], frame, T[name]);
  show(page, frame, T.send.from + VISUAL, T.send.to, cardMotion);
  show(popup, frame, T.send.from + VISUAL + theme.stagger.block, T.report.from, { ...cardMotion, dx: 40, dy: 10 });
  drawPopup(frame);
  drawRing(frame);

  // Step 2: the plan excerpt, line by line
  show(code, frame, T.plan.from + sec(2.3), T.plan.to, cardMotion);
  stagger(codeLines, frame, T.plan.from + sec(2.5), T.plan.to, theme.stagger.word, { dy: 10, scale: 0.98 });

  // Step 4: the checks, each ticked as it lands
  checkItems.forEach((row, i) => {
    const start = T.checks.from + sec(2.2) + i * theme.stagger.item * 2;
    show(row, frame, start, T.checks.to, { dy: 16, scale: 0.97 });
    const tick = interpolate(frame, [start + sec(0.2), start + sec(0.6)], [14, 0], ease.out);
    row.querySelector(".tick").setAttribute("stroke-dashoffset", tick.toFixed(2));
  });

  // Step 5: the report, the payoff
  const r = T.report;
  show(report, frame, r.from + VISUAL, r.to, cardMotion);
  drawStrike(frame, r.from + sec(2.6));
  show(afterTag, frame, r.from + sec(3.1), r.to, { dy: 12 });
  staggerLines(afterWords, frame, r.from + sec(3.2), r.to, wordMotion);
  show(why, frame, r.from + sec(4.6), r.to, { dy: 12 });
  show(gapsTag, frame, r.from + sec(5.4), r.to, { dy: 12 });
  stagger(gaps, frame, r.from + sec(5.6), r.to, theme.stagger.item * 2, { dy: 14, scale: 0.97 });

  // Call to action
  const c = T.cta;
  show(ctaLine1, frame, c.from + sec(0.1), c.to);
  stagger(ctaWords2, frame, c.from + sec(0.5), c.to, theme.stagger.word * 2, wordMotion);
  show(ctaInstall, frame, c.from + sec(1.2), c.to, { dy: 20 });
  show(ctaLink, frame, c.from + sec(1.5), c.to, { dy: 14 });
}

/** One strike line per rendered line of the "before" text, drawn left to right in sequence. */
function drawStrike(frame, start) {
  if (!strikes.length) {
    const range = document.createRange();
    range.selectNodeContents(before);
    const origin = before.getBoundingClientRect();
    for (const line of range.getClientRects()) {
      const strike = el(before, "strike");
      strike.style.top = `${line.top - origin.top + line.height / 2}px`;
      strike.style.left = `${line.left - origin.left}px`;
      strike.style.width = `${line.width}px`;
      strikes.push(strike);
    }
  }
  strikes.forEach((strike, i) => {
    const p = interpolate(frame, [start + i * sec(0.25), start + (i + 1) * sec(0.25)], [0, 1], ease.inOut);
    strike.style.transform = `scaleX(${p.toFixed(4)})`;
  });
}

await document.fonts.ready;
await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
window.composition = { width, height, fps, durationInFrames, scenes: T };
window.renderFrame = renderFrame;
renderFrame(0);
