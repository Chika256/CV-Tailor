"""The tailoring companion: the job queue, its worker, and the pipeline that tailors each CV."""

from __future__ import annotations

import base64
import hmac
import json
import logging
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
from pathlib import Path
from typing import Any

from . import docx_io
from .agents import RateLimited, run_agent
from .applicant import ProfileStore
from .docx_ops import (
    read_json,
    sha256_file,
    validate_qa_result,
    validate_tailoring_result,
    write_json,
)
from .intake import validate_job_payload
from .knowledge import KnowledgeBase
from .logs import current_job, event
from .prep import (
    build_input,
    deterministic_layout_issues,
    fit_score,
    job_fingerprint,
    over_page_limit,
    page_target,
    role_family,
    validate_letter,
    write_cover_letter_docx,
)
from .render import Renderer, RenderError, select_renderer
from .reports import _questions_from, _saved_questions, write_report

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9]+")
MAX_PAUSE_SECONDS = 6 * 3600


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TailoringCompanion:
    PAUSE_CHECK_SECONDS = 5.0  # how often a paused worker checks whether the usage limit has reset

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
        normalized = validate_job_payload(payload, int(self.config.get("minimum_description_characters", 500)))
        normalized["fingerprint"] = job_fingerprint(normalized["url"], normalized["description"])
        if payload.get("force") is not True:
            existing = self._find_duplicate(normalized["fingerprint"])
            if existing:
                event("job_duplicate", job=existing)
                return {**self.get_job(existing), "duplicate": True}
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=False, exist_ok=False)
        normalized["job_id"] = job_id
        normalized["received_at"] = utc_now()
        write_json(job_dir / "job.json", normalized)
        event("job_received", job=job_id, source=normalized["source"], characters=len(normalized["description"]))
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
        changed = {key for key in ("state", "stage", "letter_state") if key in updates and updates[key] != status.get(key)}
        status.update(updates)
        status["updated_at"] = utc_now()
        write_json(path, status)
        # Every job's lifecycle passes through here, so this one hook traces it in the log.
        if "state" in changed or "stage" in changed:
            state = status.get("state")
            level = logging.WARNING if state in {"failed", "completed_with_warning"} else logging.INFO
            event("status", level, job=job_id, state=state, stage=status.get("stage"), message=status.get("message"))
        if "letter_state" in changed:
            state = status.get("letter_state")
            level = logging.WARNING if state == "failed" else logging.INFO
            event("letter_status", level, job=job_id, state=state, message=status.get("letter_message"))

    def _worker_loop(self) -> None:
        while True:
            job_id = self.work_queue.get()
            with self.queue_lock:
                self.queued_ids.discard(job_id)
            while time.time() < self.paused_until:
                time.sleep(self.PAUSE_CHECK_SECONDS)
            is_letter = job_id.startswith("letter:")
            base_id = job_id.removeprefix("letter:")
            context = current_job.set(base_id)
            try:
                if is_letter:
                    self._process_letter(base_id)
                else:
                    self._process_job(job_id)
            except RateLimited as limited:
                self._pause_for_limit(job_id, base_id, is_letter, limited)
            except Exception as error:  # The worker must survive one bad application.
                job_dir = self.jobs_dir / base_id
                trace = job_dir / "companion-error.log"
                trace.write_text(traceback.format_exc(), encoding="utf-8")
                event("job_error", logging.ERROR, error=type(error).__name__, traceback=trace.relative_to(self.root))
                if is_letter:
                    self._update_status(base_id, letter_state="failed", letter_message=str(error))
                else:
                    self._update_status(base_id, state="failed", message=str(error))
            finally:
                current_job.reset(context)
                self.work_queue.task_done()

    def _pause_for_limit(self, job_id: str, base_id: str, is_letter: bool, limited: RateLimited) -> None:
        event("usage_limit", logging.WARNING, reset_seconds=limited.seconds, pause=limited.seconds <= MAX_PAUSE_SECONDS)
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
                build_input(job, cv_document, answers, knowledge, template,
                            max_pages=int(self.config.get("max_pages", 0))),
                encoding="utf-8",
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
                        + "\n\n## Current plan (return a full corrected plan; one replacement per line)\n"
                        + "\n".join(json.dumps(item, ensure_ascii=False) for item in result.get("replacements", []))
                    )
                    answers_path = job_dir / "answers.json"
                    answers = read_json(answers_path).get("answers") if answers_path.exists() else None
                    (job_dir / "revise_input.md").write_text(
                        build_input(job, cv_document, answers, knowledge, None, extra,
                                    max_pages=int(self.config.get("max_pages", 0))),
                        encoding="utf-8",
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
                max_pages = int(self.config.get("max_pages", 0))
                known = layout.get("page_count") is not None and cv_document.get("page_count") is not None
                if known and layout.get("page_count") != page_target(int(cv_document["page_count"]), max_pages):
                    qa_state = "completed_with_warning"
                    qa_message = "Tailored CV created, but its page count changed"
                if qa.get("application_ready") is not True:
                    qa_state = "completed_with_warning"
                    qa_message = "Tailored CV created, but visual QA requires review"
                # Last, so the most specific reason wins: the revisions could not bring it within the limit.
                if over_page_limit(layout, max_pages):
                    qa_state = "completed_with_warning"
                    qa_message = f"Tailored CV created, but it is over the {max_pages}-page limit"
            except RateLimited:
                raise
            except Exception as error:
                qa_state = "completed_with_warning"
                qa_message = f"Tailored CV created, but automated visual QA failed: {error}"

        if not result.get("replacements") and qa_state == "completed":
            qa_message = "No changes recommended: your CV already fits this listing. A copy was saved for it."
        report_path = job_dir / "application_report.md"
        write_report(report_path, job, result, job_dir)
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
        max_pages = int(self.config.get("max_pages", 0))
        master_pages = int(cv_document.get("page_count") or 0)
        expected = page_target(master_pages, max_pages)
        issues = deterministic_layout_issues(layout, master_pages, max_pages)
        if over_page_limit(layout, max_pages):
            # The page count alone settles it: go straight to revision without paying for an AI inspection.
            qa = {
                "schema_version": 1,
                "pages_inspected": layout.get("page_count"),
                "application_ready": False,
                "issues": issues,
                "notes": [f"Over the {max_pages}-page limit; sent for revision without AI page inspection."],
            }
            write_json(job_dir / "qa.json", qa)
            return qa
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
                    {"paragraph_id": r["paragraph_id"], "new_text": r.get("new_text", "(removed)")}
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
        company, title = _filename_parts(job, "Role")
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
        self, agent: str, job_dir: Path, job_id: str, attachments: list[Path] | None = None
    ) -> None:
        run_agent(agent, job_dir, job_id, root=self.root, config=self.config, attachments=attachments)

    def _assert_master_unchanged(self, expected_hash: str) -> None:
        actual_hash = sha256_file(self.master_cv)
        if not hmac.compare_digest(expected_hash, actual_hash):
            raise RuntimeError("Safety stop: the protected master CV changed during processing")

    def _output_path(self, job: dict[str, Any], job_id: str) -> Path:
        company, title = _filename_parts(job, "Target_Role")
        filename = f"{self._candidate_slug()}_{company}_{title}_CV.docx"
        candidate = (self.output_dir / filename).resolve()
        if candidate.parent != self.output_dir:
            raise RuntimeError("Unsafe output filename")
        if candidate.exists():
            candidate = self.output_dir / f"{self._candidate_slug()}_{company}_{title}_CV_{job_id[-8:]}.docx"
        if candidate == self.master_cv:
            raise RuntimeError("Safety stop: output path equals the master CV path")
        return candidate


def _filename_component(value: str) -> str:
    cleaned = SAFE_FILENAME.sub("_", value.strip()).strip("_")
    return cleaned[:80] or "Unknown"


def _filename_parts(job: dict[str, Any], default_title: str) -> tuple[str, str]:
    """The company and title parts of an output filename, with the company left out of the title."""
    company = str(job.get("company") or "")
    title = _title_without_company(str(job.get("title") or default_title), company)
    return _filename_component(company or "Company"), _filename_component(title)


def _title_without_company(title: str, company: str) -> str:
    """Drop the company that listing titles often repeat ("Role - Company", "Role at Company")."""
    words = [word for word in SAFE_FILENAME.split(title) if word]
    company_words = [word.lower() for word in SAFE_FILENAME.split(company) if word]
    if not company_words:
        return title
    size = len(company_words)
    kept: list[str] = []
    index = 0
    while index < len(words):
        if [word.lower() for word in words[index : index + size]] == company_words:
            if kept and kept[-1].lower() == "at":
                kept.pop()
            index += size
        else:
            kept.append(words[index])
            index += 1
    # A title that is only the company stays as it is rather than becoming empty.
    return "_".join(kept) if kept else title


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
