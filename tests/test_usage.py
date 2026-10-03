import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.usage import record_run, report, summarise, tokens_from_output


def step(input_: int, output: int, reasoning: int = 0, read: int = 0, write: int = 0) -> str:
    tokens = {"total": input_ + output + reasoning + read + write, "input": input_, "output": output,
              "reasoning": reasoning, "cache": {"write": write, "read": read}}
    return json.dumps({"type": "step_finish", "part": {"type": "step-finish", "tokens": tokens}})


class TokensFromOutputTests(unittest.TestCase):
    def test_sums_every_model_call_and_ignores_other_lines(self) -> None:
        output = "\n".join([
            "not json",
            json.dumps({"type": "step_start", "part": {}}),
            step(4036, 57),
            json.dumps({"type": "text", "part": {"text": "{\"tokens\": 99}"}}),
            step(1540, 188, reasoning=95, read=3584),
        ])
        self.assertEqual(tokens_from_output(output), {
            "input": 5576, "cache_read": 3584, "cache_write": 0, "output": 245, "reasoning": 95,
            "steps": 2, "total": 9500,
        })

    def test_returns_none_when_no_model_call_was_reported(self) -> None:
        error = json.dumps({"type": "error", "error": {"data": {"message": "The usage limit has been reached"}}})
        self.assertIsNone(tokens_from_output(error))
        self.assertIsNone(tokens_from_output(""))

    def test_skips_counts_that_are_missing_or_not_integers(self) -> None:
        odd = json.dumps({"type": "step_finish", "part": {"tokens": {"input": True, "output": "7", "reasoning": 3}}})
        self.assertEqual(tokens_from_output(odd), {
            "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 3, "steps": 1, "total": 3,
        })


class ReportTests(unittest.TestCase):
    def test_runs_are_kept_per_job_and_summarised_by_median(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory)
            for job_id, totals in (("job-a", (1000, 400)), ("job-b", (3000,)), ("job-c", (2000,))):
                for index, total in enumerate(totals):
                    tokens = tokens_from_output(step(total, 0))
                    assert tokens is not None
                    record_run(jobs / job_id, "cv-tailor-qa" if index else "cv-tailor", "p/m", "medium", 1.5, tokens)
            (jobs / "job-without-usage").mkdir()
            summary = summarise(jobs)
        self.assertEqual([job["job_id"] for job in summary], ["job-a", "job-b", "job-c"])
        self.assertEqual(summary[0]["total"], 1400)
        self.assertEqual(summary[0]["seconds"], 3.0)
        text = report(summary)
        self.assertIn("median cv-tailor             2,000 tokens over 3 run(s)", text)
        self.assertIn("median cv-tailor-qa            400 tokens over 1 run(s)", text)
        self.assertIn("median per job               2,000 tokens over 3 job(s)", text)

    def test_an_empty_workspace_says_so(self) -> None:
        self.assertIn("No token usage recorded yet", report([]))


if __name__ == "__main__":
    unittest.main()
