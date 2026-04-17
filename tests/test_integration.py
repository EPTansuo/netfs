import os

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
