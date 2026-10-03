"""End to end run of the companion with a stand-in for OpenCode and no page renderer."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.cli import main
from cv_tailor.companion import TailoringCompanion
from cv_tailor.config import CONFIG_NAME, load_config
from cv_tailor.docx_io import extract_cv
from cv_tailor.render import Layout, Renderer

FAKE_AGENT = r'''
import json, os, re, sys
# Like OpenCode, trust $PWD over the real working directory.
pwd = os.environ.get("PWD")
if pwd and os.path.normcase(os.path.realpath(pwd)) != os.path.normcase(os.path.realpath(os.getcwd())):
    raise SystemExit(4)
args = sys.argv
agent = args[args.index("--agent") + 1]
text = open(re.search(r"file (\S+\.md)", args[2]).group(1).replace("\\", "/"), encoding="utf8").read()
with open("agents-called.txt", "a") as log:
    log.write(agent + "\n")
# A usage limit, the way OpenCode reports one: once for "[usage limit]", every time for the long one.
if agent == "cv-tailor" and "[usage limit" in text:
    long = "[usage limit long]" in text
    if long or not os.path.exists("limit-hit"):
        open("limit-hit", "w").close()
        print('{"type":"error","error":{"data":{"message":"The usage limit has been reached"}}}')
        print("primary-reset-after-seconds: " + ("99999" if long else "120"), file=sys.stderr)
        raise SystemExit(1)
if agent == "cv-tailor" and "[no changes]" in text:
    out = {"schema_version": 1, "status": "ready", "job": {"title": "T", "company": "C"}, "replacements": [],
           "change_summary": ["Already fits."], "unsupported_requirements": [], "recommendations": []}
elif agent == "cv-tailor":
    m = re.search(r"^(document:p\d{4}) \[Eb\] (.+)$", text, re.M)
    out = {"schema_version": 1, "status": "ready", "job": {"title": "T", "company": "C"},
           "replacements": [{"paragraph_id": m.group(1), "original_text": m.group(2),
                             "new_text": m.group(2) + " Tailored.", "reason": "test"}],
           "change_summary": ["x"], "unsupported_requirements": ["y"], "recommendations": ["z"]}
elif agent == "cv-tailor-letter":
    out = {"schema_version": 1, "status": "ready", "salutation": "Dear Team,",
           "paragraphs": ["First paragraph.", "Second paragraph."], "closing": "Kind regards,"}
elif agent == "cv-tailor-qa":
    pages = int(re.search(r"Pages rendered: (\d+)", text).group(1))
    out = {"schema_version": 1, "pages_inspected": pages, "application_ready": True, "issues": [],
           "notes": ["Inspected by the stand-in."]}
else:
    raise SystemExit(3)
# Two model calls, reported the way OpenCode does: 1,000 + 1,500 = 2,500 tokens for every run.
for tokens in ({"total": 1000, "input": 900, "output": 100, "reasoning": 0, "cache": {"write": 0, "read": 0}},
               {"total": 1500, "input": 200, "output": 150, "reasoning": 50, "cache": {"write": 0, "read": 1100}}):
    print(json.dumps({"type": "step_finish", "part": {"type": "step-finish", "tokens": tokens}}))
print(json.dumps({"type": "text", "part": {"text": json.dumps(out)}}))
'''

class StubRenderer(Renderer):
    """Writes a placeholder PDF and reports every paragraph on page 1, so layout checks pass."""

    name = "stub"

    def __init__(self, tailored_pages: int = 1) -> None:
        self.tailored_pages = tailored_pages  # > 1 makes the tailored copy fail the page-count check

    def render(self, docx: Path, pdf: Path | None) -> Layout:
        if pdf is not None:
            pdf.write_bytes(b"%PDF-1.4 stand-in")
        paragraphs = [
            {"index": index, "start_page": 1, "end_page": 1, "is_list": False, "length": len(item["text"])}
            for index, item in enumerate(extract_cv(docx, "", None)["paragraphs"], 1)
        ]
        pages = 1 if docx.name == "master_cv.docx" else self.tailored_pages
        return Layout(pages, paragraphs, pdf, self.name)


JOB = {
    "url": "https://example.com/jobs/1?utm=x",
    "title": "Graduate Software Engineer",
    "company": "Example Ltd",
    "description": ("About the role\nYou will build Python services and REST APIs.\nRequirements\n"
                    "Experience with testing, Docker and Git. " * 12),
}


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "ws"
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", str(self.root), "--model", "provider/model", "--sample"])
        script = self.root / "fake_agent.py"
        script.write_text(FAKE_AGENT, encoding="utf-8")
        if os.name == "nt":
            launcher = self.root / "fake_agent.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.root / "fake_agent.sh"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        config = json.loads((self.root / CONFIG_NAME).read_text(encoding="utf-8"))
        config.update(opencode_executable=str(launcher), render_backend="none")
        (self.root / CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
        self.app = TailoringCompanion(load_config(self.root), self.root)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def submit(self, marker: str = "") -> str:
        return self.app.create_job({**JOB, "description": f"{JOB['description']} {marker}"})["job_id"]

    def run_job(self, marker: str = "") -> dict:
        job_id = self.submit(marker)
        self.app.work_queue.join()
        return self.app.get_job(job_id)

    def wait_for_state(self, job_id: str, state: str) -> dict:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self.app.get_job(job_id)
            if status["state"] == state:
                return status
            time.sleep(0.05)
        self.fail(f"job never reached {state!r}: {status}")

    def agents_called(self) -> list[str]:
        log = self.root / "agents-called.txt"
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    def test_job_is_tailored_deduplicated_and_logged(self) -> None:
        # A PWD inherited from the launching shell must not leak into the agent process.
        stale_pwd = mock.patch.dict(os.environ, {"PWD": self.directory.name})
        stale_pwd.start()
        self.addCleanup(stale_pwd.stop)
        job = self.app.create_job(dict(JOB))
        self.app.work_queue.join()
        status = self.app.get_job(job["job_id"])
        self.assertEqual(status["state"], "completed", status.get("message"))
        self.assertIn("not checked", status["message"])
        output = self.root / status["output_path"]
        self.assertEqual(output.name, "Alex_Morgan_Example_Ltd_Graduate_Software_Engineer_CV.docx")
        texts = [p["text"] for p in extract_cv(output, "", None)["paragraphs"]]
        self.assertTrue(any(text.endswith("Tailored.") for text in texts))
        master_texts = [p["text"] for p in extract_cv(self.root / "data/master_cv.docx", "", None)["paragraphs"]]
        self.assertFalse(any(text.endswith("Tailored.") for text in master_texts))  # master untouched

        duplicate = self.app.create_job({**JOB, "url": "https://example.com/jobs/1?ref=y"})
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.app.knowledge.applications_summary()["totals"], {"tailored": 1})

        self.app.request_cover_letter(job["job_id"])
        self.app.work_queue.join()
        letter = self.app.get_job(job["job_id"])
        self.assertEqual(letter["letter_state"], "completed", letter.get("letter_message"))
        self.assertTrue((self.root / letter["letter_path"]).is_file())

    def test_a_plan_with_no_changes_completes_with_an_unchanged_copy(self) -> None:
        job = self.app.create_job({**JOB, "description": JOB["description"] + " [no changes]"})
        self.app.work_queue.join()
        status = self.app.get_job(job["job_id"])
        self.assertEqual(status["state"], "completed", status.get("message"))
        self.assertIn("No changes recommended", status["message"])
        output = extract_cv(self.root / status["output_path"], "", None)["paragraphs"]
        master = extract_cv(self.root / "data/master_cv.docx", "", None)["paragraphs"]
        self.assertEqual([p["text"] for p in output], [p["text"] for p in master])
        report = (self.root / status["report_path"]).read_text(encoding="utf-8")
        self.assertIn("- None. See the change summary for why.", report)
        # An empty plan must not replace the reusable plan for this role family.
        self.assertEqual(list((self.root / "data/runtime/templates").glob("*.json")), [])

    def test_a_job_leaves_a_complete_log_trace_without_its_content(self) -> None:
        with self.assertLogs("cv_tailor", level="INFO") as captured:
            status = self.run_job()
        records = [record for record in captured.records if getattr(record, "job", "-") == status["job_id"]]
        messages = [record.getMessage() for record in records]
        self.assertTrue(any(m.startswith("event=job_received") for m in messages), messages)
        self.assertIn("event=agent_done agent=cv-tailor exit_code=0", " | ".join(messages))
        self.assertTrue(messages[-1].startswith("event=status state=completed stage=complete"), messages[-1])
        everything = "\n".join(record.getMessage() for record in captured.records)
        self.assertNotIn("REST APIs", everything)  # the listing's text never reaches the log

    def test_every_agent_run_records_its_token_usage(self) -> None:
        self.app.renderer = StubRenderer()
        self.app.config["ai_qa_mode"] = "always"
        with self.assertLogs("cv_tailor", level="INFO") as captured:
            status = self.run_job()
            self.app.request_cover_letter(status["job_id"])
            self.app.work_queue.join()
        job_dir = self.root / "data/runtime/jobs" / status["job_id"]
        runs = json.loads((job_dir / "usage.json").read_text(encoding="utf-8"))["runs"]
        self.assertEqual([run["agent"] for run in runs], ["cv-tailor", "cv-tailor-qa", "cv-tailor-letter"])
        self.assertEqual(runs[0]["model"], "provider/model")
        self.assertEqual(runs[0]["tokens"], {"input": 1100, "cache_read": 1100, "cache_write": 0, "output": 250,
                                             "reasoning": 50, "steps": 2, "total": 2500})
        self.assertIn("event=agent_done agent=cv-tailor exit_code=0 seconds=",
                      " | ".join(record.getMessage() for record in captured.records))
        self.assertIn(" tokens=2500 ", " | ".join(record.getMessage() for record in captured.records))

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(main(["usage", "--workspace", str(self.root)]), 0)
        self.assertIn("median per job               7,500 tokens over 1 job(s)", printed.getvalue())

    # ai_qa_mode: the AI page inspection costs tokens, so by default it runs only when the free checks fail.

    def test_ai_qa_is_skipped_when_the_automatic_layout_checks_pass(self) -> None:
        self.app.renderer = StubRenderer()
        status = self.run_job()
        self.assertEqual(status["state"], "completed", status.get("message"))
        self.assertNotIn("cv-tailor-qa", self.agents_called())
        qa = json.loads((self.root / status["preview_path"]).with_name("qa.json").read_text(encoding="utf-8"))
        self.assertIn("AI page inspection was not needed", qa["notes"][0])

    def test_ai_qa_runs_when_the_automatic_layout_checks_fail(self) -> None:
        self.app.renderer = StubRenderer(tailored_pages=2)  # the tailored copy grew by a page
        status = self.run_job()
        self.assertIn("cv-tailor-qa", self.agents_called())
        self.assertEqual(status["state"], "completed_with_warning")
        self.assertIn("page count changed", status["message"])

    def test_ai_qa_mode_always_inspects_even_a_clean_layout(self) -> None:
        self.app.renderer = StubRenderer()
        self.app.config["ai_qa_mode"] = "always"
        status = self.run_job()
        self.assertEqual(status["state"], "completed", status.get("message"))
        self.assertIn("cv-tailor-qa", self.agents_called())

    # Usage limits: a short one pauses the queue and resumes; one longer than six hours fails the job.

    def test_usage_limit_pauses_the_queue_then_resumes_the_job(self) -> None:
        self.app.PAUSE_CHECK_SECONDS = 0.05
        job_id = self.submit("[usage limit]")
        status = self.wait_for_state(job_id, "paused")
        self.assertRegex(status["message"], r"^Usage limit reached\. Resuming automatically at \d\d:\d\d\.$")
        self.assertGreater(self.app.paused_until - time.time(), 150)  # the 120 s reset plus a 60 s margin
        self.app.paused_until = 0.0  # the limit has reset
        self.app.work_queue.join()
        status = self.app.get_job(job_id)
        self.assertEqual(status["state"], "completed", status.get("message"))
        self.assertEqual(self.agents_called().count("cv-tailor"), 2)

    def test_usage_limit_longer_than_six_hours_fails_instead_of_pausing(self) -> None:
        self.app.PAUSE_CHECK_SECONDS = 0.05
        # Poll rather than join the queue: if the job wrongly paused, joining would wait ~28 hours.
        status = self.wait_for_state(self.submit("[usage limit long]"), "failed")
        self.assertIn("Try again later", status["message"])
        self.assertEqual(self.app.paused_until, 0.0)


if __name__ == "__main__":
    unittest.main()
