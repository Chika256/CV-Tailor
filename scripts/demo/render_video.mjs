// Render scripts/demo/video (a frame-driven composition) to PNG frames in headless Edge or Chrome.
// Every frame is drawn by window.renderFrame(n) and screenshotted, so the output does not depend on
// timing: the same frame always renders the same way.
//
// Usage: node scripts/demo/render_video.mjs              all frames -> out/video/frames/00000.png ...
//        node scripts/demo/render_video.mjs 15 60 140    single frames -> out/video/stills/frame-0015.png
//        add --no-grain to leave out the film grain (for the GIF)
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { evaluate, launch, OUT, openPage, REPO, serve, sleep } from "./cdp.mjs";

const args = process.argv.slice(2);
const grain = !args.includes("--no-grain");
const stills = args.filter((arg) => !arg.startsWith("--")).map(Number);
const out = path.join(OUT, "video", stills.length ? "stills" : "frames");
if (!stills.length) await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

const site = await serve(REPO);
const { send, close } = await launch();
try {
  const { session } = await openPage(send, `${site.url}/scripts/demo/video/index.html${grain ? "" : "?grain=0"}`, {
    beforeLoad: `window.__errors = [];
      addEventListener("error", (e) => __errors.push(String(e.message || e)));
      addEventListener("unhandledrejection", (e) => __errors.push(String(e.reason)));`,
  });
  const js = (expression) => evaluate(send, session, expression);
  let composition;
  for (let attempt = 0; attempt < 100 && !composition; attempt++) {
    const errors = await js("window.__errors");
    if (errors?.length) throw new Error(`The composition failed to load: ${errors.join("; ")}`);
    composition = await js("window.composition && JSON.parse(JSON.stringify(window.composition))");
    if (!composition) await sleep(100);
  }
  if (!composition) throw new Error("The composition never defined window.renderFrame");
  const { width, height, fps, durationInFrames } = composition;
  const frames = stills.length ? stills : [...Array(durationInFrames).keys()];
  const started = Date.now();
  for (const frame of frames) {
    await js(`renderFrame(${frame}); new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))`);
    const { data } = await send("Page.captureScreenshot", {
      format: "png", clip: { x: 0, y: 0, width, height, scale: 1 },
    }, session);
    const name = stills.length ? `frame-${String(frame).padStart(4, "0")}.png` : `${String(frame).padStart(5, "0")}.png`;
    await writeFile(path.join(out, name), Buffer.from(data, "base64"));
  }
  const errors = await js("window.__errors");
  if (errors.length) throw new Error(`Errors while rendering: ${errors.join("; ")}`);
  if (!stills.length) {
    await writeFile(path.join(OUT, "video", "composition.json"), JSON.stringify({ ...composition, grain }, null, 2));
  }
  console.log(`${frames.length} frame(s) of ${durationInFrames} (${(durationInFrames / fps).toFixed(1)} s at ${fps} fps) ` +
    `in ${((Date.now() - started) / 1000).toFixed(0)} s -> ${path.relative(REPO, out)}`);
} finally {
  await close();
  await site.close();
}
