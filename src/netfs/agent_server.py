from __future__ import print_function

import argparse
import errno
import socket
import socketserver
import threading

from netfs.errors import error_payload_from_exception
from netfs.exec import ExecSessionManager
from netfs.fs import FilesystemService
from netfs.protocol import read_frame, write_frame


class RpcConnection(object):
    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self.write_lock = threading.Lock()

    def send_response(self, request_id, ok, result=None, error=None, payload=None):
        header = {
            "type": "response",
            "id": request_id,
            "ok": ok,
            "result": result,
            "error": error,
        }
        with self.write_lock:
            write_frame(self.wfile, header, payload=payload)


class NetfsRequestHandler(socketserver.StreamRequestHandler):
    def setup(self):
        socketserver.StreamRequestHandler.setup(self)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rpc = RpcConnection(self.rfile, self.wfile)

    def handle(self):
        while True:
            try:
                header, payload = read_frame(self.rfile)
            except EOFError:
                return
            request_thread = threading.Thread(
                target=self.server.handle_request,
                args=(self.rpc, header, payload),
            )
            request_thread.daemon = True
            request_thread.start()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, request_handler_class, fs_service, exec_service):
        self.fs_service = fs_service
        self.exec_service = exec_service
        socketserver.TCPServer.__init__(self, server_address, request_handler_class)

    def handle_request(self, rpc, header, payload):
        request_id = header.get("id")
        try:
            if header.get("type") != "request":
                raise ValueError("invalid message type")
            op = header["op"]
            params = header.get("params", {})
            result, response_payload = self.dispatch(op, params, payload)
            rpc.send_response(request_id, ok=True, result=result, payload=response_payload)
        except Exception as exc:
            rpc.send_response(
                request_id,
                ok=False,
                error=error_payload_from_exception(exc),
            )

    def dispatch(self, op, params, payload):
        if op == "ping":
            return {"pong": True}, None
        if op == "stat":
            return self.fs_service.stat(params["path"]), None
        if op == "lstat":
            return self.fs_service.lstat(params["path"]), None
        if op == "access":
            return self.fs_service.access(params["path"], int(params.get("mode", 0))), None
        if op == "readlink":
            return self.fs_service.readlink(params["path"]), None
        if op == "readdir":
            return self.fs_service.readdir(params["path"]), None
        if op == "open":
            return self.fs_service.open(params["path"], int(params.get("flags", 0))), None
        if op == "create":
            return self.fs_service.create(
                params["path"],
                int(params.get("flags", 0)),
                int(params.get("mode", 0o666)),
            ), None
        if op == "read":
            data = self.fs_service.read(
                int(params["handle"]),
                int(params.get("offset", 0)),
                int(params.get("size", 65536)),
            )
            return {"size": len(data), "eof": len(data) == 0}, data
        if op == "write":
            return self.fs_service.write(
                int(params["handle"]),
                int(params.get("offset", 0)),
                payload,
            ), None
        if op == "truncate":
            if "handle" in params and params["handle"] is not None:
                return self.fs_service.truncate(int(params["handle"]), int(params["size"])), None
            return self.fs_service.truncate_path(params["path"], int(params["size"])), None
        if op == "flush":
            return self.fs_service.flush(int(params["handle"])), None
        if op == "fsync":
            return self.fs_service.fsync(
                int(params["handle"]),
                datasync=bool(params.get("datasync", False)),
            ), None
        if op == "close":
            return self.fs_service.close(int(params["handle"])), None
        if op == "mkdir":
            return self.fs_service.mkdir(params["path"], int(params.get("mode", 0o777))), None
        if op == "rename":
            return self.fs_service.rename(params["old_path"], params["new_path"]), None
        if op == "unlink":
            return self.fs_service.unlink(params["path"]), None
        if op == "rmdir":
            return self.fs_service.rmdir(params["path"]), None
        if op == "fsyncdir":
            return self.fs_service.fsyncdir(params["path"]), None
        if op == "statfs":
            return self.fs_service.statfs(params["path"]), None
        if op == "exec_start":
            return self.exec_service.start(
                argv=params["argv"],
                cwd=params.get("cwd", "/"),
                extra_env=params.get("env", {}),
                stdin_mode=params.get("stdin_mode", "closed"),
            ), None
        if op == "exec_poll":
            return self.exec_service.poll(int(params["session_id"])), None
        if op == "exec_read_stdout":
            data, eof = self.exec_service.read_stdout(
                int(params["session_id"]),
                int(params.get("size", 65536)),
                wait=bool(params.get("wait", False)),
                timeout=params.get("timeout"),
            )
            return {"size": len(data), "eof": eof}, data
        if op == "exec_read_stderr":
            data, eof = self.exec_service.read_stderr(
                int(params["session_id"]),
                int(params.get("size", 65536)),
                wait=bool(params.get("wait", False)),
                timeout=params.get("timeout"),
            )
            return {"size": len(data), "eof": eof}, data
        if op == "exec_write_stdin":
            return self.exec_service.write_stdin(int(params["session_id"]), payload), None
        if op == "exec_close_stdin":
            return self.exec_service.close_stdin(int(params["session_id"])), None
        if op == "exec_wait":
            return self.exec_service.wait(int(params["session_id"]), timeout=params.get("timeout")), None
        if op == "exec_signal":
            return self.exec_service.send_signal(int(params["session_id"]), int(params["signal"])), None
        if op == "exec_close":
            return self.exec_service.close(int(params["session_id"])), None
        raise OSError(errno.ENOSYS, "unsupported operation: %s" % op)


def build_parser():
    parser = argparse.ArgumentParser(description="Run the device-side netfs agent.")
    parser.add_argument("--root", required=True, help="exported root directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47001)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    fs_service = FilesystemService(args.root)
    exec_service = ExecSessionManager(fs_service.exported_root)
    server = ThreadedTCPServer((args.host, args.port), NetfsRequestHandler, fs_service, exec_service)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        exec_service.close_all()
        fs_service.handles.close_all()
        server.server_close()


if __name__ == "__main__":
    main()
