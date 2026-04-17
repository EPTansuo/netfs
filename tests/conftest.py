from __future__ import print_function

import os
import socket
import subprocess
import sys
import time

import pytest

from netfs.rpc_client import RpcClient


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def exported_tree(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    (root / "hello.txt").write_text("hello from netfs\n", encoding="utf-8")
    (root / "subdir").mkdir()
    (root / "subdir" / "data.txt").write_text("payload", encoding="utf-8")
    os.symlink("hello.txt", str(root / "hello.link"))
    return root


@pytest.fixture()
def agent(exported_tree):
    port = _free_port()
    proc = None

    def start():
        nonlocal proc
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "netfs.agent_server",
                "--root",
                str(exported_tree),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
        )
        deadline = time.time() + 10.0
        while time.time() < deadline:
            try:
                client = RpcClient("127.0.0.1", port)
                client.connect()
                client.close()
                return
            except OSError:
                time.sleep(0.1)
        proc.terminate()
        raise RuntimeError("agent did not start")

    def stop():
        nonlocal proc
        if proc is None:
            return
        proc.terminate()
        proc.wait(timeout=10)
        proc = None

    start()
    yield {"port": port, "start": start, "stop": stop}
    stop()
