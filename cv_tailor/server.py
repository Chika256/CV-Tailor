from __future__ import annotations

import base64
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import docx_io
from .applicant import PROFILE_FIELDS, ProfileStore
from .docx_ops import (
    PlanError,
    read_json,
    sha256_file,
    validate_qa_result,
    validate_tailoring_result,
    write_json,
)
from .knowledge import KnowledgeBase
from .prep import (
    build_input,
    deterministic_layout_issues,
    fit_score,
    job_fingerprint,
    role_family,
    validate_letter,
    write_cover_letter_docx,
)
from .render import Renderer, RenderError, select_renderer

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9]+")
MAX_PAUSE_SECONDS = 6 * 3600
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


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TailoringCompanion:
    def __init__(self, config: dict[str, Any], root: Path) -> None:
        self.config = config
        self.root = root.resolve()
        self.master_cv = self._workspace_path(config["master_cv"])
        self.cv_library = self._workspace_path(config["cv_library_dir"])
        self.cv_library.mkdir(parents=True, exist_ok=True)
        self.renderer = self._select_renderer(config)
        self.output_dir = self._workspace_path(config["output_dir"])
        self.runtime_dir = self._workspace_path(config["runtime_dir"])
        for label, path in (("output_dir", self.output_dir), ("runtime_dir", self.runtime_dir)):
            if self.root not in path.parents:
                raise RuntimeError(f"{label} must remain inside the workspace")
        self.jobs_dir = self.runtime_dir / "jobs"
        self.state_path = self.runtime_dir / "companion_state.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        if not self.master_cv.is_file():
            raise RuntimeError(f"Master CV does not exist: {self.master_cv}")

        self.knowledge = KnowledgeBase(self.runtime_dir / "knowledge.db")
        self._import_existing_knowledge()
        self.state = self._load_or_create_state()
        self.work_queue: queue.Queue[str] = queue.Queue()
        self.queued_ids: set[str] = set()
        self.queue_lock = threading.Lock()
        self.profile = ProfileStore(self.runtime_dir / "profile.json", self.master_cv)
        self.paused_until = 0.0
        self.templates_dir = self.runtime_dir / "templates"
        self.templates_dir.mkdir(exist_ok=True)
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()
        self._recover_jobs()

    def _workspace_path(self, relative: str) -> Path:
        return (self.root / str(relative)).resolve()

    @property
    def token(self) -> str:
        return str(self.state["pairing_token"])

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = read_json(self.state_path)
            token = state.get("pairing_token")
            if isinstance(token, str) and len(token) >= 32:
                return state
        state = {"pairing_token": secrets.token_urlsafe(32), "created_at": utc_now()}
        write_json(self.state_path, state)
        return state

    def _recover_jobs(self) -> None:
        for status_path in self.jobs_dir.glob("*/status.json"):
            try:
                status = read_json(status_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if status.get("state") in {"queued", "running", "paused"}:
                self._update_status(status_path.parent.name, state="queued", message="Recovered after restart")
                self.enqueue(status_path.parent.name)

    def _import_existing_knowledge(self) -> None:
        """Seed the knowledge base from saved answers and other CVs; idempotent."""
        for answers_path in sorted(self.jobs_dir.glob("*/answers.json")):
            job_dir = answers_path.parent
            try:
                answers = read_json(answers_path).get("answers", "")
                job = read_json(job_dir / "job.json")
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(answers, str) and answers.strip():
                questions = _saved_questions(job_dir)
                self.knowledge.record_answers(
                    job_dir.name, job.get("company", ""), job.get("title", ""), questions, answers
                )
        self.knowledge.import_cvs(self.cv_library, exclude={self.master_cv}, require_cv_in_name=False)

    @staticmethod
    def _select_renderer(config: dict[str, Any]) -> Renderer:
        try:
            return select_renderer(str(config.get("render_backend", "auto")))
        except RenderError as error:
            raise RuntimeError(str(error)) from error

    def _candidate_slug(self) -> str:
        """Filename prefix from the configured name, else from the master CV's first line."""
        name = str(self.config.get("candidate_name", "")).strip()
        if not name:
            profile = self.profile.load()
            name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        return _filename_component(name) if name else "Candidate"

    def enqueue(self, job_id: str) -> None:
        with self.queue_lock:
            if job_id in self.queued_ids:
                return
            self.queued_ids.add(job_id)
            self.work_queue.put(job_id)

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._validate_job_payload(payload)
        normalized["fingerprint"] = job_fingerprint(normalized["url"], normalized["description"])
        if payload.get("force") is not True:
            existing = self._find_duplicate(normalized["fingerprint"])
            if existing:
                return {**self.get_job(existing), "duplicate": True}
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=False, exist_ok=False)
        normalized["job_id"] = job_id
        normalized["received_at"] = utc_now()
        write_json(job_dir / "job.json", normalized)
        self._update_status(job_id, state="queued", message="Waiting for the tailoring worker")
        self.enqueue(job_id)
        return self.get_job(job_id)

    def _find_duplicate(self, fingerprint: str) -> str | None:
        """Return an earlier, non-failed job for the same listing so it is never processed twice."""
        for status_path in sorted(self.jobs_dir.glob("*/status.json"), reverse=True):
            try:
                if read_json(status_path).get("state") == "failed":
                    continue
                if read_json(status_path.parent / "job.json").get("fingerprint") == fingerprint:
                    return status_path.parent.name
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    def proceed_low_fit(self, job_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        if read_json(job_dir / "status.json").get("state") != "needs_confirmation":
            raise ValueError("This job is not waiting for confirmation")
        self._update_status(job_id, state="queued", message="Confirmed; queued", confirmed=True)
        self.enqueue(job_id)
        return self.get_job(job_id)

    def request_cover_letter(self, job_id: str) -> dict[str, Any]:
        status = read_json(self._job_dir(job_id) / "status.json")
        if not str(status.get("state", "")).startswith("completed"):
            raise ValueError("A cover letter can be written only after the CV is complete")
        if status.get("letter_state") in {"queued", "running"}:
            return self.get_job(job_id)
        self._update_status(job_id, letter_state="queued", letter_message="Waiting for the worker")
        self.enqueue(f"letter:{job_id}")
        return self.get_job(job_id)

    def set_application_status(self, job_id: str, value: str) -> dict[str, Any]:
        self._job_dir(job_id)
        self.knowledge.set_application_status(job_id, value)
        return self.get_job(job_id)

    def job_file(self, job_id: str, what: str) -> dict[str, Any]:
        """Return a finished CV or cover letter as base64 so the extension can attach it to a form."""
        keys = {"cv": "output_path", "letter": "letter_path"}
        if what not in keys:
            raise ValueError("Unknown file")
        relative = read_json(self._job_dir(job_id) / "status.json").get(keys[what])
        if not relative:
            raise FileNotFoundError("That file has not been created yet")
        target = (self.root / relative).resolve()
        if self.root not in target.parents or not target.is_file() or target == self.master_cv:
            raise FileNotFoundError("That file is no longer available")
        return {"name": target.name, "base64": base64.b64encode(target.read_bytes()).decode("ascii")}

    def open_file(self, job_id: str, what: str) -> dict[str, Any]:
        keys = {"cv": "output_path", "report": "report_path", "preview": "preview_path", "letter": "letter_path"}
        if what not in keys:
            raise ValueError("Unknown file")
        relative = read_json(self._job_dir(job_id) / "status.json").get(keys[what])
        if not relative:
            raise FileNotFoundError("That file has not been created yet")
        target = (self.root / relative).resolve()
        if self.root not in target.parents or not target.is_file():
            raise FileNotFoundError("That file is no longer available")
        _open_with_default_app(target)
        return {"opened": target.name}

    def submit_answers(self, job_id: str, answers: object) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        status = read_json(job_dir / "status.json")
        if status.get("state") != "needs_clarification":
            raise ValueError("This job is not waiting for clarification")
        if not isinstance(answers, str) or not answers.strip() or len(answers) > 10000:
            raise ValueError("Answers must contain between 1 and 10,000 characters")
        write_json(job_dir / "answers.json", {"answers": answers.strip(), "submitted_at": utc_now()})
        result_path = job_dir / "result.json"
        questions = _questions_from(read_json(result_path)) if result_path.exists() else []
        if questions:
            write_json(job_dir / "questions.json", {"questions": questions})
        job = read_json(job_dir / "job.json")
        self.knowledge.record_answers(
            job_id, job.get("company", ""), job.get("title", ""), questions, answers.strip()
        )
        if result_path.exists():
            result_path.unlink()
        self._update_status(
            job_id, state="queued", message="Clarifications received; queued again", plan_done=False
        )
        self.enqueue(job_id)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job_dir = self._job_dir(job_id)
        status = read_json(job_dir / "status.json")
        job = read_json(job_dir / "job.json")
        response = {
            **status,
            "job_id": job_id,
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("url"),
            "application_status": self.knowledge.application_status(job_id),
        }
        result_path = job_dir / "result.json"
        if status.get("state") == "needs_clarification" and result_path.exists():
            response["questions"] = read_json(result_path).get("questions", [])
        return response

    def list_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        paths = sorted(self.jobs_dir.glob("*/status.json"), reverse=True)
        jobs: list[dict[str, Any]] = []
        for path in paths[: max(1, min(limit, 50))]:
            try:
                jobs.append(self.get_job(path.parent.name))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return jobs

    def _validate_job_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("The request body must be a JSON object")
        url = _required_string(payload, "url", 4000)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("The captured page URL is invalid")
        title = _required_string(payload, "title", 500)
        company = _required_string(payload, "company", 500)
        description = _required_string(payload, "description", 200000)
        minimum = int(self.config.get("minimum_description_characters", 500))
        if len(description) < minimum:
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
            "location": _optional_string(payload.get("location"), 500),
            "description": description,
            "source": _optional_string(payload.get("source"), 100) or parsed.netloc,
            "extraction": payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {},
        }

    def _job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", job_id):
            raise FileNotFoundError("Unknown job")
        job_dir = self.jobs_dir / job_id
        if not job_dir.is_dir():
            raise FileNotFoundError("Unknown job")
        return job_dir

    def _update_status(self, job_id: str, **updates: Any) -> None:
        job_dir = self.jobs_dir / job_id
        path = job_dir / "status.json"
        status = read_json(path) if path.exists() else {"created_at": utc_now()}
        status.update(updates)
        status["updated_at"] = utc_now()
        write_json(path, status)

    def _worker_loop(self) -> None:
        while True:
            job_id = self.work_queue.get()
            with self.queue_lock:
                self.queued_ids.discard(job_id)
            while time.time() < self.paused_until:
                time.sleep(5)
            is_letter = job_id.startswith("letter:")
            base_id = job_id.removeprefix("letter:")
            try:
                if is_letter:
                    self._process_letter(base_id)
                else:
                    self._process_job(job_id)
            except RateLimited as limited:
                self._pause_for_limit(job_id, base_id, is_letter, limited)
            except Exception as error:  # The worker must survive one bad application.
                job_dir = self.jobs_dir / base_id
                (job_dir / "companion-error.log").write_text(traceback.format_exc(), encoding="utf-8")
                if is_letter:
                    self._update_status(base_id, letter_state="failed", letter_message=str(error))
                else:
                    self._update_status(base_id, state="failed", message=str(error))
            finally:
                self.work_queue.task_done()

    def _pause_for_limit(self, job_id: str, base_id: str, is_letter: bool, limited: RateLimited) -> None:
        if limited.seconds > MAX_PAUSE_SECONDS:
            message = f"{limited}. Try again later."
            if is_letter:
                self._update_status(base_id, letter_state="failed", letter_message=message)
            else:
                self._update_status(base_id, state="failed", message=message)
            return
        wait = limited.seconds + 60
        self.paused_until = time.time() + wait
        resume = datetime.fromtimestamp(self.paused_until).strftime("%H:%M")
        message = f"Usage limit reached. Resuming automatically at {resume}."
        if is_letter:
            self._update_status(base_id, letter_state="queued", letter_message=message)
        else:
            self._update_status(base_id, state="paused", message=message)
        self.enqueue(job_id)

    def _process_job(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        status = read_json(job_dir / "status.json")
        self._update_status(job_id, state="running", stage="preflight", message="Protecting and reading the master CV")
        master_hash = sha256_file(self.master_cv)
        self._extract_cv(job_dir, master_hash)
        self._assert_master_unchanged(master_hash)
        cv_document = read_json(job_dir / "cv.json")
        job = read_json(job_dir / "job.json")
        result_path = job_dir / "result.json"

        if not self._passes_fit_gate(job_id, job, cv_document, status):
            return

        if status.get("plan_done") and result_path.exists():
            result = validate_tailoring_result(read_json(result_path), cv_document)
        else:
            if result_path.exists():
                result_path.unlink()
            knowledge = self.knowledge.snapshot(
                str(job["description"]), int(self.config.get("knowledge_cv_limit", 40))
            )
            write_json(job_dir / "knowledge.json", knowledge)
            answers_path = job_dir / "answers.json"
            answers = read_json(answers_path).get("answers") if answers_path.exists() else None
            template = self._load_template(job, master_hash)
            (job_dir / "input.md").write_text(
                build_input(job, cv_document, answers, knowledge, template), encoding="utf-8"
            )
            self._update_status(job_id, stage="tailoring", message="OpenCode is creating the truthful tailoring plan")
            self._run_opencode("cv-tailor", job_dir, job_id)
            self._assert_master_unchanged(master_hash)

            if not result_path.exists():
                raise RuntimeError("OpenCode did not create result.json")
            result = validate_tailoring_result(read_json(result_path), cv_document)
            if result["status"] == "needs_clarification":
                self._update_status(
                    job_id,
                    state="needs_clarification",
                    stage="clarification",
                    message="Truthful tailoring requires additional information",
                )
                return
            self._update_status(job_id, plan_done=True)

        output_path = self._output_path(job, job_id)
        shutil.copy2(self.master_cv, output_path)
        self._update_status(job_id, stage="document", message="Applying the plan to a protected copy")
        self._apply_and_render(job_dir, output_path)
        self._assert_master_unchanged(master_hash)

        qa_state = "completed"
        qa_message = "Tailored CV created and inspected"
        if self.renderer.name == "none":
            qa_message = "Tailored CV created. Layout was not checked because no page renderer is installed."
        elif self.config.get("qa_enabled", True):
            self._update_status(job_id, stage="qa", message="Checking the rendered layout")
            try:
                qa = self._inspect_layout(job_dir, job_id, cv_document)
                attempts = int(self.config.get("qa_revision_attempts", 0))
                for attempt in range(attempts):
                    if qa.get("application_ready") is True:
                        break
                    self._update_status(
                        job_id,
                        stage="revision",
                        message=f"Automatically revising layout after visual QA ({attempt + 1}/{attempts})",
                    )
                    revised_path = job_dir / "revised-result.json"
                    if revised_path.exists():
                        revised_path.unlink()
                    knowledge = read_json(job_dir / "knowledge.json")
                    extra = (
                        "## Layout problems to fix\n"
                        + "\n".join(f"- {issue}" for issue in qa.get("issues", []))
                        + "\n\n## Current plan (return a full corrected plan)\n"
                        + json.dumps(result.get("replacements", []), ensure_ascii=False, separators=(",", ":"))
                    )
                    answers_path = job_dir / "answers.json"
                    answers = read_json(answers_path).get("answers") if answers_path.exists() else None
                    (job_dir / "revise_input.md").write_text(
                        build_input(job, cv_document, answers, knowledge, None, extra), encoding="utf-8"
                    )
                    self._run_opencode(
                        "cv-tailor-revise", job_dir, job_id, attachments=[job_dir / "preview.pdf"]
                    )
                    result = validate_tailoring_result(read_json(revised_path), cv_document)
                    if result.get("status") != "ready":
                        raise RuntimeError("The layout revision agent did not return a ready plan")
                    write_json(result_path, result)
                    shutil.copy2(self.master_cv, output_path)
                    self._apply_and_render(job_dir, output_path)
                    self._assert_master_unchanged(master_hash)
                    self._update_status(job_id, stage="qa", message="Checking the revised layout")
                    qa = self._inspect_layout(job_dir, job_id, cv_document)

                layout = read_json(job_dir / "layout.json")
                known = layout.get("page_count") is not None and cv_document.get("page_count") is not None
                if known and layout.get("page_count") != cv_document.get("page_count"):
                    qa_state = "completed_with_warning"
                    qa_message = "Tailored CV created, but its page count changed"
                if qa.get("application_ready") is not True:
                    qa_state = "completed_with_warning"
                    qa_message = "Tailored CV created, but visual QA requires review"
            except RateLimited:
                raise
            except Exception as error:
                qa_state = "completed_with_warning"
                qa_message = f"Tailored CV created, but automated visual QA failed: {error}"

        if not result.get("replacements") and qa_state == "completed":
            qa_message = "No changes recommended: your CV already fits this listing. A copy was saved for it."
        report_path = job_dir / "application_report.md"
        self._write_report(report_path, job, result, job_dir)
        self._assert_master_unchanged(master_hash)
        family = role_family(str(job.get("title", "")))
        # An empty plan says nothing reusable; keep the previous plan for this role family instead.
        if result.get("replacements"):
            self._save_template(family, job, result, master_hash)
        self.knowledge.log_application(
            job_id,
            str(job.get("company", "")),
            str(job.get("title", "")),
            str(job.get("url", "")),
            family,
            read_json(job_dir / "status.json").get("fit_score"),
            output_path.name,
        )
        self._update_status(
            job_id,
            state=qa_state,
            stage="complete",
            message=qa_message,
            output_path=str(output_path.relative_to(self.root)),
            preview_path=str((job_dir / "preview.pdf").relative_to(self.root)) if (job_dir / "preview.pdf").exists() else None,
            report_path=str(report_path.relative_to(self.root)),
            master_sha256=master_hash,
            family=family,
        )

    def _passes_fit_gate(
        self, job_id: str, job: dict[str, Any], cv_document: dict[str, Any], status: dict[str, Any]
    ) -> bool:
        """Score the listing locally, for free. A low score pauses the job until the user confirms."""
        if status.get("fit_checked"):
            return True
        evidence = " ".join(str(p.get("text", "")) for p in cv_document["paragraphs"])
        everything = self.knowledge.snapshot(str(job["description"]), 100000)
        evidence += " " + json.dumps(everything, ensure_ascii=False)
        fit = fit_score(
            str(job["description"]), evidence,
            ignore=f"{job.get('company', '')} {job.get('title', '')}", title=str(job.get("title", "")),
        )
        self._update_status(job_id, fit_checked=True, fit_score=fit["score"], missing_keywords=fit["missing"])
        threshold = int(self.config.get("min_fit_score", 0))
        if threshold and fit["score"] < threshold and not status.get("confirmed"):
            self._update_status(
                job_id,
                state="needs_confirmation",
                stage="fit",
                message=f"Low keyword fit ({fit['score']}%). Not covered: {', '.join(fit['missing'][:6]) or 'n/a'}",
            )
            return False
        return True

    def _inspect_layout(self, job_dir: Path, job_id: str, cv_document: dict[str, Any]) -> dict[str, Any]:
        """Free structural checks first; the AI inspects the PDF only when they find a problem."""
        layout = read_json(job_dir / "layout.json")
        expected = int(cv_document.get("page_count", 0))
        issues = deterministic_layout_issues(layout, expected)
        if not issues and self.config.get("ai_qa_mode", "on_failure") != "always":
            qa = {
                "schema_version": 1,
                "pages_inspected": layout.get("page_count"),
                "application_ready": True,
                "issues": [],
                "notes": ["Passed the automatic layout checks; AI page inspection was not needed."],
            }
            write_json(job_dir / "qa.json", qa)
            return qa
        (job_dir / "qa_input.md").write_text(
            "# QA INPUT\nThe rendered `preview.pdf` is attached. Inspect every page.\n\n"
            f"Pages expected: {expected}. Pages rendered: {layout.get('page_count')}.\n"
            "Automatic checks flagged:\n"
            + ("\n".join(f"- {issue}" for issue in issues) or "- nothing (full inspection requested)")
            + "\n\nDecide whether each flagged issue is a real defect that would make the CV unsuitable "
            "to submit, and look for any other problem listed in your instructions.\n",
            encoding="utf-8",
        )
        return self._run_visual_qa(job_dir, job_id)

    def _load_template(self, job: dict[str, Any], master_hash: str) -> dict[str, Any] | None:
        if not self.config.get("use_templates", True):
            return None
        path = self.templates_dir / f"{role_family(str(job.get('title', '')))}.json"
        if not path.exists():
            return None
        template = read_json(path)
        return template if template.get("master_sha256") == master_hash else None

    def _save_template(self, family: str, job: dict[str, Any], result: dict[str, Any], master_hash: str) -> None:
        write_json(
            self.templates_dir / f"{family}.json",
            {
                "family": family,
                "company": job.get("company"),
                "title": job.get("title"),
                "master_sha256": master_hash,
                "replacements": [
                    {"paragraph_id": r["paragraph_id"], "new_text": r["new_text"]}
                    for r in result.get("replacements", [])
                ],
            },
        )

    def _process_letter(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        job = read_json(job_dir / "job.json")
        cv_document = read_json(job_dir / "cv.json")
        result = read_json(job_dir / "result.json")
        self._update_status(job_id, letter_state="running", letter_message="OpenCode is drafting the cover letter")
        knowledge = self.knowledge.snapshot(str(job["description"]), int(self.config.get("knowledge_cv_limit", 40)))
        answers_path = job_dir / "answers.json"
        answers = read_json(answers_path).get("answers") if answers_path.exists() else None
        extra = (
            "## What the tailored CV emphasises\n"
            + "\n".join(f"- {item}" for item in result.get("change_summary", []))
            + "\n\n## Requirements that are NOT supported (never claim these)\n"
            + "\n".join(f"- {item}" for item in result.get("unsupported_requirements", []))
        )
        (job_dir / "letter_input.md").write_text(
            build_input(job, cv_document, answers, knowledge, None, extra), encoding="utf-8"
        )
        self._run_opencode("cv-tailor-letter", job_dir, job_id)
        try:
            letter = validate_letter(read_json(job_dir / "letter.json"))
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        header = [p["text"] for p in cv_document["paragraphs"][:2] if p.get("text")]
        company = _filename_component(str(job.get("company") or "Company"))
        title = _filename_component(str(job.get("title") or "Role"))
        target = self.output_dir / f"{self._candidate_slug()}_{company}_{title}_Cover_Letter.docx"
        write_cover_letter_docx(target, header, datetime.now().strftime("%d %B %Y"), letter)
        self._update_status(
            job_id,
            letter_state="completed",
            letter_message="Cover letter created",
            letter_path=str(target.relative_to(self.root)),
        )

    def _run_visual_qa(self, job_dir: Path, job_id: str) -> dict[str, Any]:
        qa_path = job_dir / "qa.json"
        if qa_path.exists():
            qa_path.unlink()
        self._run_opencode("cv-tailor-qa", job_dir, job_id, attachments=[job_dir / "preview.pdf"])
        return validate_qa_result(read_json(qa_path), read_json(job_dir / "layout.json"))

    def _extract_cv(self, job_dir: Path, master_hash: str) -> None:
        page_count = self._master_page_count(master_hash)
        write_json(job_dir / "cv.json", docx_io.extract_cv(self.master_cv, master_hash, page_count))

    def _master_page_count(self, master_hash: str) -> int | None:
        """Page count of the untouched master, cached per file hash and renderer."""
        cache = self.runtime_dir / "master_layout.json"
        if cache.exists():
            cached = read_json(cache)
            if cached.get("sha256") == master_hash and cached.get("backend") == self.renderer.name:
                return cached.get("page_count")
        try:
            layout = self.renderer.render(self.master_cv, None)
        except RenderError:
            return None
        write_json(cache, {"sha256": master_hash, "backend": layout.backend, "page_count": layout.page_count})
        return layout.page_count

    def _apply_and_render(self, job_dir: Path, output_path: Path) -> None:
        plan = read_json(job_dir / "result.json")
        docx_io.apply_plan(self.master_cv, output_path, plan["replacements"])
        pdf_path = job_dir / "preview.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
        try:
            layout = self.renderer.render(output_path, pdf_path if self.renderer.name != "none" else None)
        except RenderError as error:
            raise RuntimeError(str(error)) from error
        write_json(job_dir / "layout.json", layout.to_json())

    def _run_opencode(
        self,
        agent: str,
        job_dir: Path,
        job_id: str,
        attachments: list[Path] | None = None,
    ) -> None:
        relative_job_dir = job_dir.relative_to(self.root)
        prompt = (
            f"Process CV tailoring job {job_id}. Your complete input is the single file "
            f"{relative_job_dir / AGENT_FILES[agent][0]}; read only that file. "
            "Follow your output contract exactly."
        )
        command = [
            str(self.config["opencode_executable"]),
            "run",
            prompt,
            "--agent",
            agent,
            "--model",
            str(self.config.get("agent_models", {}).get(agent, self.config["opencode_model"])),
            "--format",
            "json",
            "--title",
            f"CV Tailor {job_id}",
            "--auto",
        ]
        variant = self.config.get("agent_variants", {}).get(agent)
        if variant:
            command.extend(["--variant", str(variant)])
        for attachment in attachments or []:
            if attachment.exists():
                command.extend(["--file", str(attachment)])
        timeout = int(self.config.get("opencode_timeout_seconds", 1200))
        output = self._run_process(command, job_dir / f"opencode-{agent}.log", timeout=timeout)
        parsed = _parse_opencode_json(output)
        if agent not in AGENT_FILES:
            raise RuntimeError(f"Unsupported OpenCode agent: {agent}")
        output_name = AGENT_FILES[agent][1]
        write_json(job_dir / output_name, parsed)

    def _run_process(self, command: list[str], log_path: Path, timeout: int) -> str:
        # OpenCode takes its project directory from $PWD when set, and cwd= does not
        # update it. An inherited PWD (e.g. from Git Bash) would point OpenCode at the
        # wrong directory, where the workspace agents do not exist.
        env = {**os.environ, "PWD": str(self.root)}
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
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
            raise RuntimeError(f"Process timed out after {timeout} seconds: {command[0]}") from error
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

    def _assert_master_unchanged(self, expected_hash: str) -> None:
        actual_hash = sha256_file(self.master_cv)
        if not hmac.compare_digest(expected_hash, actual_hash):
            raise RuntimeError("Safety stop: the protected master CV changed during processing")

    def _output_path(self, job: dict[str, Any], job_id: str) -> Path:
        company = _filename_component(str(job.get("company") or "Company"))
        title = _filename_component(str(job.get("title") or "Target_Role"))
        filename = f"{self._candidate_slug()}_{company}_{title}_CV.docx"
        candidate = (self.output_dir / filename).resolve()
        if candidate.parent != self.output_dir:
            raise RuntimeError("Unsafe output filename")
        if candidate.exists():
            candidate = self.output_dir / f"{self._candidate_slug()}_{company}_{title}_CV_{job_id[-8:]}.docx"
        if candidate == self.master_cv:
            raise RuntimeError("Safety stop: output path equals the master CV path")
        return candidate

    def _write_report(
        self,
        path: Path,
        job: dict[str, Any],
        result: dict[str, Any],
        job_dir: Path,
    ) -> None:
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


class CompanionServer(ThreadingHTTPServer):
    """HTTP server that carries the companion, so request handlers can reach it."""

    def __init__(self, address: tuple[str, int], app: TailoringCompanion) -> None:
        super().__init__(address, CompanionHandler)
        self.app = app


class CompanionHandler(BaseHTTPRequestHandler):
    server_version = "CVTailorCompanion/1.0"

    @property
    def app(self) -> TailoringCompanion:
        return cast(CompanionServer, self.server).app

    def do_OPTIONS(self) -> None:
        if not self._extension_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/health":
                self._send_json({"status": "ok", "version": 1})
                return
            if not self._authorized():
                self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            if path == "/jobs":
                self._send_json({"jobs": self.app.list_jobs()})
                return
            match = re.fullmatch(r"/jobs/([^/]+)", path)
            if match:
                self._send_json(self.app.get_job(match.group(1)))
                return
            if path == "/profile":
                self._send_json({"profile": self.app.profile.load(), "fields": PROFILE_FIELDS})
                return
            match = re.fullmatch(r"/jobs/([^/]+)/file", path)
            if match:
                what = parse_qs(urlparse(self.path).query).get("what", [""])[0]
                self._send_json(self.app.job_file(match.group(1), what))
                return
            if path == "/applications":
                self._send_json(self.app.knowledge.applications_summary())
                return
            if path == "/knowledge":
                query = parse_qs(urlparse(self.path).query).get("q", [""])[0][:200]
                self._send_json({"items": self.app.knowledge.search(query)})
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except FileNotFoundError as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/pair":
                if not self._extension_origin():
                    self._send_json({"error": "Pairing is available only to a Chrome extension"}, HTTPStatus.FORBIDDEN)
                    return
                self._send_json({"token": self.app.token})
                return
            if not self._authorized():
                self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            body = self._read_body()
            if path == "/jobs":
                self._send_json(self.app.create_job(body), HTTPStatus.ACCEPTED)
                return
            if path == "/profile":
                values = body.get("values")
                if not isinstance(values, dict):
                    raise ValueError("values must be an object")
                self._send_json({"profile": self.app.profile.save(values)})
                return
            if path == "/knowledge/notes":
                new_id = self.app.knowledge.add(
                    "note", "added in extension", _required_string(body, "text", 4000),
                    _optional_string(body.get("topic"), 200),
                )
                self._send_json({"id": new_id}, HTTPStatus.CREATED)
                return
            if path == "/knowledge/import-cvs":
                self._send_json(
                    self.app.knowledge.import_cvs(self.app.cv_library, exclude={self.app.master_cv}, require_cv_in_name=False)
                )
                return
            if path == "/knowledge/import-chat":
                added = self.app.knowledge.import_chat_export(
                    _optional_string(body.get("label"), 100) or "unnamed",
                    _required_string(body, "text", 1_500_000),
                )
                self._send_json({"added": added})
                return
            match = re.fullmatch(r"/knowledge/(\d+)/(retire|correct)", path)
            if match:
                record_id = int(match.group(1))
                if match.group(2) == "retire":
                    if not self.app.knowledge.retire(record_id):
                        raise FileNotFoundError("No active knowledge record with that id")
                    self._send_json({"retired": record_id})
                else:
                    new_id = self.app.knowledge.correct(record_id, _required_string(body, "text", 4000))
                    self._send_json({"id": new_id})
                return
            match = re.fullmatch(r"/jobs/([^/]+)/(proceed|cover-letter|application|open)", path)
            if match:
                job_id, action = match.groups()
                if action == "proceed":
                    self._send_json(self.app.proceed_low_fit(job_id), HTTPStatus.ACCEPTED)
                elif action == "cover-letter":
                    self._send_json(self.app.request_cover_letter(job_id), HTTPStatus.ACCEPTED)
                elif action == "application":
                    self._send_json(self.app.set_application_status(job_id, str(body.get("status", ""))))
                else:
                    self._send_json(self.app.open_file(job_id, str(body.get("what", ""))))
                return
            match = re.fullmatch(r"/jobs/([^/]+)/answers", path)
            if match:
                self._send_json(
                    self.app.submit_answers(match.group(1), body.get("answers")),
                    HTTPStatus.ACCEPTED,
                )
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, PlanError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > 2_000_000:
            raise ValueError("Request body must contain no more than 2 MB")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _authorized(self) -> bool:
        # Chrome's privileged extension GET requests may omit Origin. The
        # bearer token authenticates job requests; reject an explicit website
        # origin, but do not confuse a missing header with an invalid token.
        origin = self.headers.get("Origin", "")
        if origin and not self._extension_origin():
            return False
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.app.token}"
        return hmac.compare_digest(authorization, expected)

    def _extension_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return origin.startswith("chrome-extension://") and len(origin) < 200

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if self._extension_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: Any) -> None:
        return


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


def _required_string(payload: dict[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required field: {key}")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"Field is too long: {key}")
    return value


def _optional_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def _filename_component(value: str) -> str:
    cleaned = SAFE_FILENAME.sub("_", value.strip()).strip("_")
    return cleaned[:80] or "Unknown"


def _parse_opencode_json(output: str) -> dict[str, Any]:
    candidates: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part")
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


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run(config: dict[str, Any], root: Path) -> None:
    app = TailoringCompanion(config, root)
    server = CompanionServer((config["host"], int(config["port"])), app)
    print(f"CV Tailor companion listening at http://{config['host']}:{config['port']}")
    print(f"Workspace: {app.root}   Page renderer: {app.renderer.name}")
    print("Open the extension once to pair it with this local companion. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
