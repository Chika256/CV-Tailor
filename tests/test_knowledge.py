import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.knowledge import KnowledgeBase


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.kb = KnowledgeBase(Path(self.directory.name) / "kb.db")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_answers_keep_questions_and_are_deduplicated(self) -> None:
        first = self.kb.record_answers("j1", "Acme", "Dev", ["Did you ship it?"], "No, it was a prototype.")
        again = self.kb.record_answers("j1", "Acme", "Dev", ["Did you ship it?"], "No, it was a prototype.")
        self.assertIsNotNone(first)
        self.assertIsNone(again)
        item = self.kb.snapshot()["explicit_user_answers"][0]
        self.assertIn("1. Did you ship it?", item["question"])

    def test_correction_retires_old_statement(self) -> None:
        old = self.kb.add("note", "user", "Built the Gemma feature")
        self.kb.correct(old, "Gemma feature was not fully implemented")
        texts = [row["text"] for row in self.kb.search("gemma")]
        self.assertEqual(texts, ["Gemma feature was not fully implemented"])
        self.assertEqual(len(self.kb.search("gemma", include_retired=True)), 2)

    def test_cv_duplicates_merge_sources(self) -> None:
        sentence = "Built a deployment configurator for VR training."
        self.kb.add("cv", "a.docx", sentence)
        self.kb.add("cv", "b.docx", sentence)
        rows = self.kb.snapshot()["other_cv_evidence"]
        self.assertEqual(len(rows), 1)
        self.assertIn("b.docx", rows[0]["source"])

    def test_chat_export_imports_only_user_messages(self) -> None:
        export = '[{"role":"user","content":"I led the VR web project end to end."},{"role":"assistant","content":"Great, noted for later use."}]'
        self.assertEqual(self.kb.import_chat_export("x", export), 1)


if __name__ == "__main__":
    unittest.main()
