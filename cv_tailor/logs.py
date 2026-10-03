"""Companion logging: one line per event, as key=value fields, tagged with the job being processed.

Lines look like::

    2026-10-03T18:10:05 level=INFO job=20261003-181000-daa27e0e event=agent_done agent=cv-tailor exit_code=0

Never log CV text, job descriptions or the user's answers: only identifiers, states and timings.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any

logger = logging.getLogger("cv_tailor")
logger.addHandler(logging.NullHandler())  # silent until configure(); a library must not print by default

# The job the current thread is working on; the worker sets it, so every line it logs carries the id.
current_job: contextvars.ContextVar[str] = contextvars.ContextVar("current_job", default="-")

FORMAT = "%(asctime)s level=%(levelname)s job=%(job)s %(message)s"
MAX_BYTES = 1_000_000
BACKUPS = 3


class _JobFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "job"):  # an explicit extra={"job": ...} wins
            record.job = current_job.get()
        return True


logger.addFilter(_JobFilter())


def _value(value: Any) -> str:
    text = "-" if value is None else str(value)
    if not text or any(char.isspace() or char in '"=' for char in text):
        return json.dumps(text, ensure_ascii=False)
    return text


def event(name: str, level: int = logging.INFO, job: str | None = None, **fields: Any) -> None:
    """Log one event as ``event=<name>`` followed by the fields, quoted where needed."""
    message = " ".join([f"event={name}", *(f"{key}={_value(value)}" for key, value in fields.items())])
    logger.log(level, message, extra={"job": job} if job else None)


def configure(log_file: Path | None) -> None:
    """Send logs to stderr and, when given, to a rotating file (1 MB x 4)."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(
            logging.handlers.RotatingFileHandler(log_file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        )
    formatter = logging.Formatter(FORMAT, "%Y-%m-%dT%H:%M:%S")
    for handler in handlers:
        handler.setFormatter(formatter)
    reset()
    for handler in handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def reset() -> None:
    """Close and remove configured handlers, returning to silence."""
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            handler.close()
