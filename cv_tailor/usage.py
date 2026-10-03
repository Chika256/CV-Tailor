"""Token usage: what each agent run cost, read from OpenCode's event stream and kept per job.

OpenCode's ``--format json`` output has one ``step_finish`` event per model call, carrying that call's
token counts. Each agent run is summed and appended to ``usage.json`` in the job folder, so a job's
cost survives an agent running more than once (after clarification answers or a QA revision).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .docx_ops import read_json, write_json

USAGE_FILE = "usage.json"
COUNTS = ("input", "cache_read", "cache_write", "output", "reasoning")


def tokens_from_output(output: str) -> dict[str, int] | None:
    """Sum the token counts of every model call in an OpenCode event stream; None if it reported none."""
    totals = dict.fromkeys(COUNTS, 0)
    steps = 0
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "step_finish":
            continue
        part = record.get("part")
        tokens = part.get("tokens") if isinstance(part, dict) else None
        if not isinstance(tokens, dict):
            continue
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            cache = {}
        values = {
            "input": tokens.get("input"),
            "cache_read": cache.get("read"),
            "cache_write": cache.get("write"),
            "output": tokens.get("output"),
            "reasoning": tokens.get("reasoning"),
        }
        for key, value in values.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += value
        steps += 1
    if not steps:
        return None
    return {**totals, "steps": steps, "total": sum(totals.values())}


def record_run(job_dir: Path, agent: str, model: str, variant: str | None, seconds: float,
               tokens: dict[str, int]) -> None:
    """Append one agent run to the job's usage file."""
    path = job_dir / USAGE_FILE
    usage = read_json(path) if path.exists() else {"runs": []}
    usage["runs"].append(
        {"agent": agent, "model": model, "variant": variant, "seconds": seconds, "tokens": tokens}
    )
    write_json(path, usage)


def job_usage(job_dir: Path) -> dict[str, Any] | None:
    """A job's runs and their total tokens and seconds; None if no run was recorded."""
    path = job_dir / USAGE_FILE
    if not path.exists():
        return None
    runs: list[dict[str, Any]] = read_json(path).get("runs", [])
    if not runs:
        return None
    return {
        "job_id": job_dir.name,
        "runs": runs,
        "total": sum(int(run["tokens"]["total"]) for run in runs),
        "seconds": round(sum(float(run["seconds"]) for run in runs), 1),
    }


def summarise(jobs_dir: Path) -> list[dict[str, Any]]:
    """Usage for every job that recorded any, oldest first."""
    found = (job_usage(path) for path in sorted(jobs_dir.iterdir()) if path.is_dir())
    return [usage for usage in found if usage is not None]


def report(jobs: list[dict[str, Any]]) -> str:
    """A plain-text table: one row per job and agent run, then the median per agent and per job."""
    if not jobs:
        return "No token usage recorded yet: run a job first."
    header = f"{'job':<26} {'agent':<17} {'input':>7} {'cached':>7} {'output':>7} {'reason':>7} {'total':>7} {'secs':>6}"
    lines = [header, "-" * len(header)]
    per_agent: dict[str, list[int]] = {}
    for job in jobs:
        for run in job["runs"]:
            tokens = run["tokens"]
            per_agent.setdefault(run["agent"], []).append(int(tokens["total"]))
            lines.append(
                f"{job['job_id']:<26} {run['agent']:<17} {tokens['input']:>7} {tokens['cache_read']:>7} "
                f"{tokens['output']:>7} {tokens['reasoning']:>7} {tokens['total']:>7} {run['seconds']:>6}"
            )
    lines.append("")
    for agent, totals in sorted(per_agent.items()):
        lines.append(f"median {agent:<17} {statistics.median(totals):>9,.0f} tokens over {len(totals)} run(s)")
    job_totals = [int(job["total"]) for job in jobs]
    lines.append(f"median per job{'':<10} {statistics.median(job_totals):>9,.0f} tokens over {len(jobs)} job(s)")
    return "\n".join(lines)
