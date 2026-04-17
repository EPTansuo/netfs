from __future__ import print_function

import itertools
import queue
import socket
import threading

from netfs.errors import RpcError
from netfs.protocol import read_frame, write_frame


class RpcClient(object):
    def __init__(self, host, port, connect_timeout=10.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.sock = None
        self.reader = None
        self.writer = None
        self._closed = False
        self._reader_thread = None
        self._write_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._request_ids = itertools.count(1)

    def connect(self):
        if self.sock is not None:
            return
        self.sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        self.reader = self.sock.makefile("rb")
        self.writer = self.sock.makefile("wb")
        self._reader_thread = threading.Thread(target=self._reader_loop, name="netfs-rpc-reader")
        self._reader_thread.daemon = True
        self._reader_thread.start()

    def close(self):
        self._closed = True
        try:
            if self.writer is not None:
                self.writer.close()
        finally:
            try:
                if self.reader is not None:
                    self.reader.close()
            finally:
                if self.sock is not None:
                    self.sock.close()
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.put(RuntimeError("rpc connection closed"))

    def _reader_loop(self):
        try:
            while not self._closed:
                header, payload = read_frame(self.reader)
                response_queue = None
                with self._pending_lock:
                    response_queue = self._pending.pop(header["id"], None)
                if response_queue is not None:
                    response_queue.put((header, payload))
        except Exception as exc:
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for item in pending:
                item.put(exc)

    def request(self, op, params=None, payload=None, timeout=10.0):
        self.connect()
        request_id = next(self._request_ids)
        response_queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        header = {
            "type": "request",
            "id": request_id,
            "op": op,
            "params": params or {},
        }
        with self._write_lock:
            write_frame(self.writer, header, payload=payload)
        item = response_queue.get(timeout=timeout)
        if isinstance(item, Exception):
            raise item
        response, response_payload = item
        if not response.get("ok"):
            error = response.get("error", {})
            raise RpcError(
                code=error.get("code", "RemoteError"),
                message=error.get("message", "remote call failed"),
                remote_errno=error.get("errno"),
            ).to_exception()
        return response.get("result"), response_payload

    def ping(self):
        result, _ = self.request("ping")
        return result

    def stat(self, path, follow_symlinks=True):
        op = "stat" if follow_symlinks else "lstat"
        result, _ = self.request(op, {"path": path})
        return result

    def access(self, path, mode):
        result, _ = self.request("access", {"path": path, "mode": mode})
        return result["allowed"]

    def readlink(self, path):
        result, _ = self.request("readlink", {"path": path})
        return result["target"]

    def readdir(self, path):
        result, _ = self.request("readdir", {"path": path})
        return result["entries"]

    def open(self, path, flags=0):
        result, _ = self.request("open", {"path": path, "flags": flags})
        return result["handle"]

    def read(self, handle, offset, size):
        result, payload = self.request(
            "read",
            {"handle": handle, "offset": offset, "size": size},
        )
        return payload, result["eof"]

    def close_handle(self, handle):
        self.request("close", {"handle": handle})

    def statfs(self, path):
        result, _ = self.request("statfs", {"path": path})
        return result

    def exec_start(self, argv, cwd="/", env=None, stdin_mode="closed"):
        result, _ = self.request(
            "exec_start",
            {
                "argv": argv,
                "cwd": cwd,
                "env": env or {},
                "stdin_mode": stdin_mode,
            },
        )
        return result["session_id"]

    def exec_poll(self, session_id):
        result, _ = self.request("exec_poll", {"session_id": session_id})
        return result

    def exec_read_stdout(self, session_id, size=65536, wait=False, timeout=0.5):
        result, payload = self.request(
            "exec_read_stdout",
            {"session_id": session_id, "size": size, "wait": wait, "timeout": timeout},
            timeout=max(10.0, timeout + 2.0),
        )
        return payload, result["eof"]

    def exec_read_stderr(self, session_id, size=65536, wait=False, timeout=0.5):
        result, payload = self.request(
            "exec_read_stderr",
            {"session_id": session_id, "size": size, "wait": wait, "timeout": timeout},
            timeout=max(10.0, timeout + 2.0),
        )
        return payload, result["eof"]

    def exec_write_stdin(self, session_id, data):
        result, _ = self.request("exec_write_stdin", {"session_id": session_id}, payload=data)
        return result

    def exec_close_stdin(self, session_id):
        self.request("exec_close_stdin", {"session_id": session_id})

    def exec_wait(self, session_id, timeout=None):
        result, _ = self.request("exec_wait", {"session_id": session_id, "timeout": timeout}, timeout=30.0)
        return result

    def exec_signal(self, session_id, signum):
        self.request("exec_signal", {"session_id": session_id, "signal": signum})

    def exec_close(self, session_id):
        self.request("exec_close", {"session_id": session_id})
