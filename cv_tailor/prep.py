"""Deterministic helpers that keep LLM work small: input assembly, scoring, layout checks."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape

BOILERPLATE_LINE = re.compile(
    r"equal opportunit|diversity|inclusi(on|ve) (employer|workplace)|reasonable adjustment|"
    r"privacy (policy|notice)|cookie|data protection|gdpr|"
    r"\b(apply|save|share|report)( now| today| job| this job)?\s*$|"
    r"follow us|sign up for (job )?alerts|back to (search|jobs)|"
    r"we are an? .{0,40}employer|accessibility statement",
    re.IGNORECASE,
)
STOPWORDS = frozenset(
    """a about above across after all also an and any are as at be been being but by can could do does each
    etc for from had has have having how if in into is it its may more most must new no not of on one or our
    out over per should so some such than that the their them then there these they this to up us use using was
    we well what when where which while who will with within without would you your work working team teams role
    job company ability able strong good great experience experienced years year skills skill knowledge
    understanding opportunity join looking responsible responsibilities requirements required including
    across ensure support help make build part just careers career future candidates candidate offering
    receiving top success opportunities apply benefits salary based location hybrid remote office days
    day time people business world industry leading looking seeking""".split()
) | frozenset(
    # Everyday verbs, adjectives and job-ad filler that are never skills. Words that can name a
    # skill or domain on their own (product, design, lead, communication) are deliberately absent.
    """write writes writing written try tries trying learn learning learned ship ships shipping power powers
    powering deliver delivering create creating provide providing joining grow growing improve improving keep
    take taking bring want need needs like love enjoy get give see find think know own owning drive driving
    small large big clear fast exciting excited passionate curious willing willingness solid nice ideal
    ideally various varied real key high low wide broad current latest modern first best better plus bonus
    desirable essential preferred similar related relevant other every many several multiple range variety
    tool tools listing listings posting description things way ways environment culture mission values impact
    place home hours week month level entry mindset attitude passion exposure familiarity familiar
    proficiency proficient hands through changes another before again already always often both either
    even still very much only together around between during under until whether though although
    understand rely reach add catch caught fix fail become allow enable start stay feel seem mean
    bad accurate simple easy hard right wrong important useful early late morning afternoon daily weekly
    today tomorrow""".split()
)
FAMILIES = (
    ("qa", ("qa", "test", "quality assurance", "sdet")),
    ("ai", ("ai ", " ai", "machine learning", "ml ", "data scientist", "llm", "agentic")),
    ("data", ("data", "analyst", "analytics", "bi ", "insight")),
    ("frontend", ("front-end", "frontend", "front end", "ui ", "react", "web developer")),
    ("fullstack", ("full stack", "full-stack", "fullstack")),
    ("backend", ("backend", "back-end", "back end", "java", "python", "api", "platform", "infrastructure", "cloud")),
    ("consulting", ("consult", "programme", "project manager", "business analyst")),
)


def clean_description(text: str, limit: int = 12000) -> tuple[str, int]:
    """Drop obvious non-requirement boilerplate lines; returns (text, characters_removed)."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if len(stripped) < 160 and BOILERPLATE_LINE.search(stripped):
            continue
        kept.append(stripped)
    cleaned = "\n".join(kept).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit("\n", 1)[0]
    return cleaned, max(0, len(text) - len(cleaned))


def job_fingerprint(url: str, description: str) -> str:
    parsed = urlparse(url)
    stable = f"{parsed.netloc}{parsed.path}".lower().rstrip("/")
    body = re.sub(r"\s+", " ", description).strip().lower()
    return hashlib.sha256(f"{stable}\0{body}".encode()).hexdigest()[:16]


def role_family(title: str) -> str:
    lowered = f" {title.lower()} "
    for family, markers in FAMILIES:
        if any(marker in lowered for marker in markers):
            return family
    return "general"


def _terms(text: str) -> list[str]:
    words = (word.strip(".") for word in re.findall(r"[a-z][a-z0-9+#.]{1,}", text.lower()))
    # Checking the stem too means one stopword covers its inflections ("owns", "ensures", "joined").
    return [word for word in words if len(word) > 2 and word not in STOPWORDS and _stem(word) not in STOPWORDS]


def _without_heading(description: str, title: str) -> str:
    """Drop a first line that repeats the job title ("Title - Company (...)"); it is not a requirement."""
    first, _, rest = description.strip().partition("\n")
    if title.strip() and title.strip().lower() in first.lower():
        return rest
    return description


def _stem(word: str) -> str:
    """Light, symmetric normalisation so "testing", "tests" and "tested" all match "test"."""
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not word.endswith("ss"):
            return word[: -len(suffix)]
    return word


def fit_score(
    description: str, evidence_text: str, top: int = 40, ignore: str = "", title: str = ""
) -> dict[str, Any]:
    """Share of the job's most repeated terms that appear anywhere in the user's evidence.

    Terms are compared by stem; missing terms are reported in the listing's own wording.
    """
    skip = {_stem(word) for word in _terms(ignore)}
    counts: Counter[str] = Counter()
    wording: dict[str, str] = {}
    for word in _terms(_without_heading(description, title)):
        stem = _stem(word)
        if stem not in skip:
            counts[stem] += 1
            wording.setdefault(stem, word)
    wanted = [stem for stem, _ in counts.most_common(top)]
    if not wanted:
        return {"score": 0, "missing": []}
    have = {_stem(word) for word in _terms(evidence_text)}
    missing = [wording[stem] for stem in wanted if stem not in have]
    return {"score": round(100 * (len(wanted) - len(missing)) / len(wanted)), "missing": missing[:10]}


