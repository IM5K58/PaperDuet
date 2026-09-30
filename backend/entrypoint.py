"""Sidecar boot protocol. stdout is one JSON readiness line; never log secrets."""
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

from paperduet.app import create_app


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Tauri assigns the process to a kill-on-close Windows Job BEFORE releasing
    # this gate. No server/workers/descendants may start before that assignment.
    if os.environ.pop("PAPERDUET_START_GATE", "") == "1":
        if sys.stdin.readline().strip() != "start":
            return
    token = os.environ.pop("PAPERDUET_SESSION_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Missing session authorization")
    origin = os.environ.pop("PAPERDUET_ORIGIN", "http://tauri.localhost")
    allowed = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost", "http://127.0.0.1:1420"}
    if origin not in allowed:
        raise RuntimeError("Unsupported app origin")
    data_dir = Path(os.environ.pop("PAPERDUET_DATA_DIR", str(Path(os.environ.get("APPDATA", Path.home())) / "PaperDuet")))
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    app = create_app(token, data_dir, root / "fixtures" / "rex-omni.blocks.json", origin)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(json.dumps({"event": "ready", "port": port}), flush=True)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False,
                            log_config=None, log_level="critical", loop="asyncio", http="h11")
    try:
        asyncio.run(Server(config).serve(sockets=[listener]))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
