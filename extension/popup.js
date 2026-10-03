// Loaded after connection.js, which provides getServerUrl, getCompanionPort and setCompanionPort.

const sendButton = document.querySelector("#send-job");
const manualButton = document.querySelector("#send-manual");
const refreshButton = document.querySelector("#refresh");
const statusElement = document.querySelector("#status");
const jobList = document.querySelector("#job-list");
const POLL_MILLISECONDS = 3000;
let lastJobsSignature = "";
let drafts = {};

// Disabled while a request runs; aria-busy drives the loading spinner.
function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function setStatus(message, kind = "") {
  statusElement.textContent = message;
  statusElement.className = `status ${kind}`.trim();
  // The status line sits at the top; make sure a result is never hidden off-screen.
  statusElement.scrollIntoView({ block: "nearest" });
}

async function getConnection() {
  const serverUrl = await getServerUrl();
  const stored = await chrome.storage.local.get("pairingToken");
  let token = stored.pairingToken;
  if (!token) {
    const response = await fetch(`${serverUrl}/pair`, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.token) {
      const error = new Error(body.error || "Could not pair with the local companion");
      error.status = response.status;
      throw error;
    }
    token = body.token;
    await chrome.storage.local.set({ pairingToken: token });
  }
  return { serverUrl, token };
}

async function companionFetch(path, options = {}, retried = false) {
  const { serverUrl, token } = await getConnection();
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${serverUrl}${path}`, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && !retried) {
    await chrome.storage.local.remove("pairingToken");
    return companionFetch(path, options, true);
  }
  if (!response.ok) {
    const error = new Error(body.error || `Companion request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function currentTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs[0]?.id || !tabs[0]?.url) {
    throw new Error("No active job page was found");
  }
  return tabs[0];
}

async function extractCurrentJob() {
  const tab = await currentTab();
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: extractJobPage,
  });
  if (!result) {
    throw new Error("The page did not return any job information");
  }
  return result;
}

