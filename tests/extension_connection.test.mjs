// Unit tests for extension/connection.js against an in-memory chrome.storage.
// Run with: node --test tests/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const source = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "extension", "connection.js"),
  "utf8",
);

// connection.js is a classic script shared by the popup and the service worker, so it is
// evaluated in a fresh context with a stub of the one Chrome API it uses.
function load(initial = {}) {
  const store = { ...initial };
  const local = {
    get: async (keys) => Object.fromEntries([keys].flat().filter((k) => k in store).map((k) => [k, store[k]])),
    set: async (values) => Object.assign(store, values),
    remove: async (keys) => [keys].flat().forEach((k) => delete store[k]),
  };
  const context = vm.createContext({ chrome: { storage: { local } } });
  vm.runInContext(source, context);
  return { store, run: (expression) => vm.runInContext(expression, context) };
}

test("parsePort accepts only whole numbers from 1024 to 65535", () => {
  const { run } = load();
  const cases = [
    ["8799", 8799], [" 8799 ", 8799], [8765, 8765], ["1024", 1024], ["65535", 65535],
    ["1023", null], ["65536", null], ["80", null], ["8799abc", null], ["1e4", null],
    ["-1", null], ["", null], [null, null], ["123456", null],
  ];
  for (const [input, expected] of cases) {
    assert.equal(run(`parsePort(${JSON.stringify(input)})`), expected, `parsePort(${JSON.stringify(input)})`);
  }
});

test("the server URL is the default loopback port until one is saved", async () => {
  assert.equal(await load().run("getServerUrl()"), "http://127.0.0.1:8765");
  assert.equal(await load({ companionPort: 8799 }).run("getServerUrl()"), "http://127.0.0.1:8799");
});

test("a tampered stored value can never point the token at another host", async () => {
  for (const companionPort of ["evil.example.com", "80", "8799@evil.example.com", { port: 1 }]) {
    assert.equal(await load({ companionPort }).run("getServerUrl()"), "http://127.0.0.1:8765");
  }
});

test("changing the port discards the old companion's token and watched jobs", async () => {
  const { store, run } = load({ pairingToken: "old", watchedJobs: ["j1"], serverUrl: "http://127.0.0.1:8765" });
  assert.equal(await run("setCompanionPort('8799')"), 8799);
  assert.equal(store.companionPort, 8799);
  assert.ok(!("pairingToken" in store) && !("watchedJobs" in store) && !("serverUrl" in store));
});

test("saving the current port keeps the pairing token", async () => {
  const { store, run } = load({ pairingToken: "kept" });
  await run("setCompanionPort(8765)");
  assert.equal(store.pairingToken, "kept");
});

test("an invalid port is rejected with instructions and nothing is stored", async () => {
  const { store, run } = load({ companionPort: 8799 });
  await assert.rejects(run("setCompanionPort('99999')"), /“99999” isn’t a usable port\. Enter a number from 1024 to 65535/);
  await assert.rejects(run("setCompanionPort('  ')"), /Enter the port value/);
  assert.equal(store.companionPort, 8799);
});
