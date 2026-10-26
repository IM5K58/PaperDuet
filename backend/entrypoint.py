"""Sidecar boot protocol. stdout is one JSON readiness line; never log secrets."""
import asyncio
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from paperduet.app import create_app


def arm_parent_pipe(stream, group):
    """Own only the isolated group explicitly handed to us by the desktop app."""
    if group <= 1 or os.getpgrp() != group:
        raise RuntimeError('Invalid backend process group')
    def watch():
        import signal
        stream.read()  # EOF also occurs after a desktop crash or SIGKILL.
        os.killpg(group, signal.SIGKILL)
    threading.Thread(target=watch, name='desktop-lifetime', daemon=True).start()


def default_data_dir():
    if sys.platform == 'darwin':return Path.home() / 'Library/Application Support/PaperDuet'
    return Path(os.environ.get('APPDATA', Path.home())) / 'PaperDuet'


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # The desktop owns the Windows Job or macOS process group before releasing
    # this gate. No server/workers may start before that assignment.
    if os.environ.pop("PAPERDUET_START_GATE", "") == "1":
        gate = sys.stdin.readline().strip().split()
        if sys.platform == 'win32':
            if gate != ['start']:return
        else:
            if len(gate) != 2 or gate[0] != 'start' or not gate[1].isdigit():return
            arm_parent_pipe(sys.stdin, int(gate[1]))
    token = os.environ.pop("PAPERDUET_SESSION_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Missing session authorization")
    origin = os.environ.pop("PAPERDUET_ORIGIN", "http://tauri.localhost")
    allowed = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost", "http://127.0.0.1:1420"}
    if origin not in allowed:
        raise RuntimeError("Unsupported app origin")
    data_dir = Path(os.environ.pop("PAPERDUET_DATA_DIR", str(default_data_dir())))
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