async function submitJob(payload) {
  const job = await companionFetch("/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await chrome.runtime.sendMessage({ type: "watch-job", jobId: job.job_id });
  setStatus(
    job.duplicate
      ? "This listing was already processed, so it was not run again. Its result is below."
      : "Job queued. The companion will tailor and check a copy of your CV automatically.",
    "success"
  );
  await refreshJobs(true);
}

sendButton.addEventListener("click", async () => {
  setBusy(sendButton, true);
  setStatus("Expanding and reading the current job listing...");
  try {
    const job = await extractCurrentJob();
    document.querySelector("#manual-title").value = job.title || "";
    document.querySelector("#manual-company").value = job.company || "";
    document.querySelector("#manual-description").value = job.description || "";
    await submitJob(job);
  } catch (error) {
    setStatus(`${error.message} Use the manual panel if the site blocks extraction.`, "error");
    document.querySelector("#manual-panel").open = true;
  } finally {
    setBusy(sendButton, false);
  }
});

const manualResult = document.querySelector("#manual-result");

manualButton.addEventListener("click", async () => {
  setBusy(manualButton, true);
  manualResult.textContent = "";
  try {
    const tab = await currentTab();
    await submitJob({
      url: tab.url,
      title: document.querySelector("#manual-title").value,
      company: document.querySelector("#manual-company").value,
      description: document.querySelector("#manual-description").value,
      location: "",
      source: new URL(tab.url).hostname,
      extraction: { method: "manual" },
    });
    document.querySelector("#manual-panel").open = false;
  } catch (error) {
    // Shown beside the button as well, because the status line is above the open panel.
    manualResult.textContent = error.message;
    setStatus(error.message, "error");
  } finally {
    setBusy(manualButton, false);
  }
});

refreshButton.addEventListener("click", () => refreshJobs());

async function refreshJobs(preserveStatus = false) {
  try {
    const response = await companionFetch("/jobs");
    const signature = JSON.stringify(response.jobs || []);
    if (signature !== lastJobsSignature) {
      lastJobsSignature = signature;
      renderJobs(response.jobs || []);
      if (typeof updateCvSelect === "function") updateCvSelect(response.jobs || []);
    }
    if (!preserveStatus) {
      setStatus(
        response.jobs?.length
          ? "Companion connected. Ready to send the current job."
          : "Companion connected. No jobs have been submitted yet.",
        "success"
      );
    }
  } catch (error) {
    jobList.replaceChildren();
    const message = error.status === 401 || error.status === 403
      ? `Companion authentication failed: ${error.message}. The companion is running; update and restart it.`
      : error.status
        ? `Companion request failed: ${error.message}.`
        : `Cannot reach the companion at ${await getServerUrl()}. Start it with: cv-tailor serve, `
          + `or set the port under Companion connection. ${error.message}`;
    setStatus(message, "error");
  }
}

function renderJobs(jobs) {
  jobList.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No captured jobs.";
    jobList.append(empty);
    return;
  }

  for (const job of jobs) {
    const card = document.createElement("article");
    card.className = `job-card ${job.state || ""}`;

    const title = document.createElement("p");
    title.className = "job-title";
    title.textContent = `${job.company || "Unknown company"} - ${job.title || "Unknown role"}`;
    card.append(title);

    const meta = document.createElement("p");
    meta.className = `badge state-${job.state || "unknown"}`;
    meta.textContent = String(job.state || "unknown").replaceAll("_", " ");
    card.append(meta);

    const message = document.createElement("p");
    message.className = "job-message";
    message.textContent = job.message || "";
    card.append(message);
    if (typeof job.fit_score === "number") {
      const fit = document.createElement("p");
      fit.className = "job-meta";
      fit.textContent = `Keyword fit ${job.fit_score}%` +
        (job.missing_keywords?.length ? ` - not covered: ${job.missing_keywords.slice(0, 6).join(", ")}` : "");
      card.append(fit);
    }
    if (job.state === "needs_confirmation") {
      card.append(actionButton("Tailor anyway", () => post(`/jobs/${job.job_id}/proceed`)));
    }
    if (String(job.state).startsWith("completed")) {
      renderCompleted(card, job);
    }

    if (job.state === "needs_clarification") {
      renderClarification(card, job);
    }
    jobList.append(card);
  }
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  button.textContent = label;
  button.addEventListener("click", async () => {
    setBusy(button, true);
    try {
      await action();
      await refreshJobs(true);
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  });
  return button;
}

function post(path, body = {}) {
  return companionFetch(path, { method: "POST", body: JSON.stringify(body) });
}

function renderCompleted(card, job) {
  const row = document.createElement("div");
  row.className = "action-row";
  row.append(
    actionButton("Open CV", () => post(`/jobs/${job.job_id}/open`, { what: "cv" })),
    actionButton("Review changes", () => post(`/jobs/${job.job_id}/open`, { what: "report" }))
  );
  if (job.letter_state === "completed") {
    row.append(actionButton("Open cover letter", () => post(`/jobs/${job.job_id}/open`, { what: "letter" })));
  } else if (job.letter_state === "queued" || job.letter_state === "running") {
    const wait = document.createElement("span");
    wait.className = "job-meta";
    wait.textContent = job.letter_message || "Writing cover letter...";
    row.append(wait);
  } else {
    row.append(actionButton("Write cover letter", () => post(`/jobs/${job.job_id}/cover-letter`)));
  }
  card.append(row);

  const select = document.createElement("select");
  select.setAttribute("aria-label", "Application status");
  for (const value of ["tailored", "applied", "interview", "offer", "rejected", "no_reply", "skipped"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value.replaceAll("_", " ");
    option.selected = (job.application_status || "tailored") === value;
    select.append(option);
  }
  select.addEventListener("change", () =>
    post(`/jobs/${job.job_id}/application`, { status: select.value }).catch((error) => setStatus(error.message, "error"))
  );
  card.append(select);
}

function renderClarification(card, job) {
  const questions = document.createElement("ol");
  questions.className = "questions";
  for (const item of job.questions || []) {
    const entry = document.createElement("li");
    entry.textContent = item.question || String(item);
    questions.append(entry);
  }
  card.append(questions);

  const answer = document.createElement("textarea");
  answer.className = "answer-box";
  answer.placeholder = "Answer the numbered questions here...";
  answer.value = drafts[job.job_id] || "";
  answer.addEventListener("input", () => {
    drafts[job.job_id] = answer.value;
    chrome.storage.local.set({ drafts });
  });
  card.append(answer);

  const submit = document.createElement("button");
  submit.className = "secondary";
  submit.type = "button";
  submit.textContent = "Submit answers and resume";
  submit.addEventListener("click", async () => {
    setBusy(submit, true);
    try {
      await companionFetch(`/jobs/${encodeURIComponent(job.job_id)}/answers`, {
        method: "POST",
        body: JSON.stringify({ answers: answer.value }),
      });
      await chrome.runtime.sendMessage({ type: "watch-job", jobId: job.job_id });
      delete drafts[job.job_id];
      chrome.storage.local.set({ drafts });
      setStatus("Answers submitted and saved to the knowledge base. The unattended run has resumed.", "success");
      await refreshJobs(true);
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(submit, false);
    }
  });
  card.append(submit);
}

async function extractJobPage() {
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const normalize = (value) =>
    String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  const textOf = (element) => normalize(element?.innerText || element?.textContent || "");
  const htmlToText = (html) => {
    const container = document.createElement("div");
    container.innerHTML = String(html || "");
    return textOf(container);
  };
  const firstText = (selectors) => {
    for (const selector of selectors) {
      const text = textOf(document.querySelector(selector));
      if (text) return text;
    }
    return "";
  };
  const findJobPosting = (value) => {
    if (!value || typeof value !== "object") return null;
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = findJobPosting(item);
        if (found) return found;
      }
      return null;
    }
    const type = value["@type"];
    if (type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"))) {
      return value;
    }
    for (const nested of Object.values(value)) {
      const found = findJobPosting(nested);
      if (found) return found;
    }
    return null;
  };

  for (const button of document.querySelectorAll("button")) {
    const label = normalize(button.innerText || button.getAttribute("aria-label"));
    if (/^(show|see|view) more( description)?$/i.test(label) && button.offsetParent !== null) {
      try {
        button.click();
      } catch {}
    }
  }
  await sleep(350);

  let structured = null;
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      structured = findJobPosting(JSON.parse(script.textContent));
      if (structured) break;
    } catch {}
  }

  const candidates = [];
  const addCandidate = (text, method) => {
    const normalized = normalize(text);
    if (normalized.length < 200) return;
    const lowered = normalized.toLowerCase();
    const signals = [
      "responsibil",
      "requirement",
      "qualification",
      "experience",
      "skills",
      "about the role",
      "what you'll",
      "what you will",
    ].filter((token) => lowered.includes(token)).length;
    const methodBonus = method === "json-ld" ? 40000 : /article|main/.test(method) ? -8000 : 20000;
    candidates.push({
      text: normalized.slice(0, 200000),
      method,
      score: Math.min(normalized.length, 30000) + signals * 1200 + methodBonus,
    });
  };

  if (structured?.description) {
    addCandidate(htmlToText(structured.description), "json-ld");
  }

  const selectors = [
    "#jobDescriptionText",
    ".jobs-description__content",
    ".jobs-description-content__text",
    ".show-more-less-html__markup",
    '[data-automation-id="jobPostingDescription"]',
    '[data-testid="job-posting-description"]',
    '[data-testid="job-description"]',
    ".job__description",
    ".job-description",
    ".job-sections",
    "[class*='jobDescription']",
    "[class*='job-description']",
    "article",
    "main",
  ];
  for (const selector of selectors) {
    for (const element of document.querySelectorAll(selector)) {
      addCandidate(textOf(element), `selector:${selector}`);
    }
  }
  candidates.sort((left, right) => right.score - left.score);
  const best = candidates[0] || { text: "", method: "none", score: 0 };

  const organization = structured?.hiringOrganization;
  const address = structured?.jobLocation?.address || structured?.jobLocation?.[0]?.address;
  const title = normalize(
    structured?.title ||
      firstText([
        "h1",
        ".jobs-unified-top-card__job-title",
        '[data-automation-id="jobPostingHeader"] h2',
        '[data-testid="job-title"]',
      ]) ||
      document.title.split(/[|\-–—]/)[0]
  );
  const company = normalize(
    (typeof organization === "object" ? organization?.name : organization) ||
      firstText([
        ".jobs-unified-top-card__company-name",
        ".topcard__org-name-link",
        '[data-automation-id="company"]',
        '[data-testid="company-name"]',
        '[data-testid="inlineHeader-companyName"]',
        ".company",
      ])
  );
  const jobLocation = normalize(
    [address?.addressLocality, address?.addressRegion, address?.addressCountry]
      .filter(Boolean)
      .join(", ") ||
      structured?.jobLocationType ||
      firstText([
        ".jobs-unified-top-card__bullet",
        ".topcard__flavor--bullet",
        '[data-automation-id="locations"]',
        '[data-testid="job-location"]',
      ])
  );

  return {
    url: window.location.href,
    title,
    company,
    location: jobLocation,
    description: best.text,
    source: window.location.hostname,
    extraction: {
      method: best.method,
      score: best.score,
      structuredDataFound: Boolean(structured),
      capturedAt: new Date().toISOString(),
    },
  };
}

