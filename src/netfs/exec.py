from __future__ import print_function

import errno
import os
import signal
import subprocess
import threading
import time


STREAM_STDOUT = "stdout"
STREAM_STDERR = "stderr"


class ExecSession(object):
    def __init__(self, session_id, argv, cwd, env, stdin_mode):
        self.session_id = session_id
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.stdin_mode = stdin_mode
        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin_mode == "pipe" else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buffers = {
            STREAM_STDOUT: bytearray(),
            STREAM_STDERR: bytearray(),
        }
        self._eof = {
            STREAM_STDOUT: False,
            STREAM_STDERR: False,
        }
        self._threads = []
        self._start_reader(STREAM_STDOUT, self.process.stdout)
        self._start_reader(STREAM_STDERR, self.process.stderr)

    def _start_reader(self, stream_name, source):
        thread = threading.Thread(
            target=self._reader_loop,
            args=(stream_name, source),
            name="exec-session-%s-%s" % (self.session_id, stream_name),
        )
        thread.daemon = True
        thread.start()
        self._threads.append(thread)

    def _reader_loop(self, stream_name, source):
        try:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                with self._cond:
                    self._buffers[stream_name].extend(chunk)
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._eof[stream_name] = True
                self._cond.notify_all()
            try:
                source.close()
            except Exception:
                pass

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def read_stream(self, stream_name, max_bytes, wait=False, timeout=None):
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout
        with self._cond:
            while wait and not self._buffers[stream_name] and not self._eof[stream_name]:
                remaining = None
                if deadline is not None:
                    remaining = max(0.0, deadline - time.time())
                    if remaining == 0.0:
                        break
                self._cond.wait(remaining)
            size = min(len(self._buffers[stream_name]), max_bytes)
            data = bytes(self._buffers[stream_name][:size])
            if size:
                del self._buffers[stream_name][:size]
            eof = self._eof[stream_name] and not self._buffers[stream_name]
            return data, eof

    def write_stdin(self, data):
        if self.process.stdin is None:
            raise OSError(errno.EPIPE, "stdin is not writable")
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except BrokenPipeError:
            raise OSError(errno.EPIPE, "stdin is closed")
        return {"written": len(data)}

    def close_stdin(self):
        if self.process.stdin is not None:
            self.process.stdin.close()
        return {"closed": True}

    def send_signal(self, signum):
        self.process.send_signal(signum)
        return {"sent": True}

    def terminate(self):
        self.process.terminate()

    def snapshot(self):
        with self._lock:
            return {
                "session_id": self.session_id,
                "argv": list(self.argv),
                "cwd": self.cwd,
                "returncode": self.process.poll(),
                "stdout_eof": self._eof[STREAM_STDOUT] and not self._buffers[STREAM_STDOUT],
                "stderr_eof": self._eof[STREAM_STDERR] and not self._buffers[STREAM_STDERR],
                "stdout_buffered": len(self._buffers[STREAM_STDOUT]),
                "stderr_buffered": len(self._buffers[STREAM_STDERR]),
            }

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except Exception:
                pass


class ExecSessionManager(object):
    def __init__(self, exported_root):
        self.exported_root = exported_root
        self._next_session_id = 1
        self._sessions = {}
        self._lock = threading.Lock()

    def _resolve_cwd(self, cwd):
        return self.exported_root.resolve(cwd or "/")

    def start(self, argv, cwd, extra_env=None, stdin_mode="closed"):
        if not isinstance(argv, list) or not argv:
            raise ValueError("argv must be a non-empty list")
        full_env = os.environ.copy()
        if extra_env:
            full_env.update(extra_env)
        with self._lock:
            session_id = self._next_session_id
            self._next_session_id += 1
            session = ExecSession(
                session_id=session_id,
                argv=argv,
                cwd=self._resolve_cwd(cwd),
                env=full_env,
                stdin_mode=stdin_mode,
            )
            self._sessions[session_id] = session
            return {"session_id": session_id}

    def _get(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise OSError(errno.ENOENT, "unknown exec session")
        return session

    def poll(self, session_id):
        return self._get(session_id).snapshot()

    def read_stdout(self, session_id, max_bytes, wait=False, timeout=None):
        return self._get(session_id).read_stream(STREAM_STDOUT, max_bytes, wait=wait, timeout=timeout)

    def read_stderr(self, session_id, max_bytes, wait=False, timeout=None):
        return self._get(session_id).read_stream(STREAM_STDERR, max_bytes, wait=wait, timeout=timeout)

    def write_stdin(self, session_id, data):
        return self._get(session_id).write_stdin(data)

    def close_stdin(self, session_id):
        return self._get(session_id).close_stdin()

    def wait(self, session_id, timeout=None):
        try:
            returncode = self._get(session_id).wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"completed": False}
        return {"completed": True, "returncode": returncode}

    def send_signal(self, session_id, signum):
        return self._get(session_id).send_signal(signum)

    def close(self, session_id):
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise OSError(errno.ENOENT, "unknown exec session")
        session.close()
        return {"closed": True}

    def close_all(self):
        with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()
        for _, session in sessions:
            try:
                session.close()
            except Exception:
                pass
