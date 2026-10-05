# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). The companion package and the browser extension are versioned separately; the extension's version is given in brackets.

## [Unreleased]

### Added
- A page limit for tailored CVs (`max_pages`, 2 by default; `0` turns it off). A CV over the limit goes straight to revision without an AI page check.
- A plan may remove up to six project or experience bullets (`"remove": true`) to fit the limit or for relevance. Titles, headings, skills lines and an entry's last bullet are never removed, and the report lists each removal with its reason.

### Fixed
- The planning agent now reads its whole input. OpenCode's read tool cuts lines after 2,000 characters, and the knowledge base was written as one JSON line, so once it grew the agent saw only its start.
- Clarification answers and notes reach the agent in full; they were cut to 600 characters, losing any limit stated at the end.
- The keyword-fit check no longer reports everyday words such as "another" and "understand" as missing skills.
- A listing sent as page markup is read as text. Its tag and attribute words ("span", "docs-internal-guid-...") were scored as missing skills, which could pause the job below `min_fit_score`, and the markup was passed to the agent.

### Documentation
- The README demo is a motion-graphics walkthrough of a fresh real run (`docs/demo.webp`), and the "Bad plan rejected" scenario is shown as an animation.
- The tooling that makes the demo, the diagram animations and the token measurements is in `scripts/demo`.

## [0.1.0] - 2026-10-03 (extension 1.4.0)

First public release.

### Tailoring
- The companion asks an OpenCode agent for a JSON plan of paragraph rewrites, validates every replacement against the master CV (the original text must match exactly, only editable paragraphs may change, length is capped) and applies it to a copy of the DOCX using only the standard library.
- The master CV's SHA-256 is recorded before processing and verified after every stage. Agents are read-only and have no shell.
- Missing facts become one batch of clarification questions; answers are stored and reused for later jobs.
- A plan that recommends no changes completes with an unchanged copy and the model's reasons.
- A keyword-fit score for each listing; below `min_fit_score` the job waits for confirmation.
- The last plan for each role family is offered as a starting point for similar roles.
- Cover letters drafted from the same evidence.

### Layout checks
- Page rendering with Microsoft Word (Windows) or LibreOffice (any OS), chosen automatically, or none.
- Free local checks for page count, split paragraphs, stranded headings and lonely bullets. The AI inspects the PDF only when these flag something, or on every job with `ai_qa_mode: "always"`.

### Browser extension
- Captures the open listing, shows each job's progress, opens the change report and the tailored CV.
- Autofills standard application-form fields and attaches the CV. It never fills passwords, demographic questions, identity numbers or pay, never overwrites a filled field and never submits.
- Connects to a companion on any loopback port, set in the popup.
- Application log: mark each job applied, interview, offer or rejected.

### Operations
- `cv-tailor init` creates a workspace (with `--sample`, a fictional CV to try the tool); `cv-tailor doctor` checks the setup; `cv-tailor serve` runs the companion.
- `cv-tailor usage` reports the tokens each agent run used, as OpenCode reported them.
- Two fictional listings in `examples/` to try the sample CV against: one it already fits, one it only partly fits.
- An interactive architecture diagram (`docs/architecture.html`) that steps through five scenarios with real payloads.
- Structured `key=value` logs tagged with the job id, in a rotating file. Logs never contain CV text, listings or answers.
- A usage-limit error pauses the queue and resumes after the reset.
- `GET /health` reports the version and active renderer.

### Security
- The companion listens on `127.0.0.1` only; job endpoints need a bearer token, and pairing is accepted only from an extension origin.
- CI on Linux, macOS and Windows with Python 3.11 to 3.14, ruff, strict mypy, extension tests and a real LibreOffice render; gitleaks secret scanning; Dependabot; actions pinned to commit SHAs.

[Unreleased]: https://github.com/Chika256/CV-Tailor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Chika256/CV-Tailor/releases/tag/v0.1.0