// Knowledge base panel
const knowledgeSearch = document.querySelector("#knowledge-search");
const knowledgeList = document.querySelector("#knowledge-list");
const knowledgeStatus = document.querySelector("#knowledge-status");

async function loadKnowledge() {
  try {
    const { items } = await companionFetch(`/knowledge?q=${encodeURIComponent(knowledgeSearch.value)}`);
    knowledgeList.replaceChildren();
    for (const item of items) {
      const row = document.createElement("article");
      row.className = "knowledge-item";
      const text = document.createElement("p");
      text.className = "knowledge-text";
      text.textContent = item.question ? `${item.question}

${item.text}` : item.text;
      const meta = document.createElement("p");
      meta.className = "job-meta";
      meta.textContent = `${item.kind} - ${item.source}`;
      const retire = document.createElement("button");
      retire.type = "button";
      retire.className = "text-button";
      retire.textContent = "Retire";
      retire.addEventListener("click", async () => {
        await companionFetch(`/knowledge/${item.id}/retire`, { method: "POST", body: "{}" });
        loadKnowledge();
      });
      row.append(text, meta, retire);
      knowledgeList.append(row);
    }
    knowledgeStatus.textContent = items.length ? `${items.length} shown` : "Nothing stored matches.";
  } catch (error) {
    knowledgeStatus.textContent = error.message;
  }
}

