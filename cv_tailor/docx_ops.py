from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeGuard, TypeVar

T = TypeVar("T")

PARAGRAPH_ID = re.compile(r"^document:p(\d{4})$")
# Sections whose bullets a plan may remove to keep the CV within the page limit.
REMOVABLE_SECTIONS = ("project", "experience")
MAX_REMOVALS = 6


class PlanError(ValueError):
    pass


def _is_section_heading(item: dict[str, Any]) -> bool:
    """A short, unpunctuated, non-list line such as "Projects" or "WORK EXPERIENCE"."""
    text = str(item.get("text", "")).strip()
    return (
        bool(text) and not item.get("list_type") and len(text) <= 40 and len(text.split()) <= 5
        and not re.search(r"[.,:;|\t]", text)
    )


def _bullet_groups(paragraphs: list[Any]) -> list[list[dict[str, Any]]]:
    """Runs of sibling bullets (same list level, nothing between them) inside Projects and Experience.

    A list item followed by a deeper one is a parent, such as a project title written as a list item,
    and is left out. A cv.json extracted without list levels gives no groups.
    """
    items = [item for item in paragraphs if isinstance(item, dict) and str(item.get("id", "")).startswith("document:")]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    section = ""
    for position, item in enumerate(items):
        level = item.get("list_level")
        if _is_section_heading(item):
            section = str(item["text"]).lower()
        following = items[position + 1] if position + 1 < len(items) else {}
        following_level = following.get("list_level")
        is_parent = (
            isinstance(level, int) and isinstance(following_level, int)
            and bool(following.get("list_type")) and following_level > level
        )
        if (
            item.get("list_type") and isinstance(level, int) and level > 0 and not is_parent
            and any(name in section for name in REMOVABLE_SECTIONS)
        ):
            if current and current[-1].get("list_level") != level:
                groups.append(current)
                current = []
            current.append(item)
        else:
            if current:
                groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def removable_paragraphs(cv_document: dict[str, Any]) -> set[str]:
    """Bullets a plan may remove: in Projects or Experience, not a title, and with at least one sibling."""
    paragraphs = cv_document.get("paragraphs")
    if not isinstance(paragraphs, list):
        return set()
    return {
        str(item["id"])
        for group in _bullet_groups(paragraphs) if len(group) > 1
        for item in group if item.get("editable") is True
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = _retry_on_windows_lock(lambda: json.loads(path.read_text(encoding="utf-8-sig")))
    if not isinstance(value, dict):
        raise PlanError(f"{path.name} must contain a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write atomically: readers see the old file or the new one, never a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary name, so two threads saving the same file cannot write into one temp file.
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        _replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _replace(source: Path, target: Path) -> None:
    _retry_on_windows_lock(lambda: source.replace(target))


def _retry_on_windows_lock(action: Callable[[], T], attempts: int = 40, delay: float = 0.025) -> T:
    # On Windows the HTTP API reading a job's status and the worker replacing it collide: the
    # replace fails while the file is open, and opening fails while the file is being replaced.
    # Both sides hold the file for milliseconds, so retry briefly.
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def validate_tailoring_result(
    result: dict[str, Any], cv_document: dict[str, Any]
) -> dict[str, Any]:
    if result.get("schema_version") != 1:
        raise PlanError("result.json must use schema_version 1")

    status = result.get("status")
    if status == "needs_clarification":
        questions = result.get("questions")
        if not isinstance(questions, list) or not questions:
            raise PlanError("A clarification result must include at least one question")
        if len(questions) > 10:
            raise PlanError("A clarification result may contain at most 10 questions")
        for item in questions:
            if not isinstance(item, dict) or not _plain_text(item.get("question"), 500):
                raise PlanError("Every clarification item must contain a concise question")
        return result

    if status != "ready":
        raise PlanError("result status must be 'ready' or 'needs_clarification'")

    paragraphs = cv_document.get("paragraphs")
    if not isinstance(paragraphs, list):
        raise PlanError("cv.json does not contain a paragraph list")

    editable = {
        item.get("id"): item
        for item in paragraphs
        if isinstance(item, dict) and item.get("editable") is True
    }
    replacements = result.get("replacements")
    if not isinstance(replacements, list):
        raise PlanError("A ready result must contain a replacements list")
    # No replacements is a legitimate answer when the CV already fits, but only with a stated
    # reason, so a malformed or truncated response cannot pass as "no changes needed".
    if not replacements and not result.get("change_summary"):
        raise PlanError("A ready result with no replacements must explain why in change_summary")
    if len(replacements) > 40:
        raise PlanError("A tailoring plan may contain at most 40 replacements")

    removable = removable_paragraphs(cv_document)
    removed: set[str] = set()
    seen: set[str] = set()
    total_delta = 0
    for replacement in replacements:
        if not isinstance(replacement, dict):
            raise PlanError("Each replacement must be a JSON object")
        paragraph_id = replacement.get("paragraph_id")
        if not isinstance(paragraph_id, str) or not PARAGRAPH_ID.fullmatch(paragraph_id):
            raise PlanError(f"Invalid editable paragraph id: {paragraph_id!r}")
        if paragraph_id in seen:
            raise PlanError(f"Duplicate paragraph replacement: {paragraph_id}")
        seen.add(paragraph_id)

        source = editable.get(paragraph_id)
        if source is None:
            raise PlanError(f"Paragraph is not editable: {paragraph_id}")
        original = replacement.get("original_text")
        if not isinstance(original, str) or original != source.get("text"):
            raise PlanError(f"Original text mismatch for {paragraph_id}")
        if replacement.get("remove") is True:
            if paragraph_id not in removable:
                raise PlanError(f"Paragraph cannot be removed: {paragraph_id}")
            if not _plain_text(replacement.get("reason"), 500):
                raise PlanError(f"Replacement reason is missing for {paragraph_id}")
            removed.add(paragraph_id)
            total_delta -= len(original)
            continue
        new_text = replacement.get("new_text")
        if not _plain_text(new_text, 1600):
            raise PlanError(f"Replacement text is invalid for {paragraph_id}")
        if "\n" in new_text or "\r" in new_text:
            raise PlanError(f"Replacement text cannot add paragraphs: {paragraph_id}")

        original_length = len(original)
        maximum_length = max(120, int(original_length * 1.6) + 30)
        if len(new_text) > maximum_length:
            raise PlanError(
                f"Replacement for {paragraph_id} is too long for the existing layout "
                f"({len(new_text)} > {maximum_length})"
            )
        total_delta += len(new_text) - original_length

        if not _plain_text(replacement.get("reason"), 500):
            raise PlanError(f"Replacement reason is missing for {paragraph_id}")

    if total_delta > 700:
        raise PlanError("The tailoring plan expands the CV by more than 700 characters")
    if len(removed) > MAX_REMOVALS:
        raise PlanError(f"A tailoring plan may remove at most {MAX_REMOVALS} bullets")
    for group in _bullet_groups(paragraphs):
        if all(item["id"] in removed for item in group):
            raise PlanError(f"Every bullet of one entry would be removed, from {group[0]['id']}")

    for key in ("change_summary", "unsupported_requirements", "recommendations"):
        values = result.get(key)
        if not isinstance(values, list):
            raise PlanError(f"{key} must be a list")
        if len(values) > 20 or any(not _plain_text(value, 1000) for value in values):
            raise PlanError(f"{key} contains invalid entries")

    return result


def validate_qa_result(
    qa: dict[str, Any], layout: dict[str, Any]
) -> dict[str, Any]:
    if qa.get("schema_version") != 1:
        raise PlanError("qa.json must use schema_version 1")
    expected_pages = layout.get("page_count")
    if not isinstance(expected_pages, int) or expected_pages < 1:
        raise PlanError("layout.json contains an invalid page count")
    if qa.get("pages_inspected") != expected_pages:
        raise PlanError("Visual QA did not confirm inspection of every rendered page")
    if not isinstance(qa.get("application_ready"), bool):
        raise PlanError("qa.json must contain an application_ready boolean")
    for key in ("issues", "notes"):
        values = qa.get(key)
        if not isinstance(values, list) or len(values) > 30:
            raise PlanError(f"qa.json field {key} must be a concise list")
        if any(not _plain_text(value, 1000) for value in values):
            raise PlanError(f"qa.json field {key} contains an invalid entry")
    return qa


def _plain_text(value: Any, maximum_length: int) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum_length
