"""Renderer selection, PDF layout parsing, and (where installed) a real LibreOffice render."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor import render
from cv_tailor.docx_io import extract_cv
from cv_tailor.render import LibreOfficeRenderer, RenderError, find_libreoffice, select_renderer
from cv_tailor.sample import write_sample_cv

# CI sets this on the job that installs LibreOffice, so a missing install fails instead of skipping.
REQUIRE_LIBREOFFICE = os.environ.get("CV_TAILOR_REQUIRE_LIBREOFFICE") == "1"


def _has_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


class SampleCvTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.docx = write_sample_cv(Path(self.directory.name) / "cv.docx")
        self.texts = [p["text"] for p in extract_cv(self.docx, "", None)["paragraphs"]]

    def tearDown(self) -> None:
        self.directory.cleanup()


class ParagraphPageTests(SampleCvTestCase):
    def test_paragraphs_are_located_on_the_pages_that_contain_them(self) -> None:
        middle = len(self.texts) // 2
        pages = [" ".join(self.texts[:middle]), " ".join(self.texts[middle:])]
        layout = render._paragraph_pages(self.docx, pages)
        self.assertEqual([p["start_page"] for p in layout], [1] * middle + [2] * (len(self.texts) - middle))
        self.assertTrue(all(p["start_page"] == p["end_page"] for p in layout))

    def test_paragraph_split_across_a_page_break_spans_both_pages(self) -> None:
        index = max(range(len(self.texts)), key=lambda i: len(self.texts[i]))
        longest = self.texts[index]
        half = len(longest) // 2
        pages = [
            " ".join(self.texts[:index] + [longest[:half]]),
            " ".join([longest[half:]] + self.texts[index + 1:]),
        ]
        item = render._paragraph_pages(self.docx, pages)[index]
        self.assertEqual((item["start_page"], item["end_page"]), (1, 2))

    def test_whitespace_and_case_differences_in_pdf_text_are_ignored(self) -> None:
        pages = ["\n".join(text.upper().replace(" ", "  ") for text in self.texts)]
        self.assertTrue(all(p["start_page"] == 1 for p in render._paragraph_pages(self.docx, pages)))


class PdfPageCountTests(unittest.TestCase):
    def count(self, content: bytes) -> int | None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.pdf"
            path.write_bytes(content)
            return render._count_pdf_pages(path)

    def test_counts_page_objects_but_not_the_page_tree(self) -> None:
        pdf = b"%PDF-1.4 1 0 obj << /Type /Pages /Kids [2 0 R 3 0 R] >> 2 0 obj << /Type /Page >> 3 0 obj << /Type/Page >>"
        self.assertEqual(self.count(pdf), 2)

    def test_returns_none_when_page_objects_are_not_visible(self) -> None:
        self.assertIsNone(self.count(b"%PDF-1.5 compressed object streams only"))


class RendererSelectionTests(unittest.TestCase):
    def select(self, backend: str, *, word: bool, office: str | None):
        with mock.patch.object(render, "word_available", return_value=word), \
                mock.patch.object(render, "find_libreoffice", return_value=office):
            return select_renderer(backend)

    def test_auto_prefers_word_then_libreoffice_then_none(self) -> None:
        self.assertEqual(self.select("auto", word=True, office="soffice").name, "word")
        chosen = self.select("auto", word=False, office="/opt/soffice")
        self.assertIsInstance(chosen, LibreOfficeRenderer)
        self.assertEqual(chosen.executable, "/opt/soffice")
        self.assertEqual(self.select("auto", word=False, office=None).name, "none")

    def test_an_explicit_backend_that_is_missing_is_an_error(self) -> None:
        with self.assertRaisesRegex(RenderError, "Word"):
            self.select("word", word=False, office="soffice")
        with self.assertRaisesRegex(RenderError, "soffice"):
            self.select("libreoffice", word=False, office=None)


class LibreOfficeRenderTests(SampleCvTestCase):
    def setUp(self) -> None:
        self.office = find_libreoffice()
        if not self.office:
            if REQUIRE_LIBREOFFICE:
                self.fail("CV_TAILOR_REQUIRE_LIBREOFFICE=1 but soffice was not found")
            self.skipTest("LibreOffice is not installed")
        super().setUp()

    def test_renders_the_sample_cv_to_a_one_page_pdf(self) -> None:
        pdf = Path(self.directory.name) / "preview.pdf"
        layout = LibreOfficeRenderer(self.office).render(self.docx, pdf)
        self.assertEqual(layout.backend, "libreoffice")
        self.assertEqual(layout.page_count, 1)
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        if _has_pypdf():
            self.assertEqual(len(layout.paragraphs), len(self.texts))
            self.assertTrue(all(p["start_page"] == p["end_page"] == 1 for p in layout.paragraphs))
        else:
            self.assertIsNone(layout.paragraphs)


if __name__ == "__main__":
    unittest.main()
