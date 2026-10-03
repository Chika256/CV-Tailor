"""Read and edit DOCX paragraphs with the standard library only.

Word is not required. Paragraphs are located in ``word/document.xml`` by a small tag scanner
and edited in place as text, so every other part of the package (styles, numbering, headers,
relationships, media) is copied byte for byte and the original layout is preserved.

A paragraph is *editable* only when it holds plain text runs. Paragraphs with tabs, line breaks,
hyperlinks, fields, drawings or tracked changes are reported but locked, because replacing
their text could corrupt them.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .docx_ops import PlanError

DOCUMENT_PART = "word/document.xml"
PARAGRAPH_START = re.compile(r"<w:p(?=[\s>/])")
PARAGRAPH_END = "</w:p>"
TEXT_NODE = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)
STYLE = re.compile(r'<w:pStyle\s+w:val="([^"]*)"')
NUMBERING = re.compile(r"<w:numPr>.*?</w:numPr>", re.DOTALL)
NUM_ID = re.compile(r'<w:numId\s+w:val="(\d+)"')
# Anything that makes a plain text replacement unsafe.
LOCKING_MARKUP = re.compile(
    r"<w:tab[\s/>]|<w:br[\s/>]|<w:cr[\s/>]|<w:hyperlink[\s>]|<w:fldSimple[\s>]|<w:fldChar[\s/>]"
    r"|<w:instrText|<w:drawing|<w:pict|<w:object|<w:sdt[\s>]|<w:ins\s|<w:del\s|<w:footnoteReference"
    r"|<w:endnoteReference|<mc:AlternateContent"
)


def _scan_paragraphs(xml: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of top-level paragraphs, ignoring ones nested in text boxes."""
    spans: list[tuple[int, int]] = []
    position = 0
    while True:
        match = PARAGRAPH_START.search(xml, position)
        if match is None:
            return spans
        start = match.start()
        depth = 1
        cursor = match.end()
        while depth:
            next_open = PARAGRAPH_START.search(xml, cursor)
            next_close = xml.find(PARAGRAPH_END, cursor)
            if next_close == -1:
                return spans
            if next_open is not None and next_open.start() < next_close:
                depth += 1
                cursor = next_open.end()
            else:
                depth -= 1
                cursor = next_close + len(PARAGRAPH_END)
        spans.append((start, cursor))
        position = cursor


def _paragraph_text(paragraph_xml: str) -> str:
    return "".join(html.unescape(part) for part in TEXT_NODE.findall(paragraph_xml))


def _read_document(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(DOCUMENT_PART).decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as error:
        raise PlanError(f"{path.name} is not a readable DOCX file") from error


def extract_cv(path: Path, sha256: str, page_count: int | None) -> dict[str, Any]:
    """Describe every paragraph of the document in the cv.json schema the agents consume."""
    xml = _read_document(path)
    paragraphs: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(_scan_paragraphs(xml), 1):
        block = xml[start:end]
        text = _paragraph_text(block)
        style = STYLE.search(block)
        numbering = NUMBERING.search(block)
        # A numbered paragraph is a list item unless its numId is 0 ("numbering removed").
        num_id = NUM_ID.search(numbering.group(0)) if numbering else None
        is_list = numbering is not None and (num_id.group(1) if num_id else "1") != "0"
        paragraphs.append(
            {
                "id": f"document:p{index:04d}",
                "story": "document",
                "text": text,
                "style": style.group(1) if style else "",
                "list_type": 2 if is_list else 0,
                "editable": bool(text.strip()) and not LOCKING_MARKUP.search(block),
            }
        )
    return {
        "schema_version": 1,
        "source_file": path.name,
        "sha256": sha256,
        "page_count": page_count,
        "paragraphs": paragraphs,
    }


def _replace_text(paragraph_xml: str, new_text: str) -> str:
    """Put the new text in the first text run and blank the rest, keeping that run's formatting."""
    seen = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal seen
        if seen:
            return "<w:t></w:t>"
        seen = True
        return f'<w:t xml:space="preserve">{escape(new_text)}</w:t>'

    return TEXT_NODE.sub(substitute, paragraph_xml)


def apply_plan(source: Path, destination: Path, replacements: list[dict[str, Any]]) -> int:
    """Write a copy of ``source`` with each validated replacement applied; returns the count."""
    xml = _read_document(source)
    spans = _scan_paragraphs(xml)
    wanted: dict[int, dict[str, Any]] = {}
    for replacement in replacements:
        match = re.fullmatch(r"document:p(\d{4})", str(replacement.get("paragraph_id")))
        if match is None:
            raise PlanError(f"Unsupported paragraph id: {replacement.get('paragraph_id')!r}")
        index = int(match.group(1))
        if not 1 <= index <= len(spans):
            raise PlanError(f"Paragraph index is outside the document: {index}")
        wanted[index] = replacement

    pieces: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(spans, 1):
        if index not in wanted:
            continue
        block = xml[start:end]
        if LOCKING_MARKUP.search(block):
            raise PlanError(f"Paragraph {index} contains markup that cannot be edited safely")
        if _paragraph_text(block) != wanted[index]["original_text"]:
            raise PlanError(f"Safety stop: paragraph {index} no longer matches the extracted master text")
        pieces.append(xml[cursor:start])
        pieces.append(_replace_text(block, str(wanted[index]["new_text"])))
        cursor = end
    pieces.append(xml[cursor:])
    edited = "".join(pieces)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as copy:
        for item in original.infolist():
            data = edited.encode("utf-8") if item.filename == DOCUMENT_PART else original.read(item.filename)
            copy.writestr(item, data)
    return len(wanted)
