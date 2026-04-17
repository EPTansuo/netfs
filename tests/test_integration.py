import errno
import os

import pytest

from netfs.rpc_client import RpcClient


def test_ping_and_files(agent):
    client = RpcClient("127.0.0.1", agent["port"])
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
    client = RpcClient("127.0.0.1", agent["port"])
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


def test_write_and_directory_ops(agent):
    client = RpcClient("127.0.0.1", agent["port"])
    handle = None
    try:
        client.mkdir("/work", 0o755)
        client.fsyncdir("/work")
        handle = client.create("/work/output.txt", flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode=0o644)
        assert client.write(handle, 0, b"hello") == 5
        assert client.write(handle, 5, b" world") == 6
        client.fsync(handle, datasync=False)
        client.truncate(5, handle=handle)
        client.fsync(handle, datasync=False)
        with pytest.raises(OSError) as busy:
            client.open("/work/output.txt", os.O_WRONLY)
        assert busy.value.errno == errno.EBUSY
        client.close_handle(handle)
        handle = None

        read_handle = client.open("/work/output.txt", os.O_RDONLY)
        try:
            payload, eof = client.read(read_handle, 0, 1024)
            assert payload == b"hello"
            assert eof is False
        finally:
            client.close_handle(read_handle)

        client.rename("/work/output.txt", "/work/final.txt")
        entries = [item["name"] for item in client.readdir("/work")]
        assert entries == ["final.txt"]
        client.unlink("/work/final.txt")
        client.rmdir("/work")
        root_entries = [item["name"] for item in client.readdir("/")]
        assert root_entries == ["hello.link", "hello.txt", "subdir"]
    finally:
        if handle is not None:
            try:
                client.close_handle(handle)
            except Exception:
                pass
        client.close()


def test_append_is_rejected(agent):
    client = RpcClient("127.0.0.1", agent["port"])
    try:
        with pytest.raises(OSError) as exc_info:
            client.open("/hello.txt", os.O_WRONLY | os.O_APPEND)
        assert exc_info.value.errno == errno.EOPNOTSUPP
    finally:
        client.close()


def test_reconnect_after_agent_restart(agent):
    client = RpcClient("127.0.0.1", agent["port"])
    try:
        assert client.ping()["pong"] is True
        agent["stop"]()
        timeouts = []
        try:
            client.ping()
        except Exception as exc:
            timeouts.append(exc)
        agent["start"]()
        assert timeouts
        assert client.ping()["pong"] is True
    finally:
        client.close()
