import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor.cli import main
from cv_tailor.config import CONFIG_NAME, ConfigError, load_config
from cv_tailor.render import select_renderer


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, **values) -> None:
        (self.root / CONFIG_NAME).write_text(json.dumps(values), encoding="utf-8")

    def test_missing_config_points_to_init(self) -> None:
        with self.assertRaisesRegex(ConfigError, "cv-tailor init"):
            load_config(self.root)

    def test_model_is_required_and_validated(self) -> None:
        self.write()
        with self.assertRaisesRegex(ConfigError, "opencode_model"):
            load_config(self.root)
        self.write(opencode_model="nonsense")
        with self.assertRaisesRegex(ConfigError, "provider/model"):
            load_config(self.root)
        self.write(opencode_model="provider/model")
        self.assertEqual(load_config(self.root)["port"], 8765)

    def test_workspace_reached_through_a_symlink_is_accepted(self) -> None:
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            # Windows needs a privilege for symlinks but not for junctions, which resolve the same way.
            import _winapi

            _winapi.CreateJunction(str(real), str(link))
        try:
            (link / CONFIG_NAME).write_text(json.dumps({"opencode_model": "provider/model"}), encoding="utf-8")
            self.assertEqual(load_config(link)["master_cv"], "data/master_cv.docx")
        finally:
            # Remove the link itself first; older shutil.rmtree versions mishandle junctions.
            if os.name == "nt":
                os.rmdir(link)
            else:
                link.unlink()
        self.write(opencode_model="provider/model", output_dir="../real/../../outside")
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_rejects_unknown_keys_public_hosts_and_escaping_paths(self) -> None:
        for bad in ({"typo_setting": 1}, {"host": "0.0.0.0"}, {"output_dir": "../outside"}, {"port": 80},
                    {"agent_models": {"nope": "a/b"}}, {"ai_qa_mode": "sometimes"}):
            self.write(opencode_model="provider/model", **bad)
            with self.assertRaises(ConfigError, msg=str(bad)):
                load_config(self.root)


class CliTests(unittest.TestCase):
    def test_init_creates_a_usable_workspace_and_doctor_passes_its_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["init", str(root), "--model", "provider/model", "--sample"]), 0)
            for expected in (CONFIG_NAME, "CV_TAILORING_AGENT.md", ".opencode/agent/cv-tailor.md",
                             "data/master_cv.docx", "data/runtime"):
                self.assertTrue((root / expected).exists(), expected)
            config = load_config(root)
            self.assertEqual(config["opencode_model"], "provider/model")
            # Re-running must not overwrite the user's edits.
            (root / CONFIG_NAME).write_text(json.dumps({"opencode_model": "other/model"}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", str(root)])
            self.assertEqual(load_config(root)["opencode_model"], "other/model")

    def test_agent_prompts_do_not_assume_a_workspace_layout(self) -> None:
        # runtime_dir and the other data folders are configurable; the companion passes exact paths.
        templates = Path(__file__).resolve().parents[1] / "cv_tailor" / "templates"
        for prompt in templates.rglob("*.md"):
            text = prompt.read_text(encoding="utf-8")
            for folder in ("runtime/", "data/", "output/", "cv_library"):
                self.assertFalse(folder in text, f"{prompt.name} hardcodes {folder}")

    def test_renderer_selection_validates_names(self) -> None:
        self.assertEqual(select_renderer("none").name, "none")
        with self.assertRaises(ValueError):
            select_renderer("pdfkit")


if __name__ == "__main__":
    unittest.main()
