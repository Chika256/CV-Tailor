import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.applicant import ProfileStore
from cv_tailor.sample import write_sample_cv


class ProfileTests(unittest.TestCase):
    def test_seed_and_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            master = write_sample_cv(Path(directory) / "master.docx")
            store = ProfileStore(Path(directory) / "profile.json", master)
            profile = store.load()
            self.assertEqual(profile["first_name"], "Alex")
            self.assertEqual(profile["last_name"], "Morgan")
            self.assertEqual(profile["email"], "alex.morgan@example.com")
            self.assertEqual(profile["postcode"], "AB1 2CD")
            self.assertEqual(profile["right_to_work"], "yes")
            self.assertEqual(profile["salary_expectation"], "")
            self.assertEqual(store.save({"notice_period": "1 month"})["notice_period"], "1 month")
            with self.assertRaises(ValueError):
                store.save({"date_of_birth": "x"})


if __name__ == "__main__":
    unittest.main()
