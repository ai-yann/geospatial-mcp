"""HTTP transport: `mcp-geospatial --transport http` starts and answers initialize.

mcp 2.0 removed `host` and `port` from the server settings object, so setting
them before `run()` raised ValueError and the HTTP transport never started.
Only a test that launches the CLI catches that, because stdio keeps working.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_transport_answers_initialize(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "PORT"}
    env["GEO_CACHE_DIR"] = str(tmp_path / "cache")
    proc = subprocess.Popen(
        [sys.executable, "-m", "geospatial_mcp", "--transport", "http", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"server exited early: {proc.stderr.read()[-2000:]}")
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            raise AssertionError("server did not open its port")

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            assert r.status == 200
            text = r.read().decode()
        assert '"serverInfo"' in text
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
