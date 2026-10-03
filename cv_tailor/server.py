"""Entry point for `cv-tailor serve`: wire up logging, the companion and its HTTP API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__, logs
from .api import CompanionServer
from .companion import TailoringCompanion
from .logs import event


def run(config: dict[str, Any], root: Path) -> None:
    # Configure before the companion starts, so jobs recovered after a restart are logged too.
    runtime_dir = (root.resolve() / str(config["runtime_dir"])).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs.configure(runtime_dir / "companion.log")
    app = TailoringCompanion(config, root)
    server = CompanionServer((config["host"], int(config["port"])), app)
    event("listening", host=config["host"], port=config["port"], version=__version__, renderer=app.renderer.name)
    print(f"CV Tailor companion listening at http://{config['host']}:{config['port']}")
    print(f"Workspace: {app.root}   Page renderer: {app.renderer.name}")
    print("Open the extension once to pair it with this local companion. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
