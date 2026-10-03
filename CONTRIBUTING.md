# Contributing

Thanks for taking a look. Bug reports, fixes and small focused improvements are welcome.

## Before you start

- For anything larger than a bug fix, open an issue first to agree the approach.
- Security problems go through a private advisory, not an issue: see [SECURITY.md](SECURITY.md).
- Never put real personal data in the repository, in an issue or in a test: no CVs, answers, job histories or workspace files. Use the fictional sample (`cv-tailor init --sample`, `examples/sample_job.txt`) to reproduce problems.

## Setting up

See [Development](README.md#development) in the README. In short:

```bash
uv venv && uv pip install -e ".[dev]"
uvx pre-commit install
```

## What a change needs

- **Tests.** A bug fix comes with a test that fails on the old code. The suite runs without network, Word or a model: `tests/test_pipeline.py` drives the whole companion with a stand-in for OpenCode, so extend that stand-in rather than calling a real model from a test.
- **Clean checks.** All of these pass in CI on Linux, macOS and Windows:

  ```bash
  python -m unittest discover -s tests -p "test_*.py"
  node --test tests/*.test.mjs
  ruff check .
  python -m mypy        # strict; add --platform linux, darwin or win32 to check another OS
  ```

- **No new runtime dependencies** without discussion. The companion uses only the standard library, so it installs anywhere Python does; `pypdf` is an optional extra.
- **Guardrails stay intact.** Changes must not let the model edit files directly, write outside the workspace, alter the master CV, or reach the companion from a web page. `docs/ARCHITECTURE.md` describes the trust boundaries.
- **Agent prompt changes** (`cv_tailor/templates/`) need a run against a real model as well as the tests, since the stand-in cannot judge prompt quality. Say in the pull request which model and what you checked. `cv-tailor usage` shows whether the change moved token use.
- **Logs stay content-free.** Log events carry identifiers, states, timings and counts, never CV text, listings or answers. A test enforces this.

## Commits and pull requests

Write each commit message for someone reading the history later: a short imperative subject ("Fix status writes failing on Windows"), then a body giving the problem, its cause, the fix and how you verified it. Keep one logical change per commit.

In the pull request, describe what changed and how you tested it, including the OS and, for prompt changes, the model.

By contributing you agree that your contribution is licensed under the MIT licence of this project.
