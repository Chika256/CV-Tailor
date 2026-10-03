"""Turn a DOCX into a PDF and page layout facts, using whichever backend is available.

Backends
- ``word``: Microsoft Word through COM (Windows). Gives exact per-paragraph page positions.
- ``libreoffice``: ``soffice --headless`` (Windows, macOS, Linux). Page count always; per-paragraph
  positions when the optional ``pypdf`` package is installed.
- ``none``: no rendering. Tailoring still works, but layout checks and PDF review are skipped.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESOURCES = Path(__file__).resolve().parent / "resources"
LIBREOFFICE_NAMES = ("soffice", "libreoffice")
LIBREOFFICE_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


class RenderError(RuntimeError):
    pass


@dataclass
class Layout:
    page_count: int | None
    paragraphs: list[dict[str, Any]] | None
    pdf_path: Path | None
    backend: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "backend": self.backend,
            "page_count": self.page_count,
            "paragraphs": self.paragraphs,
        }


class Renderer:
    name = "none"

    def render(self, docx: Path, pdf: Path | None) -> Layout:
        return Layout(None, None, None, self.name)


class WordRenderer(Renderer):
    name = "word"

    def render(self, docx: Path, pdf: Path | None) -> Layout:
        script = RESOURCES / "render_word.ps1"
        with tempfile.TemporaryDirectory() as directory:
            layout_path = Path(directory) / "layout.json"
            command = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-DocumentPath", str(docx), "-LayoutPath", str(layout_path),
            ]
            if pdf is not None:
                command += ["-PdfPath", str(pdf)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            if completed.returncode != 0 or not layout_path.exists():
                raise RenderError(f"Word rendering failed: {(completed.stderr or completed.stdout).strip()[:300]}")
            data = json.loads(layout_path.read_text(encoding="utf-8-sig"))
        return Layout(data["page_count"], data.get("paragraphs"), pdf, self.name)


class LibreOfficeRenderer(Renderer):
    name = "libreoffice"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def render(self, docx: Path, pdf: Path | None) -> Layout:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            profile = out / "profile"  # a private profile avoids clashing with a running LibreOffice
            command = [
                self.executable, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                "--convert-to", "pdf", "--outdir", str(out), str(docx),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            produced = out / f"{docx.stem}.pdf"
            if completed.returncode != 0 or not produced.exists():
                raise RenderError(f"LibreOffice conversion failed: {(completed.stderr or completed.stdout).strip()[:300]}")
            if pdf is not None:
                shutil.copy2(produced, pdf)
            pages = _pdf_page_texts(produced)
            if pages is None:
                return Layout(_count_pdf_pages(produced), None, pdf, self.name)
            return Layout(len(pages), _paragraph_pages(docx, pages), pdf, self.name)


def _count_pdf_pages(pdf: Path) -> int | None:
    """Best-effort page count without a PDF library; None when the file uses packed objects."""
    count = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", pdf.read_bytes()))
    return count or None


def _pdf_page_texts(pdf: Path) -> list[str] | None:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        return None
    return [(page.extract_text() or "") for page in PdfReader(str(pdf)).pages]


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _paragraph_pages(docx: Path, pages: list[str]) -> list[dict[str, Any]]:
    """Locate each paragraph's first and last characters in the rendered page text."""
    from .docx_io import extract_cv

    texts = [_normal(page) for page in pages]
    result: list[dict[str, Any]] = []
    floor = 0
    for index, item in enumerate(extract_cv(docx, "", None)["paragraphs"], 1):
        text = _normal(item["text"])
        if not text:
            result.append({"index": index, "start_page": floor + 1, "end_page": floor + 1, "is_list": False, "length": 0})
            continue
        start = next((p for p in range(floor, len(texts)) if text[:24] in texts[p]), floor)
        end = next((p for p in range(start, len(texts)) if text[-24:] in texts[p]), start)
        floor = end
        result.append(
            {"index": index, "start_page": start + 1, "end_page": end + 1,
             "is_list": item["list_type"] != 0, "length": len(item["text"].strip())}
        )
    return result


def find_libreoffice() -> str | None:
    for name in LIBREOFFICE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return next((path for path in LIBREOFFICE_PATHS if Path(path).exists()), None)


def word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        completed = subprocess.run(
            ["reg", "query", r"HKCR\Word.Application\CLSID"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def select_renderer(backend: str = "auto") -> Renderer:
    if backend not in {"auto", "word", "libreoffice", "none"}:
        raise ValueError("render_backend must be one of: auto, word, libreoffice, none")
    if backend == "none":
        return Renderer()
    if backend in {"auto", "word"} and word_available():
        return WordRenderer()
    if backend == "word":
        raise RenderError("render_backend is 'word' but Microsoft Word is not available on this machine")
    office = find_libreoffice()
    if office:
        return LibreOfficeRenderer(office)
    if backend == "libreoffice":
        raise RenderError("render_backend is 'libreoffice' but soffice was not found on PATH or in default locations")
    return Renderer()
