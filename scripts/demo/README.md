# Demo and diagram tooling

The scripts that make the README demo, the diagram animations and the token measurements. They are not part of the
package and are not needed to use CV Tailor.

They drive headless Edge or Chrome over the DevTools protocol (no Playwright or Puppeteer), so they need
**Node 22+** and Edge or Chrome installed (set `DEMO_BROWSER` to the executable if it is somewhere
unusual). The GIF and WebP steps need **Pillow**, which is not a project dependency; install it in a separate
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
| `video/` | The README demo as a frame-driven composition: `theme.js`, `motion.js` (springs, easing), `demo.js` (scenes) |
| `render_video.mjs` | Renders the composition frame by frame to PNGs, or single frames for checking |
| `encode.py` | Encodes the frames as `docs/demo.webp` (or a GIF) |

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

## The README demo

```bash
node scripts/demo/capture.mjs --workspace /tmp/cvt-demo --port 8799   # a real run, as above
node scripts/demo/render_video.mjs 60 300 820                          # check single frames first
node scripts/demo/render_video.mjs                                     # all 1,060 frames, about 2 minutes
python scripts/demo/encode.py docs/demo.webp
```

`video/demo.js` builds the walkthrough from what `capture.mjs` saved: the job page, the popup at each
state (rings are drawn where the capture recorded each element), and the plan's first change and
unsupported requirements from `result.json`. Nothing on screen is typed in by hand, so a new capture
produces a matching video.

The composition follows the method of
[claude-remotion-skill](https://github.com/haidrrrry/claude-remotion-skill) (MIT): springs and
bezier curves instead of linear motion, entrances that animate opacity, rise and scale together and
are staggered, faster exits, one theme file, one hero colour per frame, a display font (Geist, which
the extension already bundles), a five-layer stack (background, captures, type, grade, grain and
vignette), all timing derived from the frame rate, and a render, inspect, fix loop. Remotion itself is
not used: `render_video.mjs` does the same job with the browser already used here, drawing each frame
with `window.renderFrame(n)` and taking a screenshot.

Some of the skill's rules assume an MP4. A README image has to stay under the 1 MB limit that
pre-commit enforces, so this composition departs from them on purpose:

- Holds are pixel-still and the background does not drift, so the encoder can merge held frames. There
  is no Ken Burns on the captures: a slow zoom changes every pixel of every frame and blurs small UI text.
- Screenshots change with a wipe, not a crossfade. A fade's last faint steps are below what the lossy
  encoder re-encodes and leave the previous screen showing through.
- It is silent; a README image cannot play sound.
- It is encoded at 12.5 fps from the 25 fps composition.

As a GIF the same video is about 2.6 MB without grain, because of the 256-colour palette and whole-
rectangle frame updates; animated WebP keeps full colour at about 0.9 MB.
