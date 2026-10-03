// Where the local companion listens. Shared by the popup and the background worker.
// The URL is always built from the loopback address and a validated port, so the
// pairing token can never be sent to another host.

const COMPANION_HOST = "127.0.0.1";
const DEFAULT_PORT = 8765;
const MIN_PORT = 1024;
const MAX_PORT = 65535;

function parsePort(value) {
  const text = String(value ?? "").trim();
  if (!/^\d{1,5}$/.test(text)) return null;
  const port = Number(text);
  return port >= MIN_PORT && port <= MAX_PORT ? port : null;
}

async function getCompanionPort() {
  const stored = await chrome.storage.local.get("companionPort");
  return parsePort(stored.companionPort) ?? DEFAULT_PORT;
}

async function getServerUrl() {
  return `http://${COMPANION_HOST}:${await getCompanionPort()}`;
}

// The pairing token and watched jobs belong to one companion, so both are dropped
// when the port changes; the extension re-pairs on its next request.
function describePortError(value) {
  const text = String(value ?? "").trim();
  if (!text) return "Enter the port value from your workspace’s cv-tailor.json.";
  return `“${text}” isn’t a usable port. Enter a number from ${MIN_PORT} to ${MAX_PORT}, `
    + "matching your workspace’s cv-tailor.json.";
}

async function setCompanionPort(port) {
  const parsed = parsePort(port);
  if (parsed === null) {
    throw new Error(describePortError(port));
  }
  if (parsed === (await getCompanionPort())) return parsed;
  await chrome.storage.local.set({ companionPort: parsed });
  await chrome.storage.local.remove(["pairingToken", "watchedJobs", "serverUrl"]);
  return parsed;
}
