"""Running OpenCode agents: the command line, the process, usage limits and the JSON reply."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .docx_ops import write_json
from .logs import event

AGENT_FILES = {
    "cv-tailor": ("input.md", "result.json"),
    "cv-tailor-revise": ("revise_input.md", "revised-result.json"),
    "cv-tailor-qa": ("qa_input.md", "qa.json"),
    "cv-tailor-letter": ("letter_input.md", "letter.json"),
}


class RateLimited(RuntimeError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"The model usage limit was reached; it resets in about {seconds // 60} minutes")
        self.seconds = seconds


def run_agent(
    agent: str,
    job_dir: Path,
    job_id: str,
    *,
    root: Path,
    config: dict[str, Any],
    attachments: list[Path] | None = None,
) -> None:
    """Run one OpenCode agent on a job and save its JSON reply as that agent's output file."""
    if agent not in AGENT_FILES:
        raise RuntimeError(f"Unsupported OpenCode agent: {agent}")
    relative_job_dir = job_dir.relative_to(root)
    prompt = (
        f"Process CV tailoring job {job_id}. Your complete input is the single file "
        f"{relative_job_dir / AGENT_FILES[agent][0]}; read only that file. "
        "Follow your output contract exactly."
    )
    command = [
        str(config["opencode_executable"]),
        "run",
        prompt,
        "--agent",
        agent,
        "--model",
        str(config.get("agent_models", {}).get(agent, config["opencode_model"])),
        "--format",
        "json",
        "--title",
        f"CV Tailor {job_id}",
        "--auto",
    ]
    variant = config.get("agent_variants", {}).get(agent)
    if variant:
        command.extend(["--variant", str(variant)])
    for attachment in attachments or []:
        if attachment.exists():
            command.extend(["--file", str(attachment)])
    timeout = int(config.get("opencode_timeout_seconds", 1200))
    event("agent_start", agent=agent, model=command[command.index("--model") + 1], variant=variant)
    output = _run_process(command, job_dir / f"opencode-{agent}.log", timeout=timeout, agent=agent, root=root)
    write_json(job_dir / AGENT_FILES[agent][1], _parse_opencode_json(output))


def _run_process(command: list[str], log_path: Path, timeout: int, agent: str, root: Path) -> str:
    # OpenCode takes its project directory from $PWD when set, and cwd= does not
    # update it. An inherited PWD (e.g. from Git Bash) would point OpenCode at the
    # wrong directory, where the workspace agents do not exist.
    env = {**os.environ, "PWD": str(root)}
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        event("agent_timeout", logging.WARNING, agent=agent, seconds=timeout)
        raise RuntimeError(f"Process timed out after {timeout} seconds: {command[0]}") from error
    event(
        "agent_done",
        logging.INFO if completed.returncode == 0 else logging.WARNING,
        agent=agent,
        exit_code=completed.returncode,
        seconds=round(time.monotonic() - started, 1),
        output=log_path.relative_to(root),
    )
    log_path.write_text(
        f"COMMAND: {json.dumps(command, ensure_ascii=True)}\n"
        f"EXIT CODE: {completed.returncode}\n\nSTDOUT\n{completed.stdout}\n\nSTDERR\n{completed.stderr}",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        combined = completed.stdout + completed.stderr
        if "usage limit" in combined.lower() or '"statusCode":429' in combined:
            found = re.search(r"primary-reset-after-seconds\W+(\d+)", combined)
            raise RateLimited(int(found.group(1)) if found else 1800)
        raise RuntimeError(f"{command[0]} failed with exit code {completed.returncode}")
    return completed.stdout


def _parse_opencode_json(output: str) -> dict[str, Any]:
    """Return the last JSON object the agent printed, from OpenCode's --format json event stream."""
    candidates: list[str] = []
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "text":
            continue
        part = record.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            candidates.append(part["text"].strip())

    for candidate in reversed(candidates):
        cleaned = candidate
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("OpenCode did not return the required JSON object")
