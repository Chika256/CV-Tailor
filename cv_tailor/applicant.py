"""The user's reusable application details, used by the extension's autofill.

Seeded once from the contact lines of the master CV, then editable. Sensitive or personal
answers (salary, notice period, availability) stay empty until the user sets them, and
demographic questions are never stored or filled.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .docx_ops import read_json, write_json
from .knowledge import read_docx_paragraphs

PROFILE_FIELDS: list[tuple[str, str]] = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("address_line", "Address line"),
    ("city", "City / town"),
    ("postcode", "Postcode"),
    ("country", "Country"),
    ("linkedin", "LinkedIn URL"),
    ("github", "GitHub URL"),
    ("website", "Website / portfolio"),
    ("right_to_work", "Right to work in the UK (yes/no)"),
    ("requires_sponsorship", "Requires visa sponsorship (yes/no)"),
    ("notice_period", "Notice period"),
    ("available_from", "Available from"),
    ("salary_expectation", "Salary expectation"),
]
FIELD_KEYS = {key for key, _ in PROFILE_FIELDS}
POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)


def seed_from_master(master_cv: Path) -> dict[str, str]:
    """Read the name and contact line from the master CV without opening Word."""
    paragraphs = read_docx_paragraphs(master_cv)[:4]
    profile: dict[str, str] = {key: "" for key in FIELD_KEYS}
    if paragraphs:
        parts = paragraphs[0].split()
        profile["first_name"] = parts[0] if parts else ""
        profile["last_name"] = " ".join(parts[1:])
    contact = next((line for line in paragraphs[1:] if "@" in line), "")
    for chunk in (piece.strip() for piece in contact.split("|")):
        lowered = chunk.lower()
        if "@" in chunk and "/" not in chunk:
            profile["email"] = chunk
        elif "linkedin.com" in lowered:
            profile["linkedin"] = chunk
        elif "github.com" in lowered:
            profile["github"] = chunk
        elif re.fullmatch(r"[+\d][\d\s()-]{8,}", chunk):
            profile["phone"] = re.sub(r"\s+", "", chunk)
        elif chunk:
            postcode = POSTCODE.search(chunk)
            if postcode:
                profile["postcode"] = postcode.group(0).upper()
            pieces = [piece.strip() for piece in chunk.split(",") if piece.strip()]
            street = [piece for piece in pieces if not POSTCODE.fullmatch(piece)]
            if street:
                profile["address_line"] = street[0]
            if len(street) > 1:
                profile["city"] = street[-1]
    text = " ".join(paragraphs).lower()
    if "right to work in the uk" in text:
        profile["right_to_work"] = "yes"
        if "no visa sponsorship required" in text:
            profile["requires_sponsorship"] = "no"
    return profile


class ProfileStore:
    def __init__(self, path: Path, master_cv: Path) -> None:
        self.path = path
        self.master_cv = master_cv

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            write_json(self.path, seed_from_master(self.master_cv))
        stored = read_json(self.path)
        return {key: str(stored.get(key, "")) for key in FIELD_KEYS}

    def save(self, values: dict[str, Any]) -> dict[str, str]:
        profile = self.load()
        for key, value in values.items():
            if key not in FIELD_KEYS:
                raise ValueError(f"Unknown profile field: {key}")
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError(f"Invalid value for {key}")
            profile[key] = value.strip()
        write_json(self.path, profile)
        return profile
