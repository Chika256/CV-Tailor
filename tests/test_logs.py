import contextlib
import io
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor import logs


class LogFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.file = Path(self.directory.name) / "companion.log"
        with contextlib.redirect_stderr(io.StringIO()):
            logs.configure(self.file)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(logs.reset)  # runs first: closes the file before the directory is removed

    def lines(self) -> list[str]:
        for handler in logs.logger.handlers:
            handler.flush()
        return self.file.read_text(encoding="utf-8").splitlines()

    def test_one_line_per_event_with_quoted_fields(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            logs.event("status", job="job-1", state="running", message='Reading "the" CV', empty="", missing=None)
        (line,) = self.lines()
        self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d level=INFO job=job-1 event=status ")
        self.assertTrue(line.endswith(r'state=running message="Reading \"the\" CV" empty="" missing=-'), line)

    def test_events_are_tagged_with_the_job_being_processed(self) -> None:
        token = logs.current_job.set("job-2")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                logs.event("agent_done", logging.WARNING, agent="cv-tailor", exit_code=1)
        finally:
            logs.current_job.reset(token)
        with contextlib.redirect_stderr(io.StringIO()):
            logs.event("listening", port=8765)
        tagged, untagged = self.lines()
        self.assertIn("level=WARNING job=job-2 event=agent_done agent=cv-tailor exit_code=1", tagged)
        self.assertIn("level=INFO job=- event=listening port=8765", untagged)


class SilentByDefaultTests(unittest.TestCase):
    def test_nothing_is_printed_until_logging_is_configured(self) -> None:
        logs.reset()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            logs.event("job_error", logging.ERROR, error="RuntimeError")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
