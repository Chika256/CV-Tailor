# Demo and diagram tooling

The scripts that make the README's animations and the token measurements. They are not part of the
package and are not needed to use CV Tailor.

They drive headless Edge or Chrome over the DevTools protocol (no Playwright or Puppeteer), so they need
**Node 22+** and Edge or Chrome installed (set `DEMO_BROWSER` to the executable if it is somewhere
unusual). The GIF steps need **Pillow**, which is not a project dependency; install it in a separate
virtual environment. Everything is written to `scripts/demo/out/`, which git ignores.

| Script | What it does |
| --- | --- |
| `cdp.mjs` | Shared helpers: find the browser, launch it headless in a throwaway profile, serve a folder on a free localhost port |
| `job.html` | The fictional job board page the demo listing is captured from |
| `capture.mjs` | Runs one real job through the real extension and saves the job page, the popup at each state, and the job's status and report |
| `record_diagram.mjs` | Records each step of one scenario of `docs/architecture.html` as PNG frames |
| `diagram_gif.py` | Turns recorded diagram frames into a GIF |
| `layout_check.mjs` | Checks the interactive diagram at seven widths for overlapping or escaping nodes |
| `drive_jobs.py` | Submits a listing to a test companion N times, answering clarification rounds, for token measurements |

## Never use your real workspace

`capture.mjs` and `drive_jobs.py` send jobs to a companion and refuse the default port 8765, where a real
companion with a real CV may be running. Use a fresh sample workspace on another port:

```bash
cv-tailor init /tmp/cvt-demo --model provider/model --sample
# edit /tmp/cvt-demo/cv-tailor.json: "port": 8799
python -m cv_tailor serve --workspace /tmp/cvt-demo
```

Run the companion from the checkout's root with `python -m cv_tailor` so that it uses the checkout's
code. `capture.mjs` seeds the extension's `companionPort` before the popup opens, so the popup never
contacts 8765.

## Diagram GIFs

```bash
node scripts/demo/record_diagram.mjs dark tailor
node scripts/demo/record_diagram.mjs light tailor
python scripts/demo/diagram_gif.py scripts/demo/out/diagram/tailor-dark docs/architecture-dark.gif
python scripts/demo/diagram_gif.py scripts/demo/out/diagram/tailor-light docs/architecture.gif
```

The same with `reject` makes `docs/architecture-reject-dark.gif` and `docs/architecture-reject.gif`.
Each frame gets its own octree palette: a palette shared across frames, dominated by the background,
washed the coloured wires out to grey.

## Token measurements

```bash
python scripts/demo/drive_jobs.py --runs 5 --listing examples/sample_job_partial_fit.txt --company "Northwind Analytics"
cv-tailor usage --workspace /tmp/cvt-demo
```

## Capturing a real run

```bash
node scripts/demo/capture.mjs --workspace /tmp/cvt-demo --port 8799
```

This sends `job.html` through the real popup, answers the clarification questions (the model sometimes
asks a follow-up round; only the first is captured) and waits for the job to finish. It uses a real
model, so it costs one job's tokens.
