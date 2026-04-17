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
        self._state_lock = threading.Lock()

    def connect(self):
        with self._state_lock:
            if self.sock is not None:
                return
            self.sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(None)
            self.reader = self.sock.makefile("rb")
            self.writer = self.sock.makefile("wb")
            self._reader_thread = threading.Thread(target=self._reader_loop, name="netfs-rpc-reader")
            self._reader_thread.daemon = True
            self._reader_thread.start()

    def close(self):
        self._closed = True
        self._reset_connection()
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.put(RuntimeError("rpc connection closed"))

    def _reset_connection(self):
        with self._state_lock:
            writer = self.writer
            reader = self.reader
            sock = self.sock
            self.writer = None
            self.reader = None
            self.sock = None
            self._reader_thread = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            if writer is not None:
                writer.close()
        finally:
            try:
                if reader is not None:
                    reader.close()
            finally:
                if sock is not None:
                    sock.close()

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
            self._reset_connection()
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
        try:
            with self._write_lock:
                write_frame(self.writer, header, payload=payload)
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            self._reset_connection()
            raise
        try:
            item = response_queue.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("rpc request timed out")
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

    def lstat(self, path):
        result, _ = self.request("lstat", {"path": path})
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

    def create(self, path, flags=0, mode=0o666):
        result, _ = self.request("create", {"path": path, "flags": flags, "mode": mode})
        return result["handle"]

    def read(self, handle, offset, size):
        result, payload = self.request(
            "read",
            {"handle": handle, "offset": offset, "size": size},
        )
        return payload, result["eof"]

    def write(self, handle, offset, data):
        result, _ = self.request(
            "write",
            {"handle": handle, "offset": offset},
            payload=data,
        )
        return result["written"]

    def truncate(self, size, handle=None, path=None):
        params = {"size": size}
        if handle is not None:
            params["handle"] = handle
        if path is not None:
            params["path"] = path
        self.request("truncate", params)

    def flush(self, handle):
        self.request("flush", {"handle": handle})

    def fsync(self, handle, datasync=False):
        self.request("fsync", {"handle": handle, "datasync": datasync})

    def close_handle(self, handle):
        self.request("close", {"handle": handle})

    def mkdir(self, path, mode=0o777):
        self.request("mkdir", {"path": path, "mode": mode})

    def rename(self, old_path, new_path):
        self.request("rename", {"old_path": old_path, "new_path": new_path})

    def unlink(self, path):
        self.request("unlink", {"path": path})

    def rmdir(self, path):
        self.request("rmdir", {"path": path})

    def fsyncdir(self, path):
        self.request("fsyncdir", {"path": path})

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