async function loadApplications() {
  try {
    const summary = await companionFetch("/applications");
    const totals = Object.entries(summary.totals || {}).map(([key, count]) => `${key.replaceAll("_", " ")}: ${count}`);
    document.querySelector("#applications-summary").textContent = totals.length
      ? `Applications - ${totals.join(", ")}`
      : "No applications logged yet.";
  } catch {}
}

knowledgeSearch.addEventListener("input", () => loadKnowledge());
document.querySelector("#knowledge-panel").addEventListener("toggle", (event) => {
  if (event.target.open) {
    loadKnowledge();
    loadApplications();
  }
});
document.querySelector("#knowledge-add").addEventListener("click", async () => {
  const box = document.querySelector("#knowledge-note");
  try {
    await companionFetch("/knowledge/notes", { method: "POST", body: JSON.stringify({ text: box.value }) });
    box.value = "";
    loadKnowledge();
  } catch (error) {
    knowledgeStatus.textContent = error.message;
  }
});
document.querySelector("#knowledge-import-cvs").addEventListener("click", async () => {
  try {
    const result = await companionFetch("/knowledge/import-cvs", { method: "POST", body: "{}" });
    knowledgeStatus.textContent = `Scanned ${result.files} CVs, ${result.added} new statements.`;
    loadKnowledge();
  } catch (error) {
    knowledgeStatus.textContent = error.message;
  }
});
document.querySelector("#knowledge-import-chat").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const result = await companionFetch("/knowledge/import-chat", {
      method: "POST",
      body: JSON.stringify({ label: file.name, text: await file.text() }),
    });
    knowledgeStatus.textContent = `Imported ${result.added} messages from ${file.name}.`;
    loadKnowledge();
  } catch (error) {
    knowledgeStatus.textContent = error.message;
  }
  event.target.value = "";
});

const portInput = document.querySelector("#companion-port");
const portSaveButton = document.querySelector("#companion-port-save");
const connectionResult = document.querySelector("#connection-result");

document.querySelector("#connection-panel").addEventListener("toggle", async (event) => {
  if (event.target.open) {
    portInput.value = await getCompanionPort();
    connectionResult.textContent = "";
  }
});
portInput.addEventListener("input", () => portInput.removeAttribute("aria-invalid"));
portSaveButton.addEventListener("click", async () => {
  setBusy(portSaveButton, true);
  try {
    const previous = await getCompanionPort();
    const port = await setCompanionPort(portInput.value);
    portInput.value = port;
    connectionResult.textContent = `Using ${await getServerUrl()}.`;
    if (port !== previous) {
      // Jobs and badge state came from the previous companion.
      await chrome.action.setBadgeText({ text: "" });
      lastJobsSignature = "";
      await refreshJobs();
    }
  } catch (error) {
    portInput.setAttribute("aria-invalid", "true");
    connectionResult.textContent = error.message;
  } finally {
    setBusy(portSaveButton, false);
  }
});

chrome.storage.local.get("drafts").then((stored) => {
  drafts = stored.drafts || {};
  refreshJobs();
  setInterval(() => {
    if (!document.hidden) refreshJobs(true);
  }, POLL_MILLISECONDS);
});
