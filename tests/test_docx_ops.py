import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.docx_ops import PlanError, validate_qa_result, validate_tailoring_result


class TailoringPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cv = {
            "paragraphs": [
                {
                    "id": "document:p0001",
                    "text": "Original profile text.",
                    "editable": True,
                },
                {
                    "id": "header1-1:p0001",
                    "text": "Private header",
                    "editable": False,
                },
            ]
        }

    def test_accepts_valid_ready_plan(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "replacements": [
                {
                    "paragraph_id": "document:p0001",
                    "original_text": "Original profile text.",
                    "new_text": "Tailored profile text.",
                    "reason": "Aligns truthful evidence with the role.",
                }
            ],
            "change_summary": ["Updated the profile."],
            "unsupported_requirements": [],
            "recommendations": [],
        }
        self.assertEqual(validate_tailoring_result(plan, self.cv), plan)

    def test_accepts_a_plan_with_no_changes_only_when_it_says_why(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "replacements": [],
            "change_summary": ["No changes: the CV already presents the relevant evidence."],
            "unsupported_requirements": [],
            "recommendations": [],
        }
        self.assertIs(validate_tailoring_result(plan, self.cv), plan)
        for missing in ([], None):
            with self.assertRaisesRegex(PlanError, "explain why"):
                validate_tailoring_result({**plan, "change_summary": missing}, self.cv)
        with self.assertRaisesRegex(PlanError, "replacements list"):
            validate_tailoring_result({**plan, "replacements": None}, self.cv)

    def test_accepts_clarification_batch(self) -> None:
        result = {
            "schema_version": 1,
            "status": "needs_clarification",
            "questions": [{"question": "Have you used Kubernetes?", "why": "Required skill"}],
        }
        self.assertEqual(validate_tailoring_result(result, self.cv), result)

    def test_rejects_non_editable_paragraph(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "replacements": [
                {
                    "paragraph_id": "header1-1:p0001",
                    "original_text": "Private header",
                    "new_text": "Changed header",
                    "reason": "Should fail.",
                }
            ],
            "change_summary": [],
            "unsupported_requirements": [],
            "recommendations": [],
        }
        with self.assertRaises(PlanError):
            validate_tailoring_result(plan, self.cv)

    def test_rejects_original_text_mismatch(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "replacements": [
                {
                    "paragraph_id": "document:p0001",
                    "original_text": "Different text.",
                    "new_text": "Tailored profile text.",
                    "reason": "Should fail.",
                }
            ],
            "change_summary": [],
            "unsupported_requirements": [],
            "recommendations": [],
        }
        with self.assertRaises(PlanError):
            validate_tailoring_result(plan, self.cv)


class QaPlanTests(unittest.TestCase):
    def test_requires_every_page_to_be_inspected(self) -> None:
        qa = {
            "schema_version": 1,
            "pages_inspected": 1,
            "application_ready": True,
            "issues": [],
            "notes": ["Page inspected."],
        }
        with self.assertRaises(PlanError):
            validate_qa_result(qa, {"page_count": 2})


if __name__ == "__main__":
    unittest.main()
