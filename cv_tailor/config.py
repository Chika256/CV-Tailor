"""Workspace discovery and configuration with validation and clear errors."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_NAME = "cv-tailor.json"
WORKSPACE_ENV = "CV_TAILOR_HOME"

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8765,
    "master_cv": "data/master_cv.docx",
    "output_dir": "data/output",
    "runtime_dir": "data/runtime",
    "cv_library_dir": "data/cv_library",
    "candidate_name": "",
    "opencode_executable": "opencode",
    "opencode_model": "",
    "agent_models": {},
    "agent_variants": {},
    "render_backend": "auto",
    "minimum_description_characters": 500,
    "opencode_timeout_seconds": 1200,
    "qa_enabled": True,
    "qa_revision_attempts": 1,
    "ai_qa_mode": "on_failure",
    "knowledge_cv_limit": 40,
    "min_fit_score": 20,
    "use_templates": True,
}
AGENTS = ("cv-tailor", "cv-tailor-revise", "cv-tailor-qa", "cv-tailor-letter")


class ConfigError(RuntimeError):
    """A problem the user can fix by editing cv-tailor.json."""


def workspace_root(explicit: str | Path | None = None) -> Path:
    """The workspace is --workspace, else $CV_TAILOR_HOME, else the current directory."""
    chosen = explicit or os.environ.get(WORKSPACE_ENV) or Path.cwd()
    return Path(chosen).expanduser().resolve()


def load_config(root: Path) -> dict[str, Any]:
    path = root / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(
            f"No {CONFIG_NAME} in {root}. Run `cv-tailor init` there first, or point to a workspace "
            f"with --workspace or the {WORKSPACE_ENV} environment variable."
        )
    try:
        user = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(user, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    unknown = sorted(set(user) - set(DEFAULTS))
    if unknown:
        raise ConfigError(f"Unknown setting(s) in {CONFIG_NAME}: {', '.join(unknown)}")
    config = {**DEFAULTS, **user}
    validate(config, root)
    return config


def validate(config: dict[str, Any], root: Path) -> None:
    if config["host"] not in {"127.0.0.1", "localhost"}:
        raise ConfigError("host must be a loopback address (127.0.0.1 or localhost); the companion is never exposed")
    if not isinstance(config["port"], int) or not 1024 <= config["port"] <= 65535:
        raise ConfigError("port must be an integer between 1024 and 65535")
    if not str(config["opencode_model"]).strip():
        raise ConfigError(
            f"opencode_model is not set in {CONFIG_NAME}. Choose a model in provider/model form "
            "(list the ones you can use with `opencode models`)."
        )
    if "/" not in str(config["opencode_model"]):
        raise ConfigError("opencode_model must look like provider/model; `opencode models` lists the ones you can use")
    if config["render_backend"] not in {"auto", "word", "libreoffice", "none"}:
        raise ConfigError("render_backend must be one of: auto, word, libreoffice, none")
    if config["ai_qa_mode"] not in {"on_failure", "always"}:
        raise ConfigError("ai_qa_mode must be on_failure or always")
    for key in ("agent_models", "agent_variants"):
        value = config[key]
        if not isinstance(value, dict) or any(name not in AGENTS or not isinstance(v, str) for name, v in value.items()):
            raise ConfigError(f"{key} must map agent names ({', '.join(AGENTS)}) to strings")
    for key in ("minimum_description_characters", "opencode_timeout_seconds", "qa_revision_attempts",
                "knowledge_cv_limit", "min_fit_score"):
        if not isinstance(config[key], int) or config[key] < 0:
            raise ConfigError(f"{key} must be a non-negative integer")
    if config["min_fit_score"] > 100:
        raise ConfigError("min_fit_score must be between 0 and 100")
    for key in ("qa_enabled", "use_templates"):
        if not isinstance(config[key], bool):
            raise ConfigError(f"{key} must be true or false")
    # Compare resolved paths on both sides: a workspace reached through a symlink (macOS's
    # /var -> /private/var, for one) must not look like it contains none of its own folders.
    root = root.resolve()
    for key in ("master_cv", "output_dir", "runtime_dir", "cv_library_dir"):
        resolved = (root / config[key]).resolve()
        if root != resolved and root not in resolved.parents:
            raise ConfigError(f"{key} must stay inside the workspace so agents can only read job files")
