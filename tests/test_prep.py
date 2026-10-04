import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.prep import (
    MAX_INPUT_LINE,
    build_input,
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


class InputPackageTests(unittest.TestCase):
    def test_every_line_fits_what_the_agent_can_read(self) -> None:
        # OpenCode's read tool cuts each line after 2,000 characters, so the agent never saw the rest of
        # the knowledge base (written as one JSON line) or of a listing captured as one long line.
        note = "Built the configurator with Mesh Training Ltd in weekly meetings. " * 40
        knowledge = {
            "schema_version": 1,
            "explicit_user_answers": [{"kind": "answer", "source": "job j1", "question": "1. Docker?", "text": "Yes."}],
            "user_notes_and_corrections": [{"kind": "note", "source": "user", "text": note}],
            "other_cv_evidence": [{"kind": "cv", "source": f"cv{n}.docx", "text": f"Paragraph {n} " * 30} for n in range(20)],
        }
        job = {"title": "Engineer", "company": "Acme", "url": "https://example.com/j",
               "description": "Requirements: " + "Python and SQL. " * 300}
        cv = {"paragraphs": [{"id": "document:p0001", "text": "Profile.", "editable": True}]}
        template = {"family": "data", "company": "Old", "title": "Role", "replacements": [
            {"paragraph_id": f"document:p{n:04d}", "new_text": "Rewritten bullet text. " * 8} for n in range(1, 30)
        ]}
        text = build_input(job, cv, None, knowledge, template)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), MAX_INPUT_LINE)
        self.assertLess(MAX_INPUT_LINE, 2000)
        flattened = " ".join(text.split())
        self.assertIn(" ".join(note.split()), flattened)
        self.assertEqual(flattened.count("Python and SQL."), 300)
        self.assertIn("### other_cv_evidence", text)


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

    def test_fit_score_does_not_report_everyday_words_as_missing(self) -> None:
        listing = (
            "Northwind helps retailers understand their sales, and analysts rely on the warehouse every morning.\n"
            "PostgreSQL or another relational database. Add tests so bad records are caught before they reach a"
            " dashboard."
        )
        fit = fit_score(listing, "PostgreSQL", ignore="Northwind")
        self.assertEqual(
            fit["missing"],
            ["retailers", "sales", "analysts", "warehouse", "relational", "database", "tests", "records", "dashboard"],
        )

    def test_fit_score_matches_inflections_and_reports_the_listing_wording(self) -> None:
        fit = fit_score("testing testing reviews deployments kafka", "Wrote unit tests and did code review.")
        self.assertNotIn("testing", fit["missing"])
        self.assertNotIn("reviews", fit["missing"])
        self.assertEqual(fit["missing"], ["deployments", "kafka"])

    def test_layout_clean_passes(self) -> None:
        layout = {"page_count": 2, "paragraphs": [para(1, 1), para(2, 1, is_list=True), para(3, 2)]}
        self.assertEqual(deterministic_layout_issues(layout, 2), [])

    def test_layout_flags_a_cv_over_the_page_limit(self) -> None:
        layout = {"page_count": 3, "paragraphs": [para(1, 1), para(2, 2), para(3, 3)]}
        issues = deterministic_layout_issues(layout, 2, max_pages=2)
        self.assertTrue(any("over the page limit of 2" in issue for issue in issues), issues)
        # A longer master brought down to the limit is the aim, not a page-count problem.
        fitted = {"page_count": 2, "paragraphs": [para(1, 1), para(2, 2)]}
        self.assertEqual(deterministic_layout_issues(fitted, 3, max_pages=2), [])

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
