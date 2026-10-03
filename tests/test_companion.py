import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.server import TailoringCompanion, _filename_component, _parse_opencode_json


class PayloadValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = TailoringCompanion.__new__(TailoringCompanion)
        self.app.config = {"minimum_description_characters": 100}

    def test_accepts_complete_job_payload(self) -> None:
        description = (
            "About the role\nResponsibilities include building services.\n"
            "Requirements include software development experience and testing skills."
        )
        result = self.app._validate_job_payload(
            {
                "url": "https://example.com/jobs/123",
                "title": "Software Engineer",
                "company": "Example Ltd",
                "description": description,
            }
        )
        self.assertEqual(result["title"], "Software Engineer")

    def test_accepts_long_description_with_one_signal(self) -> None:
        description = "Build products for creators. Some experience helps. " * 40
        result = self.app._validate_job_payload(
            {"url": "https://example.com/jobs/1", "title": "Engineer", "company": "X", "description": description}
        )
        self.assertEqual(result["company"], "X")

    def test_rejects_incomplete_description(self) -> None:
        with self.assertRaises(ValueError):
            self.app._validate_job_payload(
                {
                    "url": "https://example.com/jobs/123",
                    "title": "Software Engineer",
                    "company": "Example Ltd",
                    "description": "Short summary only.",
                }
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
