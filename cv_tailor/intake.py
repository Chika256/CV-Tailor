"""Validating a job listing submitted by the browser extension."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def validate_job_payload(payload: dict[str, Any], minimum_characters: int) -> dict[str, Any]:
    """Check a captured listing and return the normalised job record, or raise ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("The request body must be a JSON object")
    url = required_string(payload, "url", 4000)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("The captured page URL is invalid")
    title = required_string(payload, "title", 500)
    company = required_string(payload, "company", 500)
    description = required_string(payload, "description", 200000)
    if len(description) < minimum_characters:
        raise ValueError(
            f"The extracted description is too short ({len(description)} characters). "
            "Expand the complete listing and try again."
        )
    lowered = description.lower()
    signals = sum(
        token in lowered
        for token in (
            "responsibil",
            "requirement",
            "qualification",
            "experience",
            "skills",
            "about the role",
            "what you'll",
            "what you will",
            "you will",
            "you'll",
            "about you",
            "looking for",
            "the role",
            "mission",
            "bonus",
            "benefits",
        )
    )
    # Short pages need two signals; a long, substantial description needs only one.
    if signals < 2 and not (signals >= 1 and len(description) >= 1500):
        raise ValueError("The page does not appear to contain a complete job description")
    return {
        "url": url,
        "title": title,
        "company": company,
        "location": optional_string(payload.get("location"), 500),
        "description": description,
        "source": optional_string(payload.get("source"), 100) or parsed.netloc,
        "extraction": payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {},
    }


def required_string(payload: dict[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required field: {key}")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"Field is too long: {key}")
    return value


def optional_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]
