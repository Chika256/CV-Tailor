// Record every step of one scenario of docs/architecture.html, in one theme, as PNG frames.
// Usage: node scripts/demo/record_diagram.mjs <dark|light> [tailor|clarify|reject|layout|letter]
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { evaluate, launch, OUT, openPage, REPO, serve, sleep } from "./cdp.mjs";

const theme = process.argv[2] === "light" ? "light" : "dark";
const flow = process.argv[3] || "tailor";
const out = path.join(OUT, "diagram", `${flow}-${theme}`);
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

const site = await serve(path.join(REPO, "docs"));
const { send, close } = await launch();
try {
  // Start in the chosen theme with no saved layout, so nothing animates on load.
  const { session } = await openPage(send, `${site.url}/architecture.html`, {
    width: 1480, height: 1080,
    beforeLoad: `try { localStorage.clear(); ${theme === "light" ? "localStorage.setItem('arch-theme', 'light');" : ""} } catch (e) {}`,
  });
  await sleep(2500);
  const js = (expression) => evaluate(send, session, expression);
  await js(`document.querySelector('.flowtab[data-flow="${flow}"]').click()`);
  await sleep(1200);
  const total = Number(await js("document.getElementById('panelStepTotal').textContent"));
  for (let step = 1; step <= total; step++) {
    // Let the node and wire transitions finish before the shot.
    await sleep(1100);
    const { data } = await send("Page.captureScreenshot", {
      format: "png", clip: { x: 0, y: 0, width: 1480, height: 1080, scale: 1 },
    }, session);
    await writeFile(path.join(out, `${String(step).padStart(2, "0")}.png`), Buffer.from(data, "base64"));
    if (step < total) await js("document.getElementById('btnNext').click()");
  }
  console.log(`${flow}-${theme}: ${total} frames in ${path.relative(REPO, out)}`);
} finally {
  await close();
  await site.close();
}
