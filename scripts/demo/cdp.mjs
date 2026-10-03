// Minimal Chrome DevTools Protocol helpers for the demo scripts: find a Chromium browser, launch it
// headless in a throwaway profile (optionally with the extension), and serve a folder over HTTP.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const OUT = path.join(REPO, "scripts", "demo", "out");

const BROWSERS = {
  win32: [
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  ],
  darwin: [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ],
  linux: ["/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"],
};

/** Edge or Chrome: $DEMO_BROWSER if set, else the first one installed in its usual place. */
export function findBrowser() {
  const candidates = [process.env.DEMO_BROWSER, ...(BROWSERS[process.platform] || [])].filter(Boolean);
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) throw new Error("No Edge or Chrome found; set DEMO_BROWSER to the browser executable");
  return found;
}

const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer().once("error", reject).listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => resolve(port));
  });
});

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml",
  ".woff2": "font/woff2" };

/** Serve a folder read-only on a free localhost port. Returns { url, close }. */
export async function serve(folder) {
  const root = path.resolve(folder);
  const server = http.createServer(async (request, response) => {
    const file = path.resolve(root, "." + decodeURIComponent(new URL(request.url, "http://x").pathname));
    if (!file.startsWith(root + path.sep)) return response.writeHead(403).end();
    try {
      const body = await readFile(file);
      response.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" }).end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  const port = await freePort();
  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${port}`, close: () => new Promise((resolve) => server.close(resolve)) };
}

export async function launch({ extension, width = 1280, height = 800 } = {}) {
  const profile = await mkdtemp(path.join(os.tmpdir(), "cv-tailor-demo-"));
  // A fixed debugging port: recent Edge builds no longer print "DevTools listening" to stderr.
  const debuggingPort = await freePort();
  const child = spawn(findBrowser(), [
    "--headless=new", `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${profile}`,
    ...(extension ? [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] : []),
    "--disable-sync", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    `--window-size=${width},${height}`, "--hide-scrollbars", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let endpoint;
  for (let attempt = 0; attempt < 80 && !endpoint; attempt++) {
    try {
      endpoint = (await (await fetch(`http://127.0.0.1:${debuggingPort}/json/version`)).json()).webSocketDebuggerUrl;
    } catch { await sleep(250); }
  }
  if (!endpoint) throw new Error("Browser did not expose a debugging endpoint");
  const socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const response = JSON.parse(data);
    const request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id);
    if (response.error) request.reject(new Error(`${request.method}: ${response.error.message}`));
    else request.resolve(response.result);
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject, method });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });
  const close = async () => {
    try { await send("Browser.close"); } catch {}
    child.kill();
    await sleep(500);
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  };
  return { send, close };
}

export async function evaluate(send, sessionId, expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression, awaitPromise: true, returnByValue: true,
  }, sessionId);
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}

/** Wait for a target whose URL ends with `suffix`, attach to it and return its session id. */
export async function attachTo(send, suffix) {
  for (let attempt = 0; attempt < 40; attempt++) {
    const target = (await send("Target.getTargets")).targetInfos.find((t) => t.url.endsWith(suffix));
    if (target) return (await send("Target.attachToTarget", { targetId: target.targetId, flatten: true })).sessionId;
    await sleep(250);
  }
  throw new Error(`No ${suffix} target appeared`);
}

/** A page session at a fixed viewport, with `beforeLoad` run in every document before its own scripts. */
export async function openPage(send, url, { width = 1280, height = 800, beforeLoad = "" } = {}) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const session = (await send("Target.attachToTarget", { targetId, flatten: true })).sessionId;
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false }, session);
  if (beforeLoad) await send("Page.addScriptToEvaluateOnNewDocument", { source: beforeLoad }, session);
  await send("Page.enable", {}, session);
  await send("Page.navigate", { url }, session);
  return { targetId, session };
}
