// Autofill: popup-side orchestration plus the function injected into the page.
// Loaded after popup.js, so it can use companionFetch, post, setStatus, currentTab and refreshJobs.

const autofillButton = document.querySelector("#autofill");
const autofillCv = document.querySelector("#autofill-cv");
const autofillResult = document.querySelector("#autofill-result");
let selectedCvJob = "";

function normalizeUrl(value) {
  try {
    const parsed = new URL(value);
    return `${parsed.hostname}${parsed.pathname}`.replace(/\/$/, "").toLowerCase();
  } catch {
    return "";
  }
}

async function updateCvSelect(jobs) {
  const completed = jobs.filter((job) => String(job.state).startsWith("completed"));
  let tabUrl = "";
  try {
    tabUrl = normalizeUrl((await currentTab()).url);
  } catch {}
  const preferred =
    selectedCvJob && completed.some((job) => job.job_id === selectedCvJob)
      ? selectedCvJob
      : completed.find((job) => normalizeUrl(job.url) === tabUrl)?.job_id || completed[0]?.job_id || "";
  autofillCv.replaceChildren(new Option("Do not attach a CV", ""));
  for (const job of completed) {
    const label = `${job.company} - ${job.title}${normalizeUrl(job.url) === tabUrl ? " (this page)" : ""}`;
    const option = new Option(label, job.job_id);
    option.selected = job.job_id === preferred;
    autofillCv.append(option);
  }
  selectedCvJob = autofillCv.value;
}

autofillCv.addEventListener("change", () => {
  selectedCvJob = autofillCv.value;
});

autofillButton.addEventListener("click", async () => {
  setBusy(autofillButton, true);
  autofillResult.textContent = "";
  // Must be the first await so it still counts as part of the click.
  const allSites = await chrome.permissions
    .request({ origins: ["http://*/*", "https://*/*"] })
    .catch(() => false);
  try {
    const tab = await currentTab();
    const { profile } = await companionFetch("/profile");
    const files = {};
    if (autofillCv.value) {
      files.cv = await companionFetch(`/jobs/${autofillCv.value}/file?what=cv`);
      try {
        files.letter = await companionFetch(`/jobs/${autofillCv.value}/file?what=letter`);
      } catch {}
    }
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: allSites },
      func: autofillPage,
      args: [profile, files],
    });
    const filled = results.flatMap((entry) => entry.result?.filled || []);
    const unmatched = results.reduce((sum, entry) => sum + (entry.result?.unmatched || 0), 0);
    setStatus(
      filled.length
        ? `Filled ${filled.length} field(s). Review everything, then submit the form yourself.`
        : "Nothing was filled. The form may be on another page, or its fields are already complete.",
      filled.length ? "success" : "error"
    );
    autofillResult.textContent =
      filled.map((item) => `${item.label} -> ${item.what}`).join("\n") +
      (unmatched ? `\n${unmatched} other empty field(s) left for you.` : "") +
      (allSites ? "" : "\nOnly the main page was filled: allow all-site access to include embedded forms.");
  } catch (error) {
    setStatus(`Autofill failed: ${error.message}`, "error");
  } finally {
    setBusy(autofillButton, false);
  }
});

// Profile editor
const profilePanel = document.querySelector("#profile-panel");
const profileForm = document.querySelector("#profile-form");

async function loadProfile() {
  const { profile, fields } = await companionFetch("/profile");
  profileForm.replaceChildren();
  for (const [key, label] of fields) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 500;
    input.name = key;
    input.value = profile[key] || "";
    wrapper.append(input);
    profileForm.append(wrapper);
  }
}

