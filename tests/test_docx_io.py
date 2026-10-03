import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.docx_io import apply_plan, extract_cv
from cv_tailor.docx_ops import PlanError, validate_tailoring_result
from cv_tailor.sample import write_sample_cv


class DocxIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.master = write_sample_cv(Path(self.directory.name) / "master.docx")
        self.cv = extract_cv(self.master, "hash", 1)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def paragraph(self, startswith: str) -> dict:
        return next(item for item in self.cv["paragraphs"] if item["text"].startswith(startswith))

    def test_extract_marks_bullets_and_locks_tab_lines(self) -> None:
        bullet = self.paragraph("Built a REST API")
        self.assertEqual(bullet["list_type"], 2)
        self.assertTrue(bullet["editable"])
        self.assertTrue(self.paragraph("Computer Science graduate with").get("editable"))
        tab_line = self.paragraph("Inventory Tracker")
        self.assertFalse(tab_line["editable"])
        self.assertIn("\t", tab_line["text"] + "\t")  # tab content is not editable text

    def test_apply_replaces_text_and_leaves_everything_else_untouched(self) -> None:
        target = self.paragraph("Built a REST API")
        replacement = {
            "paragraph_id": target["id"],
            "original_text": target["text"],
            "new_text": "Built a REST API & test suite for <stock> management.",
            "reason": "test",
        }
        output = Path(self.directory.name) / "out.docx"
        self.assertEqual(apply_plan(self.master, output, [replacement]), 1)
        edited = extract_cv(output, "", None)["paragraphs"]
        original = self.cv["paragraphs"]
        self.assertEqual(len(edited), len(original))
        for before, after in zip(original, edited, strict=True):
            if before["id"] == target["id"]:
                self.assertEqual(after["text"], replacement["new_text"])
                self.assertEqual(after["list_type"], 2)  # still a bullet
            else:
                self.assertEqual(before["text"], after["text"])
        with zipfile.ZipFile(self.master) as a, zipfile.ZipFile(output) as b:
            for name in a.namelist():
                if name != "word/document.xml":
                    self.assertEqual(a.read(name), b.read(name))
            self.assertTrue(b.read("word/document.xml").decode().startswith("<?xml"))

    def test_apply_refuses_changed_or_locked_paragraphs(self) -> None:
        target = self.paragraph("Built a REST API")
        stale = {"paragraph_id": target["id"], "original_text": "something else", "new_text": "x", "reason": "r"}
        with self.assertRaises(PlanError):
            apply_plan(self.master, Path(self.directory.name) / "o.docx", [stale])
        locked = self.paragraph("Inventory Tracker")
        with self.assertRaises(PlanError):
            apply_plan(self.master, Path(self.directory.name) / "o.docx", [
                {"paragraph_id": locked["id"], "original_text": locked["text"], "new_text": "x", "reason": "r"}
            ])

    def test_plan_validation_accepts_extracted_paragraphs(self) -> None:
        target = self.paragraph("Built a REST API")
        plan = {
            "schema_version": 1, "status": "ready",
            "replacements": [{"paragraph_id": target["id"], "original_text": target["text"],
                              "new_text": target["text"] + " Added.", "reason": "relevance"}],
            "change_summary": ["x"], "unsupported_requirements": [], "recommendations": [],
        }
        self.assertEqual(validate_tailoring_result(plan, self.cv)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
