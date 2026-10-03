"""Submit a listing to a test companion N times, one job at a time, plus a cover letter for each.

This is how the README's token measurements were made. Pair as a test extension, so no browser is needed.

Usage: python scripts/demo/drive_jobs.py --runs 5 --listing examples/sample_job_partial_fit.txt \\
           --company "Northwind Analytics" [--port 8799]
Then run `cv-tailor usage --workspace <test workspace>`.
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

NEUTRAL_ANSWER = "No further information is available. Use only what the CV already says."
FINAL = {"completed", "completed_with_warning", "failed", "needs_clarification", "needs_confirmation"}


class Companion:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.token = self.call("POST", "/pair", {}, origin="chrome-extension://smoketest")["token"]

    def call(self, method: str, path: str, body: Any = None, origin: str | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, method=method, data=data)
        request.add_header("Content-Type", "application/json")
        if getattr(self, "token", None):
            request.add_header("Authorization", f"Bearer {self.token}")
        if origin:
            request.add_header("Origin", origin)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")

    def wait(self, job_id: str, field: str, done: set[str]) -> dict[str, Any]:
        while True:
            status = self.call("GET", f"/jobs/{job_id}")
            if status.get(field) in done:
                return status
            time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--listing", type=Path, default=Path("examples/sample_job.txt"))
    parser.add_argument("--company", default="Example Ltd")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    if args.port == 8765:
        parser.error("use a test workspace on another port, not the default 8765")
    listing = args.listing.read_text(encoding="utf-8")
    companion = Companion(args.port)
    for run in range(1, args.runs + 1):
        started = time.monotonic()
        job = companion.call("POST", "/jobs", {
            "url": "https://example.com/jobs/" + args.company.split()[0].lower(),
            "title": listing.splitlines()[0], "company": args.company, "description": listing, "force": True,
        })
        job_id = job["job_id"]
        status = companion.wait(job_id, "state", FINAL)
        answered = 0
        # The model can ask a follow-up round, so keep answering (up to three rounds).
        while status["state"] in {"needs_clarification", "needs_confirmation"} and answered < 3:
            if status["state"] == "needs_confirmation":
                companion.call("POST", f"/jobs/{job_id}/proceed", {})
            else:
                companion.call("POST", f"/jobs/{job_id}/answers", {"answers": NEUTRAL_ANSWER})
            answered += 1
            time.sleep(2)
            status = companion.wait(job_id, "state", FINAL)
        print(f"run {run} job {job_id} state={status['state']} answered={answered} "
              f"secs={time.monotonic() - started:.0f} msg={status.get('message', '')[:120]!r}", flush=True)
        if status["state"].startswith("completed"):
            companion.call("POST", f"/jobs/{job_id}/cover-letter", {})
            letter = companion.wait(job_id, "letter_state", {"completed", "failed"})
            print(f"  letter {letter['letter_state']} {letter.get('letter_message', '')[:100]!r}", flush=True)


if __name__ == "__main__":
    main()
