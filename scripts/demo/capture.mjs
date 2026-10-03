// Drive one real CV Tailor job through the real extension in headless Edge or Chrome, and save what
// the demo shows: the job page, the popup at each state (with the position of the parts the video
// points at), and the job's status and change report.
//
// Usage: node scripts/demo/capture.mjs --workspace <test workspace> [--port 8799]
// The workspace's companion must already be running (`python -m cv_tailor serve --workspace ...`).
import { copyFile, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { attachTo, evaluate, launch, OUT, openPage, REPO, serve, sleep } from "./cdp.mjs";

const { values } = parseArgs({ options: { workspace: { type: "string" }, port: { type: "string", default: "8799" } } });
if (!values.workspace) throw new Error("--workspace is required");
const port = Number(values.port);
// 8765 is the default port, where a real companion with a real CV may be running.
if (port === 8765) throw new Error("Use a test workspace on another port, not the default 8765");

const out = path.join(OUT, "capture");
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
const FIRST_ANSWER = "I haven't built scheduled pipelines or used Airflow or dbt yet. In the Inventory Tracker I " +
  "designed the PostgreSQL schema and wrote the SQL queries behind its stock reports.";
const LATER_ANSWER = "No. I haven't loaded data from external APIs, documented reporting tables or written " +
  "data-quality tests. The Inventory Tracker's tests covered its API, not the data.";
// Parts of the popup the video highlights, recorded in each shot's own coordinates.
const MARKS = {
  send: "#send-job", card: ".job-card", badge: ".job-card .badge", message: ".job-card .job-message",
  fit: ".job-card .job-meta", questions: ".job-card .questions", answer: ".job-card .answer-box",
  actions: ".job-card .action-row",
};

const site = await serve(path.join(REPO, "scripts", "demo"));
const { send, close } = await launch({ extension: path.join(REPO, "extension") });
const shots = [];
try {
  const worker = await attachTo(send, "/background.js");
  // Point the extension at the test companion before anything can contact the default port.
  await evaluate(send, worker, `chrome.storage.local.set({ companionPort: ${port} })`);

  const page = await openPage(send, `${site.url}/job.html`);
  await sleep(1200);
  const { data: pageShot } = await send("Page.captureScreenshot", { format: "png" }, page.session);
  await writeFile(path.join(out, "page.png"), Buffer.from(pageShot, "base64"));
  await send("Target.activateTarget", { targetId: page.targetId });
  await evaluate(send, worker, "chrome.action.openPopup()");

  const popup = await attachTo(send, "/popup.html");
  const js = (expression) => evaluate(send, popup, expression);
  for (let i = 0; i < 40; i++) {
    if (/^Companion connected/.test(await js("document.querySelector('#status').textContent"))) break;
    await sleep(250);
  }
  const shot = async (name) => {
    await sleep(300);
    const marks = await js(`(() => {
      const marks = {};
      for (const [key, selector] of Object.entries(${JSON.stringify(MARKS)})) {
        const box = document.querySelector(selector)?.getBoundingClientRect();
        if (box && box.width) marks[key] = { x: box.x, y: box.y, w: box.width, h: box.height };
      }
      return marks;
    })()`);
    const { data } = await send("Page.captureScreenshot", { format: "png" }, popup);
    await writeFile(path.join(out, `${name}.png`), Buffer.from(data, "base64"));
    shots.push({ name, marks });
    console.log("captured", name);
  };
  const scrollToJob = () => js(`(() => {
    const card = document.querySelector('.job-card');
    if (card) card.scrollIntoView({ block: 'end' }); else scrollTo(0, 0);
  })()`);
  const state = () => js("(document.querySelector('.job-card .badge')?.textContent || '').trim()");
  const message = () => js("(document.querySelector('.job-card .job-message')?.textContent || '').trim()");
  const answer = (text) => js(`(() => {
    const box = document.querySelector('.answer-box');
    box.value = ${JSON.stringify(text)};
    box.dispatchEvent(new Event('input'));
    box.scrollIntoView({ block: 'end' });
  })()`);
  const submit = () => js(
    "[...document.querySelectorAll('.job-card button')].find(b => /Submit answers/.test(b.textContent)).click()"
  );

  await shot("01-ready");
  await js("document.querySelector('#send-job').click()");
  await sleep(2500);
  await js("scrollTo(0, 0)");
  await shot("02-sent");

  let seenRunning = false;
  let rounds = 0;
  let final = "";
  const started = Date.now();
  while (Date.now() - started < 15 * 60 * 1000) {
    await js("document.querySelector('#refresh').click()");
    await sleep(1500);
    const now = await state();
    if (now === "running" && !seenRunning && /OpenCode/.test(await message())) {
      seenRunning = true;
      await scrollToJob();
      await shot("03-running");
    } else if (now === "needs clarification" && rounds < 3) {
      rounds++;
      // The model sometimes asks a second, follow-up round; only the first one is shown.
      if (rounds === 1) {
        await scrollToJob();
        await shot("04-questions");
        await answer(FIRST_ANSWER);
        await shot("05-answer");
      } else {
        await answer(LATER_ANSWER);
      }
      await submit();
      console.log(`answered round ${rounds}`);
      await sleep(3000);
    } else if (/^completed/.test(now) || now === "failed") {
      final = now;
      await scrollToJob();
      await shot("06-done");
      break;
    }
    await sleep(1500);
  }
  if (!final) throw new Error("The job did not finish within 15 minutes");

  // A fresh test workspace holds one job: the one just run.
  const jobs = path.join(values.workspace, "data", "runtime", "jobs");
  const jobId = (await readdir(jobs)).sort().at(-1);
  const status = JSON.parse(await readFile(path.join(jobs, jobId, "status.json"), "utf-8"));
  await copyFile(path.join(jobs, jobId, "application_report.md"), path.join(out, "application_report.md"));
  await copyFile(path.join(jobs, jobId, "result.json"), path.join(out, "result.json"));
  await writeFile(path.join(out, "capture.json"),
    JSON.stringify({ jobId, state: final, rounds, shots, status }, null, 2));
  console.log(`done: ${final}, ${rounds} clarification round(s), job ${jobId}`);
} finally {
  await close();
  await site.close();
}
