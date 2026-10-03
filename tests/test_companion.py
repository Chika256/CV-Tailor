import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.agents import _parse_opencode_json
from cv_tailor.companion import _filename_component
from cv_tailor.intake import validate_job_payload

MINIMUM = 100  # description length; the default is 500


class PayloadValidationTests(unittest.TestCase):
    def test_accepts_complete_job_payload(self) -> None:
        description = (
            "About the role\nResponsibilities include building services.\n"
            "Requirements include software development experience and testing skills."
        )
        result = validate_job_payload(
            {
                "url": "https://example.com/jobs/123",
                "title": "Software Engineer",
                "company": "Example Ltd",
                "description": description,
            },
            MINIMUM,
        )
        self.assertEqual(result["title"], "Software Engineer")

    def test_accepts_long_description_with_one_signal(self) -> None:
        description = "Build products for creators. Some experience helps. " * 40
        result = validate_job_payload(
            {"url": "https://example.com/jobs/1", "title": "Engineer", "company": "X", "description": description},
            MINIMUM,
        )
        self.assertEqual(result["company"], "X")

    def test_rejects_incomplete_description(self) -> None:
        with self.assertRaises(ValueError):
            validate_job_payload(
                {
                    "url": "https://example.com/jobs/123",
                    "title": "Software Engineer",
                    "company": "Example Ltd",
                    "description": "Short summary only.",
                },
                MINIMUM,
            )

    def test_sanitizes_output_filename_components(self) -> None:
        self.assertEqual(_filename_component("Example & Co / UK"), "Example_Co_UK")

    def test_extracts_json_from_opencode_event_stream(self) -> None:
        output = (
            '{"type":"step_start","part":{}}\n'
            '{"type":"text","part":{"text":"{\\"schema_version\\":1,\\"status\\":\\"ready\\"}"}}\n'
        )
        self.assertEqual(_parse_opencode_json(output)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
