"""End to end run of the companion with a stand-in for OpenCode and no page renderer."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.cli import main
from cv_tailor.config import CONFIG_NAME, load_config
from cv_tailor.docx_io import extract_cv
from cv_tailor.server import TailoringCompanion

FAKE_AGENT = r'''
import json, os, re, sys
# Like OpenCode, trust $PWD over the real working directory.
pwd = os.environ.get("PWD")
if pwd and os.path.normcase(os.path.realpath(pwd)) != os.path.normcase(os.path.realpath(os.getcwd())):
    raise SystemExit(4)
args = sys.argv
agent = args[args.index("--agent") + 1]
text = open(re.search(r"file (\S+\.md)", args[2]).group(1).replace("\\", "/"), encoding="utf8").read()
if agent == "cv-tailor":
    m = re.search(r"^(document:p\d{4}) \[Eb\] (.+)$", text, re.M)
    out = {"schema_version": 1, "status": "ready", "job": {"title": "T", "company": "C"},
           "replacements": [{"paragraph_id": m.group(1), "original_text": m.group(2),
                             "new_text": m.group(2) + " Tailored.", "reason": "test"}],
           "change_summary": ["x"], "unsupported_requirements": ["y"], "recommendations": ["z"]}
elif agent == "cv-tailor-letter":
    out = {"schema_version": 1, "status": "ready", "salutation": "Dear Team,",
           "paragraphs": ["First paragraph.", "Second paragraph."], "closing": "Kind regards,"}
else:
    raise SystemExit(3)
print(json.dumps({"type": "text", "part": {"text": json.dumps(out)}}))
'''

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


if __name__ == "__main__":
    unittest.main()
