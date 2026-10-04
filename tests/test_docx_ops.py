import contextlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.docx_ops import (
    PlanError,
    read_json,
    removable_paragraphs,
    validate_qa_result,
    validate_tailoring_result,
    write_json,
)


def paragraph(number: int, text: str, level: int = 0) -> dict:
    """A cv.json paragraph; level is the list level counted from 1, 0 for a paragraph that is not a list."""
    return {"id": f"document:p{number:04d}", "text": text, "editable": bool(text),
            "list_type": 2 if level else 0, "list_level": level}


# Project titles written as list items with their bullets nested under them, and capitalised headings.
NESTED_CV = {"paragraphs": [
    paragraph(1, "Alex Morgan"),
    paragraph(2, "TECHNICAL SKILLS"),
    paragraph(3, "Languages: Java, Python", 1),
    paragraph(4, "Frontend: React", 1),
    paragraph(5, "PROJECTS"),
    paragraph(6, "Inventory Tracker – Python, Flask", 1),
    paragraph(7, "Built a REST API for stock management.", 2),
    paragraph(8, "Added role-based access and input validation.", 2),
    paragraph(9, ""),
    paragraph(10, "Study Planner – React", 1),
    paragraph(11, "Developed a responsive planner.", 2),
    paragraph(12, "EXPERIENCE"),
    paragraph(13, "Hackathon participant – Example Hack | Oct 2025"),
    paragraph(14, "Built a Flask REST API for a finance app.", 1),
    paragraph(15, "Wrote automated tests for the API.", 1),
]}


def removal_plan(*numbers: int) -> dict:
    texts = {item["id"]: item["text"] for item in NESTED_CV["paragraphs"]}
    return {
        "schema_version": 1, "status": "ready",
        "replacements": [
            {"paragraph_id": f"document:p{n:04d}", "original_text": texts[f"document:p{n:04d}"], "remove": True,
             "reason": "Least relevant claim for this job; removed to keep the CV within two pages."}
            for n in numbers
        ],
        "change_summary": ["Removed a bullet to fit two pages."], "unsupported_requirements": [], "recommendations": [],
    }


class RemovalTests(unittest.TestCase):
    def test_only_project_and_experience_bullets_with_a_sibling_are_removable(self) -> None:
        self.assertEqual(
            removable_paragraphs(NESTED_CV),
            {"document:p0007", "document:p0008", "document:p0014", "document:p0015"},
        )

    def test_accepts_removing_a_bullet(self) -> None:
        plan = removal_plan(8)
        self.assertEqual(validate_tailoring_result(plan, NESTED_CV), plan)

    def test_rejects_removing_a_title_a_skills_line_a_role_or_a_lone_bullet(self) -> None:
        for number in (6, 3, 13, 11):
            with self.subTest(paragraph=number), self.assertRaises(PlanError):
                validate_tailoring_result(removal_plan(number), NESTED_CV)

    def test_rejects_removing_every_bullet_of_an_entry(self) -> None:
        with self.assertRaises(PlanError):
            validate_tailoring_result(removal_plan(7, 8), NESTED_CV)

    def test_a_cv_extracted_without_list_levels_allows_no_removal(self) -> None:
        old = {"paragraphs": [{k: v for k, v in p.items() if k != "list_level"} for p in NESTED_CV["paragraphs"]]}
        self.assertEqual(removable_paragraphs(old), set())


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


class JsonFileTests(unittest.TestCase):
    def test_write_json_survives_a_concurrent_reader(self) -> None:
        # The HTTP API reads a job's status.json while the worker rewrites it; on Windows, replacing a
        # file that another thread has open fails unless the writer retries.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            write_json(path, {"n": 0})
            stop = threading.Event()

            def read_continuously() -> None:
                while not stop.is_set():
                    with contextlib.suppress(OSError):
                        read_json(path)

            reader = threading.Thread(target=read_continuously)
            reader.start()
            try:
                for n in range(300):
                    write_json(path, {"n": n})
            finally:
                stop.set()
                reader.join()
            self.assertEqual(read_json(path), {"n": 299})
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["status.json"])  # no temp files left

    def test_read_json_survives_a_concurrent_writer(self) -> None:
        # The other side of the same race: on Windows, opening a file at the moment it is being
        # replaced fails with "Access is denied", which reached the extension as an HTTP 500.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            write_json(path, {"n": 0})
            stop = threading.Event()

            def write_continuously() -> None:
                n = 0
                while not stop.is_set():
                    n += 1
                    write_json(path, {"n": n})

            writer = threading.Thread(target=write_continuously)
            writer.start()
            errors: list[OSError] = []
            try:
                for _ in range(2000):
                    try:
                        self.assertIn("n", read_json(path))
                    except OSError as error:
                        errors.append(error)
            finally:
                stop.set()
                writer.join()
            self.assertEqual(errors, [])


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