def render_cv(cv_document: dict[str, Any]) -> str:
    """Compact CV listing: id, whether it may be edited, whether it is a bullet, and the text."""
    lines = []
    for item in cv_document["paragraphs"]:
        if not str(item.get("text", "")).strip():
            continue
        flags = ("E" if item.get("editable") else "L") + ("b" if item.get("list_type") else "")
        lines.append(f"{item['id']} [{flags}] {item['text']}")
    return "\n".join(lines)


def build_input(
    job: dict[str, Any],
    cv_document: dict[str, Any],
    answers: str | None,
    knowledge: dict[str, Any],
    template: dict[str, Any] | None,
    extra: str = "",
) -> str:
    description, _ = clean_description(str(job["description"]))
    parts = [
        "# INPUT PACKAGE\nEverything needed is in this one file. Do not read any other file.",
        f"## Job\nTitle: {job.get('title')}\nCompany: {job.get('company')}\nLocation: {job.get('location') or 'n/a'}\n"
        f"Source: {job.get('url')}\n\n{description}",
        "## Master CV paragraphs\nFormat: `id [E|L][b] text`. E = editable, L = locked, b = bullet.\n\n"
        + render_cv(cv_document),
    ]
    if answers:
        parts.append(f"## Answers to your earlier questions (verbatim)\n{answers}")
    parts.append(
        "## Knowledge base (approved evidence; attributed to the source shown)\n"
        + json.dumps(knowledge, ensure_ascii=False, separators=(",", ":"))
    )
    if template:
        plan = [
            {"id": r["paragraph_id"], "new_text": r["new_text"]}
            for r in template.get("replacements", [])
        ]
        parts.append(
            f"## Prior plan for a similar {template.get('family')} role "
            f"({template.get('company')} - {template.get('title')})\n"
            "Starting point only. Reuse wording that genuinely fits THIS job, drop anything that does not, "
            "and verify every claim against the CV and knowledge base. Never carry over company-specific claims.\n"
            + json.dumps(plan, ensure_ascii=False, separators=(",", ":"))[:3500]
        )
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) + "\n"


def deterministic_layout_issues(layout: dict[str, Any], expected_pages: int) -> list[str]:
    """Cheap structural checks on per-paragraph page positions. Empty list means likely fine."""
    issues: list[str] = []
    pages = layout.get("page_count")
    if pages is None or not expected_pages:
        return issues  # nothing was rendered, so there is nothing to check
    if pages != expected_pages:
        issues.append(f"Page count changed from {expected_pages} to {pages}.")
    paragraphs = layout.get("paragraphs")
    if not isinstance(paragraphs, list):
        return issues  # a backend without per-paragraph positions: page count is all we can check
    for item in paragraphs:
        if item.get("start_page") != item.get("end_page") and item.get("length", 0) > 0:
            issues.append(f"Paragraph {item.get('index')} is split across a page break.")
    for position, (current, following) in enumerate(zip(paragraphs, paragraphs[1:], strict=False)):
        if current.get("end_page") == following.get("start_page") or current.get("length", 0) == 0:
            continue
        if following.get("length", 0) == 0:
            continue
        if not current.get("is_list") and current.get("length", 0) <= 120 and following.get("is_list"):
            issues.append(f"Heading-like paragraph {current.get('index')} is stranded at the bottom of a page.")
        elif current.get("is_list") and following.get("is_list"):
            # A long entry may continue on the next page, but one lonely bullet on either side looks wrong.
            before = _bullet_run(paragraphs, position, step=-1)
            after = _bullet_run(paragraphs, position + 1, step=1)
            if before <= 1 or after <= 1:
                issues.append(
                    f"A bullet list is split awkwardly across a page break after paragraph {current.get('index')} "
                    f"({before} bullet(s) before the break, {after} after)."
                )
    return issues


def _bullet_run(paragraphs: list[dict[str, Any]], start: int, step: int) -> int:
    count = 0
    index = start
    while 0 <= index < len(paragraphs) and paragraphs[index].get("is_list"):
        count += 1
        index += step
    return count


def write_cover_letter_docx(path: Path, header_lines: list[str], date_text: str, letter: dict[str, Any]) -> None:
    """Write a plain, application-ready cover letter DOCX with no third-party dependency."""

    def paragraph(text: str, bold: bool = False, after: int = 160) -> str:
        run_properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
        return (
            f'<w:p><w:pPr><w:spacing w:after="{after}"/></w:pPr><w:r>{run_properties}'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
        )

    body = [paragraph(header_lines[0], bold=True, after=40)] if header_lines else []
    body += [paragraph(line, after=40) for line in header_lines[1:]]
    body += [paragraph(date_text, after=240), paragraph(letter["salutation"])]
    body += [paragraph(text) for text in letter["paragraphs"]]
    body += [paragraph(letter["closing"], after=40), paragraph(header_lines[0] if header_lines else "")]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
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
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


def validate_letter(letter: dict[str, Any]) -> dict[str, Any]:
    if letter.get("schema_version") != 1 or letter.get("status") != "ready":
        raise ValueError("The cover letter result must use schema_version 1 and status ready")
    paragraphs = letter.get("paragraphs")
    if not isinstance(paragraphs, list) or not 2 <= len(paragraphs) <= 5:
        raise ValueError("The cover letter must contain 2 to 5 paragraphs")
    for key in ("salutation", "closing"):
        if not isinstance(letter.get(key), str) or not letter[key].strip():
            raise ValueError(f"The cover letter is missing {key}")
    if any(not isinstance(text, str) or not text.strip() or "\n" in text for text in paragraphs):
        raise ValueError("Cover letter paragraphs must be non-empty single-line strings")
    if sum(len(text.split()) for text in paragraphs) > 380:
        raise ValueError("The cover letter exceeds 380 words")
    return letter
