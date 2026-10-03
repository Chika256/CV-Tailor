"""A fictional sample CV, written as a real DOCX with no third-party library.

Used by ``cv-tailor init --sample``, the examples and the tests, so no real personal data is
ever needed to try or test the project.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

NAMESPACE = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

# kind: "p" plain, "b" bullet, "h" heading (bold), "t" text then tab then text (a locked layout line)
SAMPLE_LINES: list[tuple[str, str]] = [
    ("h", "Alex Morgan"),
    ("p", "1 Example Street, Exampletown, AB1 2CD | 07700 900123 | alex.morgan@example.com | https://github.com/example-alex"),
    ("p", "Right to work in the UK - no visa sponsorship required."),
    ("p", "Computer Science graduate with hands-on experience building Python and Java services, REST APIs and automated tests."),
    ("h", "Technical skills"),
    ("p", "Languages: Python, Java, JavaScript, SQL"),
    ("p", "Tools: Git, Docker, GitHub Actions, PostgreSQL"),
    ("h", "Projects"),
    ("t", "Inventory Tracker, Python, Flask, PostgreSQL\tMar 2025"),
    ("b", "Built a REST API for stock management with role-based access and input validation."),
    ("b", "Wrote unit and integration tests that run in a continuous integration pipeline."),
    ("b", "Containerised the service with Docker for repeatable local and cloud deployment."),
    ("t", "Study Planner, JavaScript, React\tOct 2024"),
    ("b", "Developed a responsive planner that stores tasks locally and syncs them on request."),
    ("b", "Collaborated with a team of four using Agile ceremonies and code reviews."),
    ("h", "Education"),
    ("t", "BSc Computer Science (2:1), Example University\t2022 - 2025"),
]

NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:numbering {NAMESPACE}><w:abstractNum w:abstractNumId=\"0\"><w:lvl w:ilvl=\"0\"><w:start w:val=\"1\"/>"
    '<w:numFmt w:val="bullet"/><w:lvlText w:val="-"/><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:lvl>'
    '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
)
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {NAMESPACE}><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii=\"Calibri\" w:hAnsi=\"Calibri\"/>"
    '<w:sz w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="60"/></w:pPr></w:pPrDefault>'
    "</w:docDefaults></w:styles>"
)


def _run(text: str, bold: bool = False) -> str:
    properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(kind: str, text: str) -> str:
    if kind == "b":
        return (
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
            f"{_run(text)}</w:p>"
        )
    if kind == "t":
        left, right = text.split("\t")
        return f"<w:p>{_run(left, bold=True)}<w:r><w:tab/></w:r>{_run(right)}</w:p>"
    return f"<w:p>{_run(text, bold=kind == 'h')}</w:p>"


def write_sample_cv(path: Path) -> Path:
    body = "".join(_paragraph(kind, text) for kind, text in SAMPLE_LINES)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {NAMESPACE}><w:body>{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:right="1000" w:bottom="1000" '
        'w:left="1000" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", STYLES)
        archive.writestr("word/numbering.xml", NUMBERING)
    return path
