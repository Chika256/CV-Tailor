"""Command line entry point: ``cv-tailor init | serve | doctor | usage``."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .config import CONFIG_NAME, DEFAULTS, ConfigError, load_config, workspace_root
from .docx_io import extract_cv
from .render import RenderError, select_renderer
from .sample import write_sample_cv
from .usage import report, summarise

TEMPLATES = Path(__file__).resolve().parent / "templates"
DATA_DIRECTORIES = ("data/cv_library", "data/output", "data/runtime")


def _copy_template(source: Path, destination: Path, force: bool) -> bool:
    if destination.exists() and not force:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def cmd_init(args: argparse.Namespace) -> int:
    root = workspace_root(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    config_path = root / CONFIG_NAME
    if not config_path.exists() or args.force:
        config = {**DEFAULTS, "opencode_model": args.model or ""}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        written.append(CONFIG_NAME)
    for name in ("CV_TAILORING_AGENT.md", "opencode.json"):
        if _copy_template(TEMPLATES / name, root / name, args.force):
            written.append(name)
    for agent in sorted((TEMPLATES / "agents").glob("*.md")):
        if _copy_template(agent, root / ".opencode" / "agent" / agent.name, args.force):
            written.append(f".opencode/agent/{agent.name}")
    for directory in DATA_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    master = root / DEFAULTS["master_cv"]
    if args.sample and not master.exists():
        write_sample_cv(master)
        written.append(DEFAULTS["master_cv"] + " (fictional sample)")
    print(f"Workspace ready: {root}")
    for item in written:
        print(f"  wrote {item}")
    if not args.model:
        print(f"\nNext: set opencode_model in {CONFIG_NAME} (see `opencode models`).")
    if not master.exists():
        print(f"Next: put your master CV at {DEFAULTS['master_cv']} (or run init --sample to try the tool).")
    print("Then: cv-tailor doctor && cv-tailor serve")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run

    root = workspace_root(args.workspace)
    run(load_config(root), root)
    return 0


def _check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    mark = "ok  " if ok else ("warn" if warn_only else "FAIL")
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    return ok or warn_only


def cmd_doctor(args: argparse.Namespace) -> int:
    root = workspace_root(args.workspace)
    healthy = True
    healthy &= _check("Python 3.11 or newer", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        config = load_config(root)
    except ConfigError as error:
        _check("Configuration", False, str(error))
        return 1
    _check("Configuration", True, f"{root / CONFIG_NAME}, model {config['opencode_model']}")
    opencode = shutil.which(config["opencode_executable"])
    healthy &= _check("OpenCode on PATH", bool(opencode), opencode or "install OpenCode: https://opencode.ai")
    if opencode:
        listed = subprocess.run([opencode, "models"], capture_output=True, text=True, timeout=60, check=False)
        known = config["opencode_model"] in listed.stdout.split()
        _check("Model is available to OpenCode", known, "run `opencode auth login` or pick another model", warn_only=True)
    master = root / config["master_cv"]
    healthy &= _check("Master CV exists", master.is_file(), str(master))
    if master.is_file():
        try:
            paragraphs = extract_cv(master, "", None)["paragraphs"]
            editable = sum(1 for item in paragraphs if item["editable"])
            healthy &= _check("Master CV is readable", editable > 0, f"{len(paragraphs)} paragraphs, {editable} editable")
        except Exception as error:  # a malformed file is the user's to fix
            healthy &= _check("Master CV is readable", False, str(error))
    try:
        renderer = select_renderer(config["render_backend"])
        _check("Page renderer", renderer.name != "none", f"backend: {renderer.name}" + (
            "" if renderer.name != "none" else " (layout checks and PDF review are skipped; install LibreOffice or Word)"
        ), warn_only=True)
    except (RenderError, ValueError) as error:
        healthy &= _check("Page renderer", False, str(error))
    with socket.socket() as probe:
        free = probe.connect_ex((config["host"], config["port"])) != 0
    _check("Port is free", free, f"{config['host']}:{config['port']}" + ("" if free else " (already in use: is it running?)"),
           warn_only=True)
    return 0 if healthy else 1


def cmd_usage(args: argparse.Namespace) -> int:
    root = workspace_root(args.workspace)
    jobs_dir = root / load_config(root)["runtime_dir"] / "jobs"
    print(report(summarise(jobs_dir) if jobs_dir.is_dir() else []))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cv-tailor", description="Local companion for the CV Tailor browser extension")
    parser.add_argument("--version", action="version", version=f"cv-tailor {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a workspace (config, agents, data folders)")
    init.add_argument("directory", nargs="?", help="workspace directory (default: current directory)")
    init.add_argument("--model", help="OpenCode model, e.g. provider/model")
    init.add_argument("--sample", action="store_true", help="create a fictional sample master CV to try the tool")
    init.add_argument("--force", action="store_true", help="overwrite existing config and agent files")
    init.set_defaults(handler=cmd_init)

    serve = commands.add_parser("serve", help="run the local companion")
    serve.add_argument("--workspace", help="workspace directory (default: $CV_TAILOR_HOME or current directory)")
    serve.set_defaults(handler=cmd_serve)

    doctor = commands.add_parser("doctor", help="check that everything needed is in place")
    doctor.add_argument("--workspace", help="workspace directory (default: $CV_TAILOR_HOME or current directory)")
    doctor.set_defaults(handler=cmd_doctor)

    usage = commands.add_parser("usage", help="show the tokens each job's agent runs used")
    usage.add_argument("--workspace", help="workspace directory (default: $CV_TAILOR_HOME or current directory)")
    usage.set_defaults(handler=cmd_usage)

    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
