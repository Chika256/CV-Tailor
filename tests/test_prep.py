import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.prep import (
    clean_description,
    deterministic_layout_issues,
    fit_score,
    job_fingerprint,
    role_family,
    validate_letter,
    write_cover_letter_docx,
)


def para(index, start, end=None, is_list=False, length=60):
    return {"index": index, "start_page": start, "end_page": end or start, "is_list": is_list, "length": length}


class PrepTests(unittest.TestCase):
    def test_clean_description_drops_boilerplate_only(self) -> None:
        text = "Requirements\nPython experience\nWe are an equal opportunities employer\nApply now\nBuild APIs"
        cleaned, removed = clean_description(text)
        self.assertEqual(cleaned, "Requirements\nPython experience\nBuild APIs")
        self.assertGreater(removed, 0)

    def test_fingerprint_ignores_tracking_query_and_whitespace(self) -> None:
        a = job_fingerprint("https://x.com/jobs/1?utm=a", "Build   things")
        b = job_fingerprint("https://X.com/jobs/1/?ref=b", "build things")
        self.assertEqual(a, b)

    def test_role_family(self) -> None:
        self.assertEqual(role_family("Graduate QA Automation Engineer"), "qa")
        self.assertEqual(role_family("Junior AI Engineer"), "ai")
        self.assertEqual(role_family("Barista"), "general")

    def test_fit_score_reports_missing_terms(self) -> None:
        fit = fit_score("kubernetes kubernetes python python terraform", "I know python")
        self.assertEqual(fit["score"], 33)
        self.assertEqual(set(fit["missing"]), {"kubernetes", "terraform"})

    def test_fit_score_ignores_filler_and_the_heading_line(self) -> None:
        listing = (
            "Graduate Software Engineer - Example Ltd (fictional listing for trying the tool)\n"
            "You will write tests and ship changes through a small team that owns a logistics dashboard."
        )
        fit = fit_score(listing, "Wrote tests.", title="Graduate Software Engineer")
        self.assertEqual(fit["missing"], ["logistics", "dashboard"])

    def test_fit_score_matches_inflections_and_reports_the_listing_wording(self) -> None:
        fit = fit_score("testing testing reviews deployments kafka", "Wrote unit tests and did code review.")
        self.assertNotIn("testing", fit["missing"])
        self.assertNotIn("reviews", fit["missing"])
        self.assertEqual(fit["missing"], ["deployments", "kafka"])

    def test_layout_clean_passes(self) -> None:
        layout = {"page_count": 2, "paragraphs": [para(1, 1), para(2, 1, is_list=True), para(3, 2)]}
        self.assertEqual(deterministic_layout_issues(layout, 2), [])

    def test_layout_flags_page_count_split_and_orphan(self) -> None:
        layout = {
            "page_count": 3,
            "paragraphs": [para(1, 1, is_list=True), para(2, 1, length=50), para(3, 2, is_list=True)],
        }
        issues = deterministic_layout_issues(layout, 2)
        self.assertTrue(any("Page count" in issue for issue in issues))
        self.assertTrue(any("stranded" in issue for issue in issues))
        self.assertTrue(any("split across" in issue for issue in deterministic_layout_issues(
            {"page_count": 1, "paragraphs": [para(1, 1, 2)]}, 1)))

    def test_long_entry_continuing_over_a_page_is_acceptable(self) -> None:
        bullets = [para(i, 1, is_list=True) for i in range(2, 5)] + [para(i, 2, is_list=True) for i in range(5, 8)]
        layout = {"page_count": 2, "paragraphs": [para(1, 1, length=40)] + bullets}
        self.assertEqual(deterministic_layout_issues(layout, 2), [])

    def test_letter_validation_and_docx(self) -> None:
        letter = {"schema_version": 1, "status": "ready", "salutation": "Dear Team,",
                  "paragraphs": ["One & two.", "Three."], "closing": "Kind regards,"}
        validate_letter(letter)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "letter.docx"
            write_cover_letter_docx(target, ["Name", "Contact"], "2 October 2026", letter)
            with zipfile.ZipFile(target) as archive:
                self.assertIn("One &amp; two.", archive.read("word/document.xml").decode())
        with self.assertRaises(ValueError):
            validate_letter({**letter, "paragraphs": ["only one"]})


if __name__ == "__main__":
    unittest.main()
