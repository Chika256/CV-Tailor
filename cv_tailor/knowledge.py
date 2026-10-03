"""Persistent, source-backed evidence store for the CV Tailor companion.

Three kinds of evidence are kept, each with its provenance:

* ``cv``      - paragraphs imported read-only from CVs in the workspace.
* ``answer``  - clarification batches the user answered (questions + exact answer).
* ``note``    - facts the user adds or corrects directly, or imports from chat exports.

Rows are never deleted; outdated statements are retired so history stays auditable.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
KINDS = {"cv", "answer", "note"}
MIN_CV_PARAGRAPH = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    superseded_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_status ON evidence(status, kind);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    family TEXT NOT NULL DEFAULT 'general',
    fit_score INTEGER,
    status TEXT NOT NULL DEFAULT 'tailored',
    output_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
APPLICATION_STATUSES = ("tailored", "applied", "interview", "offer", "rejected", "no_reply", "skipped")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(kind: str, source: str, text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(f"{kind}\0{source}\0{normalized}".encode()).hexdigest()


def read_docx_paragraphs(path: Path) -> list[str]:
    """Read paragraph text from a DOCX without opening it in Word."""
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{W_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W_NS}t"))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


class KnowledgeBase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        # sqlite3's own context manager only commits; close explicitly so Windows releases the file.
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(
        self,
        kind: str,
        source: str,
        text: str,
        topic: str = "",
        question: str = "",
    ) -> int | None:
        """Insert one record; returns its id, or None when it is an exact duplicate."""
        if kind not in KINDS:
            raise ValueError("Unknown evidence kind")
        text = text.strip()
        if not text:
            raise ValueError("Evidence text is required")
        digest = _digest(kind, "" if kind == "cv" else source, f"{question}\n{text}")
        now = _now()
        with self.lock, self._connect() as connection:
            if kind == "cv":
                # The same sentence recurs across tailored CVs; keep one row and list every source.
                existing = connection.execute(
                    "SELECT id, source FROM evidence WHERE digest = ?", (digest,)
                ).fetchone()
                if existing is not None:
                    if source not in existing["source"].split("; "):
                        connection.execute(
                            "UPDATE evidence SET source = ? WHERE id = ?",
                            (f"{existing['source']}; {source}"[:600], existing["id"]),
                        )
                    return None
            try:
                cursor = connection.execute(
                    "INSERT INTO evidence (kind, source, topic, question, text, digest, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, source[:300], topic[:200], question.strip(), text, digest, now, now),
                )
            except sqlite3.IntegrityError:
                return None
            return int(cursor.lastrowid)

    def retire(self, evidence_id: int, superseded_by: int | None = None) -> bool:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE evidence SET status = 'retired', superseded_by = ?, updated_at = ?"
                " WHERE id = ? AND status = 'active'",
                (superseded_by, _now(), evidence_id),
            )
            return cursor.rowcount > 0

    def correct(self, evidence_id: int, text: str) -> int:
        """Replace a statement; the old row is retired and points at its correction."""
        with self._connect() as connection:
            old = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        if old is None:
            raise FileNotFoundError("Unknown knowledge record")
        new_id = self.add("note", f"correction of #{evidence_id}", text, old["topic"], old["question"])
        if new_id is None:
            raise ValueError("That correction already exists")
        self.retire(evidence_id, new_id)
        return new_id

    def record_answers(
        self,
        job_id: str,
        company: str,
        title: str,
        questions: list[str],
        answers: str,
    ) -> int | None:
        """Keep the question batch together with the exact answer, so scope is never guessed."""
        numbered = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
        return self.add(
            "answer",
            f"job {job_id} ({company} - {title})",
            answers,
            topic=title,
            question=numbered,
        )

    def import_cvs(
        self, directory: Path, exclude: set[Path] | None = None, require_cv_in_name: bool = True
    ) -> dict[str, int]:
        """Import every CV DOCX in the workspace, read-only. Skips Word lock files."""
        exclude = {path.resolve() for path in (exclude or set())}
        summary = {"files": 0, "added": 0}
        for path in sorted(directory.glob("*.docx")):
            if path.name.startswith("~$") or path.resolve() in exclude:
                continue
            if require_cv_in_name and "cv" not in path.name.lower():
                continue
            try:
                paragraphs = read_docx_paragraphs(path)
            except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
                continue
            summary["files"] += 1
            for paragraph in paragraphs:
                if len(paragraph) >= MIN_CV_PARAGRAPH and self.add("cv", path.name, paragraph):
                    summary["added"] += 1
        return summary

    def import_chat_export(self, label: str, text: str) -> int:
        """Import a pasted/plain-text or JSON chat export as user-supplied notes."""
        pieces: list[str] = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            pieces = _human_messages(data)
        else:
            pieces = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
        added = 0
        for piece in pieces:
            if len(piece) >= MIN_CV_PARAGRAPH and self.add("note", f"chat export: {label}", piece[:4000]):
                added += 1
        return added

    def log_application(
        self, job_id: str, company: str, title: str, url: str, family: str, fit: int | None, output_file: str
    ) -> None:
        now = _now()
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO applications (job_id, company, title, url, family, fit_score, output_file, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(job_id) DO UPDATE SET output_file = excluded.output_file, updated_at = excluded.updated_at",
                (job_id, company, title, url, family, fit, output_file, now, now),
            )

    def set_application_status(self, job_id: str, status: str) -> None:
        if status not in APPLICATION_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(APPLICATION_STATUSES)}")
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?", (status, _now(), job_id)
            )
            if cursor.rowcount == 0:
                raise FileNotFoundError("No tailored application is recorded for that job")

    def application_status(self, job_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        return row["status"] if row else None

    def applications_summary(self) -> dict[str, Any]:
        """Counts overall and per role family, so you can see which tailoring styles get replies."""
        with self._connect() as connection:
            rows = [dict(r) for r in connection.execute("SELECT family, status, COUNT(*) AS n FROM applications GROUP BY family, status")]
        by_family: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        for row in rows:
            by_family.setdefault(row["family"], {})[row["status"]] = row["n"]
            totals[row["status"]] = totals.get(row["status"], 0) + row["n"]
        return {"totals": totals, "by_family": by_family}

    def search(self, query: str = "", limit: int = 50, include_retired: bool = False) -> list[dict[str, Any]]:
        terms = [term for term in re.findall(r"[\w+#.]{2,}", query.lower())][:8]
        clauses = [] if include_retired else ["status = 'active'"]
        params: list[Any] = []
        for term in terms:
            clauses.append("(lower(text) LIKE ? OR lower(topic) LIKE ? OR lower(question) LIKE ? OR lower(source) LIKE ?)")
            params.extend([f"%{term}%"] * 4)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM evidence {where} ORDER BY kind = 'cv', id DESC LIMIT ?",
                (*params, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, job_text: str = "", cv_limit: int = 80) -> dict[str, Any]:
        """Evidence the planning agent may rely on.

        Answers and notes are always included in full. Other-CV wording is ranked by overlap
        with the job description so the file stays small enough to read completely.
        """
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT id, kind, source, topic, question, text FROM evidence WHERE status = 'active' ORDER BY id"
            )]
        for row in rows:
            row.pop("id")  # ids are only useful to the extension; the agent does not need them
            row["text"] = row["text"][:600]
            for empty in [key for key in ("topic", "question") if not row[key]]:
                row.pop(empty)
        cv_rows = [row for row in rows if row["kind"] == "cv"]
        if job_text:
            words = set(re.findall(r"[a-z][a-z+#.]{2,}", job_text.lower()))
            cv_rows.sort(
                key=lambda row: len(words & set(re.findall(r"[a-z][a-z+#.]{2,}", row["text"].lower()))),
                reverse=True,
            )
        return {
            "schema_version": 1,
            "generated_at": _now(),
            "explicit_user_answers": [row for row in rows if row["kind"] == "answer"],
            "user_notes_and_corrections": [row for row in rows if row["kind"] == "note"],
            "other_cv_evidence": cv_rows[:cv_limit],
            "other_cv_evidence_total": len(cv_rows),
        }


def _human_messages(data: Any) -> list[str]:
    """Collect user-authored text from common chat export shapes (ChatGPT, Claude, OpenCode)."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            author = node.get("author")
            role = node.get("role") or node.get("sender") or (author.get("role") if isinstance(author, dict) else None)
            if role in {"user", "human"}:
                text = node.get("text") or node.get("content")
                if isinstance(text, list):
                    text = "\n".join(
                        part.get("text", "") if isinstance(part, dict) else str(part) for part in text
                    )
                elif isinstance(text, dict):
                    text = "\n".join(str(part) for part in text.get("parts", []))
                if isinstance(text, str) and text.strip():
                    found.append(text.strip())
                    return
            for value in node.values():
                walk(value)

    walk(data)
    return found
