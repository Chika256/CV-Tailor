# Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="architecture-dark.svg">
  <img alt="How a job flows: the extension sends a listing; an untrusted OpenCode agent reads one input file and returns a JSON plan; the local companion validates it against the real CV, applies it to a copy, checks the layout, and only calls the AI page check if a free check flags a problem." src="architecture.svg">
</picture>

The diagram's source is [architecture.excalidraw](architecture.excalidraw); open it at [excalidraw.com](https://excalidraw.com) to edit it, then export both SVGs (light, and dark with the dark-mode export option).

## Components

| Part | Responsibility |
| --- | --- |
| `extension/` | Captures the listing, shows job status, autofills forms, opens results. Talks only to the companion on `127.0.0.1`. |
| `cv_tailor/server.py` | Entry point for `cv-tailor serve`: configures logging, starts the companion and the HTTP API. |
| `cv_tailor/api.py` | Loopback HTTP API for the extension: pairing, bearer-token auth, CORS and routes. |
| `cv_tailor/companion.py` | Job queue and its single worker thread, the tailoring pipeline, usage-limit pauses. |
| `cv_tailor/agents.py` | Runs OpenCode agents: builds the command, runs the process, detects usage limits, parses the JSON reply. |
| `cv_tailor/intake.py` | Validates a listing submitted by the extension before it becomes a job. |
| `cv_tailor/reports.py` | Writes each job's before/after report; reads the clarification questions saved with a job. |
| `cv_tailor/usage.py` | Sums the token counts OpenCode reports for each agent run into the job's `usage.json`; `cv-tailor usage` reports them. |
| `cv_tailor/logs.py` | One-line `key=value` log events tagged with the job being processed. |
| `cv_tailor/docx_io.py` | Reads and edits DOCX paragraphs with the standard library. Copies every other package part byte for byte. |
| `cv_tailor/docx_ops.py` | Validates tailoring plans and QA results; JSON helpers. |
| `cv_tailor/prep.py` | Builds each agent's single input file, cleans listings, fingerprints jobs, scores keyword fit, checks layout, writes cover-letter DOCX. |
| `cv_tailor/render.py` | Renders a DOCX to PDF and page facts via Word, LibreOffice or nothing. |
| `cv_tailor/knowledge.py` | SQLite store of answers, notes, other-CV evidence and the application log. |
| `cv_tailor/applicant.py` | Editable applicant profile used by autofill. |
| `cv_tailor/templates/` | Agent prompts and `CV_TAILORING_AGENT.md`, copied into a workspace by `cv-tailor init`. |

## Job lifecycle

1. The extension posts the listing. The companion validates it, fingerprints it (a repeat returns the earlier job) and queues it.
2. **Preflight:** hash the master CV, extract its paragraphs to `cv.json`, score keyword fit locally.
3. **Plan:** write `input.md` (cleaned listing, paragraph list, answers, trimmed knowledge base, optional prior plan). One OpenCode agent reads it and returns JSON: a plan, or clarification questions.
4. **Validate:** every replacement must name an editable paragraph and quote its current text exactly; length and total growth are capped.
5. **Apply:** `docx_io.apply_plan` writes a copy under `data/output/`. The master hash is re-checked.
6. **Check:** render, then free structural checks (page count, split paragraphs, stranded headings, lonely bullets). Only on a flag does an AI agent inspect the PDF, and one revision pass is allowed.
7. **Report:** a before/after change report, status, and an entry in the application log.

## Trust boundaries

- The LLM output is untrusted data: it is parsed as JSON and validated before anything is written.
- The agents run read-only with web tools limited to company research in the planning agent.
- Workspace paths (`runtime`, `output`, `cv_library`, `master_cv`) must stay inside the workspace.
- The companion is loopback-only; the bearer token lives in `data/runtime/companion_state.json`.

## Data

Everything personal lives in the workspace, never in this repository: `cv-tailor.json`, `data/master_cv.docx`, `data/cv_library/`, `data/runtime/` (jobs, `knowledge.db`, `profile.json`, templates) and `data/output/`.
