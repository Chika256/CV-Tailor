# CV Tailor

Tailor your CV to a job listing from the browser, without letting an LLM invent experience.

A Chrome/Edge extension captures the listing you have open. A small local companion asks an LLM (through [OpenCode](https://opencode.ai)) for a *plan* of paragraph rewrites, **validates that plan against your real CV**, applies it to a **copy** of your master DOCX, and checks the layout. Nothing is submitted for you, and nothing leaves your machine except the prompts sent to the model you choose.

> Status: early release (0.1.0). Built with AI assistance and reviewed by its author. See [Limitations](#limitations).

## Why it is built this way

LLM-written CVs fail in two ways: they fabricate, and they wreck formatting. This project makes both hard:

- **The model never touches the file.** It returns JSON; deterministic code validates every replacement (the original text must match your CV exactly, only editable paragraphs may change, length is capped) and applies it to a copy.
- **Your master CV is protected.** Its SHA-256 is recorded before processing and verified after every stage; the OpenCode agents are read-only and have no shell.
- **It asks instead of guessing.** Missing facts become one batch of clarification questions. Your answers are stored with their questions and reused, so you are not asked twice.
- **Layout is checked for free first.** Page count and awkward breaks are checked locally; the AI only looks at the PDF if something is flagged.
- **Accuracy rules live in one file** (`CV_TAILORING_AGENT.md`) that you can read and change.

## How it works

```
 browser extension ──job text──▶ local companion (127.0.0.1) ──input.md──▶ OpenCode agent
        ▲                              │                                         │
        │                              │ ◀──────── JSON plan ────────────────────┘
        │                              ├─ validate plan against master CV
        │                              ├─ apply to a copy of the DOCX (stdlib only)
        │                              ├─ render + layout check (Word or LibreOffice)
        └───── status, review ─────────┴─ tailored DOCX, change report, cover letter
```

## Quick start

Requires Python 3.11+, [OpenCode](https://opencode.ai) signed in to a model provider, and Chrome or Edge.

```bash
pip install -e .                      # from a clone of this repository
cv-tailor init ~/cv-tailor-workspace --model provider/model --sample
cd ~/cv-tailor-workspace
cv-tailor doctor                      # checks everything is in place
cv-tailor serve
```

List the models you can use with `opencode models`. `--sample` creates a fictional master CV so you can try the tool; to use your own, replace `data/master_cv.docx`.

Then load the extension: open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the `extension/` folder. Open a job listing and click **Send current job**.

Your workspace holds all personal data (`cv-tailor.json`, `data/`). Keep it outside this repository.

## Page rendering

| Backend | Where | What you get |
| --- | --- | --- |
| `word` | Windows with Microsoft Word | Exact per-paragraph page positions and PDF |
| `libreoffice` | Windows, macOS, Linux | PDF and page count; paragraph positions if `pip install pypdf` |
| `none` | anywhere | Tailoring works; layout checks and PDF review are skipped |

`render_backend: "auto"` (default) picks Word, then LibreOffice, then none.

## Configuration

`cv-tailor.json` in the workspace. Unknown keys and unsafe values are rejected with a clear message.

| Key | Default | Meaning |
| --- | --- | --- |
| `opencode_model` | *(required)* | `provider/model` used for tailoring |
| `agent_models`, `agent_variants` | `{}` | Per-agent model and reasoning effort (agents: `cv-tailor`, `cv-tailor-revise`, `cv-tailor-qa`, `cv-tailor-letter`). The QA agent reads a PDF, so choose a model that accepts PDFs |
| `render_backend` | `auto` | `auto`, `word`, `libreoffice`, `none` |
| `min_fit_score` | `20` | Below this keyword fit the job waits for confirmation (`0` disables) |
| `ai_qa_mode` | `on_failure` | `always` to have the AI inspect every PDF |
| `use_templates` | `true` | Offer the last plan for a similar role as a starting point |
| `candidate_name` | *(from CV)* | Prefix for output filenames |
| `port` | `8765` | Loopback only; if you change it, set the same port in the extension under **Companion connection** |

## Safety model

- The companion binds to `127.0.0.1` only and requires a generated bearer token. Pairing is accepted only from a `chrome-extension://` origin.
- Autofill never touches passwords, demographic or diversity questions, date of birth, identity numbers or pay, never overwrites a filled field, and never submits.
- Agents are read-only, have no shell, and are told to read a single input file.
- A usage-limit error pauses the queue and resumes after the reset rather than failing.

## Development

```bash
python -m unittest discover -s tests -p "test_*.py"
```

The suite includes an end-to-end run with a stand-in for OpenCode, so it needs neither network nor Word. `tests/smoke_browser_connection.mjs` is a manual check of the real extension in an isolated browser profile.

Layout: `cv_tailor/` (companion), `extension/` (browser extension), `cv_tailor/templates/` (agent prompts and guardrails copied into each workspace), `docs/` (design notes).

## Limitations

- Paragraph-level edits only; paragraphs containing tabs, hyperlinks, fields, text boxes or tracked changes are locked.
- Mixed formatting inside a rewritten paragraph collapses to its first run's formatting.
- The extension is not published to a web store; load it unpacked.
- Free-text application questions are left for you. Autofill covers standard fields.
- LibreOffice layout checks without `pypdf` cover page count only.

## Licence

MIT. The bundled Geist font is under the SIL Open Font Licence (`extension/fonts/OFL.txt`).
