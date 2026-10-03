// Check docs/architecture.html at several viewport widths: no overlapping nodes, every node inside
// the stage, and no horizontal page scroll. Usage: node scripts/demo/layout_check.mjs
import path from "node:path";
import { evaluate, launch, openPage, REPO, serve, sleep } from "./cdp.mjs";

const widths = [375, 768, 1024, 1280, 1366, 1480, 1920];
const site = await serve(path.join(REPO, "docs"));
const { send, close } = await launch();
let failures = 0;
try {
  for (const width of widths) {
    const { session, targetId } = await openPage(send, `${site.url}/architecture.html`, {
      width, height: 900, beforeLoad: "try { localStorage.clear(); } catch (e) {}",
    });
    await sleep(1800);
    const result = await evaluate(send, session, `(() => {
      const stage = document.getElementById('stage').getBoundingClientRect();
      const nodes = [...document.querySelectorAll('.node')].map(n => ({ id: n.dataset.id, r: n.getBoundingClientRect() }));
      const overlaps = [], outside = [];
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i].r;
        if (a.left < stage.left - 1 || a.right > stage.right + 1 || a.top < stage.top - 1 || a.bottom > stage.bottom + 1) outside.push(nodes[i].id);
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j].r;
          if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) overlaps.push(nodes[i].id + '/' + nodes[j].id);
        }
      }
      const pageOverflow = document.documentElement.scrollWidth > innerWidth + 1;
      return { stage: Math.round(stage.width), node: Math.round(nodes[0].r.width), overlaps, outside, pageOverflow };
    })()`);
    if (result.overlaps.length || result.outside.length || result.pageOverflow) failures++;
    console.log(width, JSON.stringify(result));
    await send("Target.closeTarget", { targetId });
  }
} finally {
  await close();
  await site.close();
}
process.exitCode = failures ? 1 : 0;