profilePanel.addEventListener("toggle", (event) => {
  if (event.target.open) loadProfile().catch((error) => setStatus(error.message, "error"));
});
document.querySelector("#profile-save").addEventListener("click", async () => {
  const values = Object.fromEntries(
    [...profileForm.querySelectorAll("input")].map((input) => [input.name, input.value])
  );
  try {
    await post("/profile", { values });
    setStatus("Profile saved.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

// Runs inside the page and its frames, so it must not reference anything outside this function.
function autofillPage(profile, files) {
  // Demographic, identity and pay questions are never answered automatically.
  const NEVER =
    /gender|sex\b|ethnic|race\b|racial|disabilit|veteran|religio|sexual|orientation|pronoun|date of birth|birth|\bdob\b|national insurance|passport|social security|\bssn\b|password|captcha|security code|criminal|convict|salary|compensation|pay expectation|remuneration/i;
  const RULES = [
    ["first_name", /first.?name|given.?name|forename/i],
    ["last_name", /last.?name|sur.?name|family.?name/i],
    ["email", /e-?mail/i],
    ["phone", /phone|mobile|telephone|\btel\b/i],
    ["postcode", /post.?code|zip|postal/i],
    ["address_line", /address.?(line)?.?1|street|^address$/i],
    ["city", /\bcity\b|\btown\b|address-level2|locality/i],
    ["country", /country/i],
    ["linkedin", /linkedin/i],
    ["github", /github/i],
    ["website", /website|portfolio|personal (site|url)|homepage/i],
    ["notice_period", /notice period/i],
    ["available_from", /available from|start date|earliest start|when can you start/i],
  ];
  const FULL_NAME = /^(your |full |candidate )?name\b|\bfull.?name\b/i;
  const NOT_PERSON_NAME = /company|school|university|employer|reference|manager|job|file|user|referr|emergency|institution/i;
  const YES_NO = [
    ["right_to_work", /right to work|eligible to work|authori[sz]ed to work|legally (entitled|authori[sz]ed|allowed)|work in the uk/i],
    ["requires_sponsorship", /sponsor/i],
  ];
  const CV_FILE = /\bcv\b|resume|curriculum/i;
  const LETTER_FILE = /cover.?letter|covering/i;

  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && box.width > 0 && box.height > 0;
  };
  const labelOf = (element) => {
    const parts = [];
    for (const label of element.labels || []) parts.push(label.innerText);
    const ids = (element.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    for (const id of ids) parts.push(document.getElementById(id)?.innerText);
    parts.push(
      element.getAttribute("aria-label"),
      element.placeholder,
      element.getAttribute("autocomplete"),
      element.name,
      element.id,
      element.title
    );
    return clean(parts.filter(Boolean).join(" "));
  };
  const questionOf = (element) => {
    const group = element.closest("fieldset, [role='radiogroup'], [role='group']");
    const legend = group?.querySelector("legend, label, [class*='label'], [class*='question']");
    if (legend) return clean(legend.innerText);
    return clean(element.closest("div, li, p")?.innerText).slice(0, 200);
  };
  const setValue = (element, value) => {
    const prototype =
      element instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    for (const type of ["input", "change", "blur"]) element.dispatchEvent(new Event(type, { bubbles: true }));
  };
  const mark = (element) => {
    element.style.outline = "2px solid #185a4b";
    element.style.outlineOffset = "1px";
  };
  const wantedText = (value) => (value === "yes" ? /^(yes|y|true)\b/i : /^(no|n|false)\b/i);

  const filled = [];
  let unmatched = 0;
  const handledGroups = new Set();

  for (const element of document.querySelectorAll("input, select, textarea")) {
    const type = (element.type || "").toLowerCase();
    if (
      element.disabled ||
      element.readOnly ||
      ["hidden", "submit", "button", "image", "reset", "password"].includes(type)
    ) {
      continue;
    }

    if (type === "file") {
      const text = `${labelOf(element)} ${questionOf(element)}`;
      let file = null;
      let what = "";
      if (LETTER_FILE.test(text)) {
        file = files.letter;
        what = "cover letter";
      } else if (CV_FILE.test(text) || document.querySelectorAll("input[type=file]").length === 1) {
        file = files.cv;
        what = "CV";
      }
      if (!file || (element.files && element.files.length)) continue;
      const bytes = Uint8Array.from(atob(file.base64), (character) => character.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([bytes], file.name, {
          type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        })
      );
      element.files = transfer.files;
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      filled.push({ label: file.name, what });
      continue;
    }

    if (!visible(element)) continue;

    if (type === "radio") {
      const name = element.name || "";
      if (!name || handledGroups.has(name)) continue;
      handledGroups.add(name);
      const question = questionOf(element);
      if (NEVER.test(question)) continue;
      const rule = YES_NO.find(([, pattern]) => pattern.test(question));
      const answer = rule && profile[rule[0]];
      if (!answer) {
        unmatched += 1;
        continue;
      }
      const group = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)];
      if (group.some((radio) => radio.checked)) continue;
      const pattern = wantedText(answer);
      const target = group.find((radio) => pattern.test(clean(labelOf(radio))) || pattern.test(radio.value));
      if (target) {
        target.click();
        mark(target);
        filled.push({ label: question.slice(0, 60), what: answer });
      } else {
        unmatched += 1;
      }
      continue;
    }
    if (type === "checkbox") continue;

    const text = `${labelOf(element)} ${element instanceof HTMLSelectElement ? questionOf(element) : ""}`;
    if (NEVER.test(text)) continue;
    if (element instanceof HTMLTextAreaElement) {
      unmatched += 1;
      continue;
    }
    if (!(element instanceof HTMLSelectElement) && (element.value || "").trim()) continue;

    let key = RULES.find(([, pattern]) => pattern.test(text))?.[0];
    if (!key && FULL_NAME.test(text) && !NOT_PERSON_NAME.test(text)) key = "full_name";
    let value = key === "full_name" ? `${profile.first_name} ${profile.last_name}`.trim() : key ? profile[key] : "";

    if (element instanceof HTMLSelectElement) {
      const yesNo = YES_NO.find(([, pattern]) => pattern.test(text));
      if (yesNo) {
        key = yesNo[0];
        value = profile[key];
      }
      if (!key || !value) {
        if (!element.value) unmatched += 1;
        continue;
      }
      const isYesNo = key === "right_to_work" || key === "requires_sponsorship";
      const option = [...element.options].find((candidate) =>
        isYesNo
          ? wantedText(value).test(clean(candidate.text))
          : clean(candidate.text).toLowerCase().includes(value.toLowerCase())
      );
      if (option && element.value !== option.value) {
        setValue(element, option.value);
        mark(element);
        filled.push({ label: clean(labelOf(element)).slice(0, 60), what: option.text.trim() });
      } else if (!option) {
        unmatched += 1;
      }
      continue;
    }

    if (!value) {
      unmatched += 1;
      continue;
    }
    setValue(element, value);
    mark(element);
    filled.push({ label: clean(labelOf(element)).slice(0, 60), what: key });
  }
  return { filled, unmatched };
}
