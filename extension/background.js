importScripts("connection.js");

const ALARM_NAME = "cv-tailor-status";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "watch-job" || !message.jobId) return;
  watchJob(message.jobId)
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    pollWatchedJobs();
  }
});

async function watchJob(jobId) {
  const stored = await chrome.storage.local.get(["watchedJobs"]);
  const watched = new Set(stored.watchedJobs || []);
  watched.add(jobId);
  await chrome.storage.local.set({ watchedJobs: [...watched] });
  await chrome.action.setBadgeBackgroundColor({ color: "#93662e" });
  await chrome.action.setBadgeText({ text: "..." });
  await chrome.alarms.create(ALARM_NAME, { periodInMinutes: 0.5 });
}

async function pollWatchedJobs() {
  const stored = await chrome.storage.local.get(["pairingToken", "watchedJobs"]);
  if (!stored.pairingToken || !stored.watchedJobs?.length) {
    await chrome.alarms.clear(ALARM_NAME);
    return;
  }
  const serverUrl = await getServerUrl();
  const remaining = [];
  let hasWarning = false;
  let hasCompleted = false;

  for (const jobId of stored.watchedJobs) {
    try {
      const response = await fetch(`${serverUrl}/jobs/${encodeURIComponent(jobId)}`, {
        headers: { Authorization: `Bearer ${stored.pairingToken}` },
      });
      if (!response.ok) {
        remaining.push(jobId);
        continue;
      }
      const job = await response.json();
      if (["completed", "completed_with_warning", "failed", "needs_clarification", "needs_confirmation"].includes(job.state)) {
        hasCompleted ||= job.state === "completed";
        hasWarning ||= job.state !== "completed";
      } else {
        remaining.push(jobId);
      }
    } catch {
      remaining.push(jobId);
    }
  }

  await chrome.storage.local.set({ watchedJobs: remaining });
  if (!remaining.length) {
    await chrome.alarms.clear(ALARM_NAME);
    await chrome.action.setBadgeBackgroundColor({ color: hasWarning ? "#b07329" : "#185a4b" });
    await chrome.action.setBadgeText({ text: hasWarning ? "!" : hasCompleted ? "OK" : "" });
  }
}
