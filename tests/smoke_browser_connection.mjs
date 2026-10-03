// Run the real extension in an isolated Edge profile. No CV jobs are submitted.
// Set CV_TAILOR_PORT to test a companion on a non-default port; the port is seeded
// before the popup opens, and the Companion connection panel is then exercised.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, stat } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const browser = process.argv[2] || process.env.CV_TAILOR_BROWSER;
if (!browser) throw new Error("Pass a Chrome or Edge executable path, or set CV_TAILOR_BROWSER");
const port = process.env.CV_TAILOR_PORT ? Number(process.env.CV_TAILOR_PORT) : null;
const tempParent = os.tmpdir();
await stat(browser);
const profile = await mkdtemp(path.join(tempParent, "cv-tailor-browser-"));
const extension = path.join(root, "extension");
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer().once("error", reject).listen(0, "127.0.0.1", () => {
    const { port: free } = server.address();
    server.close(() => resolve(free));
  });
});
// A fixed port polled over HTTP: some browser builds do not print the DevTools URL to stderr.
const debuggingPort = await freePort();
const child = spawn(browser, [
  "--headless=new",
  `--remote-debugging-port=${debuggingPort}`,
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
  child.stderr.on("data", (data) => {
    browserOutput += data;
  });
  let endpoint;
  for (let attempt = 0; attempt < 80 && !endpoint; attempt++) {
    try {
      const response = await fetch(`http://127.0.0.1:${debuggingPort}/json/version`);
      endpoint = (await response.json()).webSocketDebuggerUrl;
    } catch {
      await sleep(250);
    }
  }
  assert.ok(endpoint, `Browser did not expose a debugging endpoint. ${browserOutput.slice(-500)}`);
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
  if (port) {
    // Seed the port before the popup's first request, so the default port is never contacted.
    const workerSession = await send("Target.attachToTarget", { targetId: worker.targetId, flatten: true });
    await send("Runtime.evaluate", {
      expression: `chrome.storage.local.set({ companionPort: ${port} })`,
      awaitPromise: true,
    }, workerSession.sessionId);
  }
  // URL.origin is 'null' for chrome-extension URLs in Node; keep the scheme/host explicitly.
  const extensionBase = `chrome-extension://${new URL(worker.url).hostname}`;
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const navigation = await send("Page.navigate", { url: `${extensionBase}/popup.html` }, sessionId);
  assert.ok(!navigation.errorText, `Extension page navigation failed: ${navigation.errorText}`);

  const evaluate = async (expression) => {
    const { result, exceptionDetails } = await send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    assert.ok(!exceptionDetails, `Popup script threw: ${exceptionDetails?.exception?.description}`);
    return result.value;
  };
  const waitForStatus = async (pattern) => {
    let text = "";
    for (let attempt = 0; attempt < 60; attempt++) {
      try {
        text = await evaluate("document.querySelector('#status')?.textContent || ''");
      } catch (error) {
        if (!/execution context/i.test(error.message)) throw error;
      }
      if (pattern.test(text)) break;
      await sleep(250);
    }
    return text;
  };

  const statusText = await waitForStatus(/^Companion connected|failed|Cannot reach/i);
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

  if (port) {
    // Drive the Companion connection panel: reject an invalid port, report an unreachable
    // one by its URL, then switch back and re-pair with the real companion.
    const savePort = (value) => evaluate(`(async () => {
      document.querySelector('#connection-panel').open = true;
      document.querySelector('#companion-port').value = ${JSON.stringify(String(value))};
      document.querySelector('#companion-port-save').click();
      await new Promise((resolve) => setTimeout(resolve, 300));
      return {
        result: document.querySelector('#connection-result').textContent,
        invalid: document.querySelector('#companion-port').getAttribute('aria-invalid'),
        stored: (await chrome.storage.local.get(['companionPort', 'pairingToken'])),
      };
    })()`);

    const rejected = await savePort("80");
    assert.equal(rejected.invalid, "true", "A privileged port must be rejected");
    assert.equal(rejected.stored.companionPort, port, "A rejected port must not be stored");

    const unused = await freePort();
    const moved = await savePort(unused);
    assert.equal(moved.stored.companionPort, unused);
    assert.equal(moved.stored.pairingToken, undefined, "Changing the port must discard the old token");
    const unreachable = await waitForStatus(/Cannot reach/);
    assert.match(unreachable, new RegExp(`127\\.0\\.0\\.1:${unused}`), "The error must name the port it tried");

    await savePort(port);
    const reconnected = await waitForStatus(/^Companion connected/);
    assert.match(reconnected, /^Companion connected/, `Re-pairing failed: ${reconnected}`);
    const repaired = await evaluate("chrome.storage.local.get('pairingToken').then((s) => Boolean(s.pairingToken))");
    assert.equal(repaired, true, "The extension must pair again with the companion");
    console.log(`Port panel passed: rejected 80, reported ${unused} as unreachable, re-paired on ${port}.`);
  }
} finally {
  socket?.close();
  child.stderr.destroy(); // an orphaned browser process must not keep Node alive
  for (const request of pending.values()) clearTimeout(request.timer);
  // The launcher process can exit and hand off to a new browser process (Edge on Windows),
  // so stop every process using this run's unique profile, not just the child's tree.
  const marker = path.basename(profile);
  const [command, args] = process.platform === "win32"
    ? ["powershell.exe", ["-NoProfile", "-Command",
        `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*${marker}*' } | `
        + "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"]]
    : ["pkill", ["-f", marker]];
  child.kill();
  await new Promise((resolve) => {
    const killer = spawn(command, args, { stdio: "ignore" });
    killer.once("exit", resolve);
    killer.once("error", resolve);
  });
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
}
