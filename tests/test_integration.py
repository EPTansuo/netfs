from __future__ import print_function

import os
import socket
import subprocess
import sys
import tempfile
import textwrap
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
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("agent did not start")
    yield port
    proc.terminate()
    proc.wait(timeout=10)


def test_ping_and_files(agent):
    client = RpcClient("127.0.0.1", agent)
    try:
        assert client.ping()["pong"] is True
        entries = client.readdir("/")
        names = [item["name"] for item in entries]
        assert names == ["hello.link", "hello.txt", "subdir"]
        hello = client.stat("/hello.txt")
        assert hello["st_size"] == len("hello from netfs\n")
        link = client.readlink("/hello.link")
        assert link == "hello.txt"
        handle = client.open("/hello.txt", os.O_RDONLY)
        try:
            payload, eof = client.read(handle, 0, 4096)
            assert payload == b"hello from netfs\n"
            assert eof is False
            payload, eof = client.read(handle, len(payload), 4096)
            assert payload == b""
            assert eof is True
        finally:
            client.close_handle(handle)
    finally:
        client.close()


def test_exec_session(agent):
    client = RpcClient("127.0.0.1", agent)
    try:
        session_id = client.exec_start(
            [
                "/bin/sh",
                "-lc",
                "printf 'out:%s\\n' \"$PWD\"; printf 'err-line\\n' 1>&2",
            ],
            cwd="/subdir",
        )
        stdout_chunks = []
        stderr_chunks = []
        stdout_eof = False
        stderr_eof = False
        while not (stdout_eof and stderr_eof):
            if not stdout_eof:
                data, stdout_eof = client.exec_read_stdout(session_id, wait=True, timeout=0.2)
                stdout_chunks.append(data)
            if not stderr_eof:
                data, stderr_eof = client.exec_read_stderr(session_id, wait=False, timeout=0.0)
                stderr_chunks.append(data)
        wait_result = client.exec_wait(session_id)
        assert wait_result == {"completed": True, "returncode": 0}
        assert b"out:" in b"".join(stdout_chunks)
        assert b"/subdir" in b"".join(stdout_chunks)
        assert b"err-line\n" in b"".join(stderr_chunks)
    finally:
        try:
            client.exec_close(session_id)
        except Exception:
            pass
        client.close()
