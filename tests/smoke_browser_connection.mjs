// Run the real extension in an isolated Edge profile. No CV jobs are submitted.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const browser = process.argv[2] || process.env.CV_TAILOR_BROWSER;
if (!browser) throw new Error("Pass a Chrome or Edge executable path, or set CV_TAILOR_BROWSER");
const tempParent = os.tmpdir();
await stat(browser);
const profile = await mkdtemp(path.join(tempParent, "cv-tailor-browser-"));
const extension = path.join(root, "extension");
const child = spawn(browser, [
  "--headless=new",
  "--remote-debugging-port=0",
  `--user-data-dir=${profile}`,
  `--disable-extensions-except=${extension}`,
  `--load-extension=${extension}`,
  "--disable-sync",
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-background-networking",
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

let socket;
let browserOutput = "";
let nextId = 1;
const pending = new Map();
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

try {
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Browser did not expose a debugging endpoint")), 20000);
    child.once("error", reject);
    child.stderr.on("data", (data) => {
      browserOutput += data;
      const match = browserOutput.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", ({ data }) => {
    const response = JSON.parse(data);
    const request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id);
    clearTimeout(request.timer);
    if (response.error) request.reject(new Error(response.error.message));
    else request.resolve(response.result);
  });

  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Browser command timed out: ${method}`));
    }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });

  let worker;
  for (let attempt = 0; attempt < 40; attempt++) {
    const { targetInfos } = await send("Target.getTargets");
    worker = targetInfos.find((target) =>
      target.url.startsWith("chrome-extension://") && target.url.endsWith("/background.js")
    );
    if (worker) break;
    await sleep(250);
  }
  assert.ok(worker, "The isolated browser did not load the extension");
  // URL.origin is 'null' for chrome-extension URLs in Node; keep the scheme/host explicitly.
  const extensionBase = `chrome-extension://${new URL(worker.url).hostname}`;
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const navigation = await send("Page.navigate", { url: `${extensionBase}/popup.html` }, sessionId);
  assert.ok(!navigation.errorText, `Extension page navigation failed: ${navigation.errorText}`);

  let statusText = "";
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const { result } = await send("Runtime.evaluate", {
        expression: "document.querySelector('#status')?.textContent || ''",
        returnByValue: true,
      }, sessionId);
      statusText = result.value || "";
    } catch (error) {
      if (!/execution context/i.test(error.message)) throw error;
    }
    if (statusText.startsWith("Companion connected")) break;
    if (/failed|Cannot reach/i.test(statusText)) break;
    await sleep(250);
  }
  assert.match(statusText, /^Companion connected/, `Extension connection failed: ${statusText}`);

  // Exercise the same fetch path directly in the extension page with no synthetic
  // Origin header. Verify reads and invalid writes without creating an application.
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression: `(async () => {
      const jobs = await companionFetch('/jobs');
      let invalidStatus = null;
      try { await companionFetch('/jobs', { method: 'POST', body: '{}' }); }
      catch (error) { invalidStatus = error.status; }
      return { jobsIsArray: Array.isArray(jobs.jobs), invalidStatus };
    })()`,
    awaitPromise: true,
    returnByValue: true,
  }, sessionId);
  assert.ok(!exceptionDetails, "The extension fetch code threw an unexpected exception");
  assert.equal(result.value.jobsIsArray, true);
  assert.equal(result.value.invalidStatus, 400, "Authenticated writes must reach payload validation");
  console.log(`Real extension connection passed: ${statusText}`);
  console.log("Authenticated GET succeeded and POST reached validation; no test jobs were created.");
} finally {
  socket?.close();
  for (const request of pending.values()) clearTimeout(request.timer);
  if (child.pid) {
    await new Promise((resolve) => {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      killer.once("exit", resolve);
      killer.once("error", resolve);
    });
  }
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
}
