"""The per-job application report, and the clarification questions saved with a job."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .docx_ops import read_json


def write_report(
    path: Path,
    job: dict[str, Any],
    result: dict[str, Any],
    job_dir: Path,
) -> None:
    """Write the user-facing report: fit, what changed and why, gaps, and the layout check."""
    qa_path = job_dir / "qa.json"
    qa = read_json(qa_path) if qa_path.exists() else {}
    fit_info = read_json(job_dir / "status.json")
    sections = [
        f"# {job.get('company')} - {job.get('title')}",
        "",
        f"Source: {job.get('url')}",
        "",
        f"Keyword fit: {fit_info.get('fit_score', 'n/a')}% - not covered by your evidence: "
        f"{', '.join(fit_info.get('missing_keywords', [])) or 'none'}",
        "",
        "## Change Summary",
        *[f"- {item}" for item in result.get("change_summary", [])],
        "",
        "## Unsupported Or Weak Requirements",
        *[f"- {item}" for item in result.get("unsupported_requirements", [])],
        "",
        "## Recommendations",
        *[f"- {item}" for item in result.get("recommendations", [])],
        "",
        "## Changes (before -> after)",
        *[
            line
            for item in result.get("replacements", [])
            for line in (
                f"### {item['paragraph_id']}",
                f"- Before: {item['original_text']}",
                f"- After: {item['new_text']}",
                f"- Why: {item['reason']}",
                "",
            )
        ],
        *([] if result.get("replacements") else ["- None. See the change summary for why.", ""]),
        "## Visual QA",
        f"- Application ready: {qa.get('application_ready', 'not verified')}",
        *[f"- {item}" for item in qa.get("issues", []) if isinstance(item, str)],
        "",
    ]
    path.write_text("\n".join(sections), encoding="utf-8")


def _questions_from(result: dict[str, Any]) -> list[str]:
    items = result.get("questions", [])
    return [
        str(item.get("question") if isinstance(item, dict) else item).strip()
        for item in items
        if item
    ]


def _saved_questions(job_dir: Path) -> list[str]:
    path = job_dir / "questions.json"
    if path.exists():
        try:
            return [str(q) for q in read_json(path).get("questions", [])]
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return []
