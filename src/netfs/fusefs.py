from __future__ import print_function

import errno
import os
import posixpath
import threading
import time

from netfs.fs import UNSUPPORTED_WRITE_FLAGS, normalize_remote_path
from netfs.rpc_client import RpcClient

try:
    from fuse import FUSE, FuseOSError, Operations
except ImportError:  # pragma: no cover
    FUSE = None
    Operations = object

    class FuseOSError(OSError):
        pass


TRANSPORT_ERRNOS = {
    errno.EPIPE,
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.ENOTCONN,
    errno.ETIMEDOUT,
    errno.EHOSTDOWN,
    errno.EHOSTUNREACH,
    errno.ECONNREFUSED,
}


def _join_remote(root, path):
    path = normalize_remote_path(path)
    if root == "/":
        return path
    return normalize_remote_path(posixpath.join(root, path.lstrip("/")))


def _is_transport_error(exc):
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, (EOFError, RuntimeError, ConnectionError)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in TRANSPORT_ERRNOS
    return False


class LocalHandleTable(object):
    def __init__(self):
        self._next_handle = 1
        self._handles = {}
        self._lock = threading.Lock()

    def register(self, remote_handle, writable):
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            self._handles[handle] = {
                "remote_handle": remote_handle,
                "writable": writable,
                "uncertain": False,
                "stale": False,
            }
            return handle

    def get(self, handle):
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            raise FuseOSError(errno.EBADF)
        return entry

    def mark_uncertain(self, handle):
        with self._lock:
            entry = self._handles.get(handle)
            if entry is not None:
                entry["uncertain"] = True
                entry["stale"] = True

    def mark_all_stale(self):
        with self._lock:
            for entry in self._handles.values():
                entry["stale"] = True

    def pop(self, handle):
        with self._lock:
            return self._handles.pop(handle, None)


class MetadataCache(object):
    def __init__(self, ttl_seconds=2.0):
        self.ttl_seconds = ttl_seconds
        self._entries = {}
        self._lock = threading.Lock()

    def store(self, path, stat_result):
        expires_at = time.monotonic() + self.ttl_seconds
        with self._lock:
            self._entries[path] = (expires_at, dict(stat_result))

    def get(self, path):
        now = time.monotonic()
        with self._lock:
            item = self._entries.get(path)
            if item is None:
                return None
            expires_at, stat_result = item
            if expires_at < now:
                del self._entries[path]
                return None
            return dict(stat_result)

    def invalidate(self, path):
        with self._lock:
            self._entries.pop(path, None)

    def invalidate_many(self, paths):
        with self._lock:
            for path in paths:
                self._entries.pop(path, None)


class NetfsFuseOperations(Operations):
    def __init__(self, client, remote_root="/"):
        self.client = client
        self.remote_root = normalize_remote_path(remote_root)
        self.handles = LocalHandleTable()
        self.metadata_cache = MetadataCache(ttl_seconds=2.0)

    def _remote(self, path):
        return _join_remote(self.remote_root, path)

    def _parent_paths(self, path):
        parent = posixpath.dirname(normalize_remote_path(path))
        return [normalize_remote_path(path), normalize_remote_path(parent)]

    def _invalidate_paths(self, *paths):
        targets = []
        for path in paths:
            if path is None:
                continue
            for item in self._parent_paths(path):
                if item not in targets:
                    targets.append(item)
        self.metadata_cache.invalidate_many(targets)

    def _translate_error(self, exc, fh=None):
        if fh is not None and _is_transport_error(exc):
            self.handles.mark_uncertain(fh)
            self.handles.mark_all_stale()
            raise FuseOSError(errno.EIO)
        if _is_transport_error(exc):
            self.handles.mark_all_stale()
            raise FuseOSError(errno.EIO)
        if isinstance(exc, OSError) and exc.errno is not None:
            raise FuseOSError(exc.errno)
        raise FuseOSError(errno.EIO)

    def _handle_entry(self, fh, require_writable=False):
        entry = self.handles.get(fh)
        if entry["stale"]:
            raise FuseOSError(errno.EIO)
        if require_writable and not entry["writable"]:
            raise FuseOSError(errno.EBADF)
        return entry

    def access(self, path, mode):
        try:
            if not self.client.access(self._remote(path), mode):
                raise FuseOSError(errno.EACCES)
        except Exception as exc:
            self._translate_error(exc)

    def getattr(self, path, fh=None):
        remote_path = self._remote(path)
        cached = self.metadata_cache.get(remote_path)
        if cached is not None:
            return cached
        try:
            result = self.client.lstat(remote_path)
            self.metadata_cache.store(remote_path, result)
            return result
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def readlink(self, path):
        try:
            return self.client.readlink(self._remote(path))
        except Exception as exc:
            self._translate_error(exc)

    def readdir(self, path, fh):
        yield "."
        yield ".."
        remote_path = self._remote(path)
        try:
            for entry in self.client.readdir(remote_path):
                entry_path = _join_remote(remote_path, entry["name"])
                if "stat" in entry:
                    self.metadata_cache.store(entry_path, entry["stat"])
                yield entry["name"]
        except Exception as exc:
            self._translate_error(exc)

    def open(self, path, flags):
        if flags & UNSUPPORTED_WRITE_FLAGS:
            raise FuseOSError(errno.EOPNOTSUPP)
        try:
            remote_handle = self.client.open(self._remote(path), flags)
            return self.handles.register(remote_handle, writable=bool(flags & (os.O_WRONLY | os.O_RDWR)))
        except Exception as exc:
            self._translate_error(exc)

    def create(self, path, mode, fi=None):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if fi is not None and hasattr(fi, "flags"):
            flags = fi.flags
        if flags & UNSUPPORTED_WRITE_FLAGS:
            raise FuseOSError(errno.EOPNOTSUPP)
        try:
            remote_handle = self.client.create(self._remote(path), flags=flags, mode=mode)
            self._invalidate_paths(path)
            return self.handles.register(remote_handle, writable=True)
        except Exception as exc:
            self._translate_error(exc)

    def read(self, path, size, offset, fh):
        entry = self._handle_entry(fh)
        try:
            payload, _ = self.client.read(entry["remote_handle"], offset, size)
            return payload
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def write(self, path, data, offset, fh):
        entry = self._handle_entry(fh, require_writable=True)
        try:
            written = self.client.write(entry["remote_handle"], offset, data)
            if written != len(data):
                raise FuseOSError(errno.EIO)
            self.metadata_cache.invalidate(self._remote(path))
            return written
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def truncate(self, path, length, fh=None):
        try:
            if fh is not None:
                entry = self._handle_entry(fh, require_writable=True)
                self.client.truncate(length, handle=entry["remote_handle"])
            else:
                self.client.truncate(length, path=self._remote(path))
            self.metadata_cache.invalidate(self._remote(path))
            return 0
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def flush(self, path, fh):
        entry = self._handle_entry(fh)
        if entry["uncertain"]:
            raise FuseOSError(errno.EIO)
        try:
            self.client.flush(entry["remote_handle"])
            return 0
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def fsync(self, path, datasync, fh):
        entry = self._handle_entry(fh, require_writable=True)
        if entry["uncertain"]:
            raise FuseOSError(errno.EIO)
        try:
            self.client.fsync(entry["remote_handle"], datasync=bool(datasync))
            return 0
        except Exception as exc:
            self._translate_error(exc, fh=fh)

    def release(self, path, fh):
        entry = self.handles.pop(fh)
        if entry is None:
            return 0
        if entry["stale"]:
            return 0
        try:
            self.client.close_handle(entry["remote_handle"])
            return 0
        except Exception:
            return 0

    def mkdir(self, path, mode):
        try:
            self.client.mkdir(self._remote(path), mode)
            self._invalidate_paths(path)
            return 0
        except Exception as exc:
            self._translate_error(exc)

    def rename(self, old, new):
        try:
            self.client.rename(self._remote(old), self._remote(new))
            self._invalidate_paths(old, new)
            return 0
        except Exception as exc:
            self._translate_error(exc)

    def unlink(self, path):
        try:
            self.client.unlink(self._remote(path))
            self._invalidate_paths(path)
            return 0
        except Exception as exc:
            self._translate_error(exc)

    def rmdir(self, path):
        try:
            self.client.rmdir(self._remote(path))
            self._invalidate_paths(path)
            return 0
        except Exception as exc:
            self._translate_error(exc)

    def fsyncdir(self, path, datasync, fh):
        try:
            self.client.fsyncdir(self._remote(path))
            return 0
        except Exception as exc:
            self._translate_error(exc)

    def statfs(self, path):
        try:
            return self.client.statfs(self._remote(path))
        except Exception as exc:
            self._translate_error(exc)


def mount_foreground(host, port, mountpoint, remote_root="/", allow_other=False):
    if FUSE is None:
        raise RuntimeError("fusepy is not installed")
    client = RpcClient(host, port)
    client.connect()
    operations = NetfsFuseOperations(client, remote_root=remote_root)
    return FUSE(
        operations,
        mountpoint,
        foreground=True,
        ro=False,
        nothreads=False,
        allow_other=allow_other,
        direct_io=True,
        kernel_cache=False,
        auto_cache=False,
        entry_timeout=2.0,
        attr_timeout=2.0,
        negative_timeout=0.0,
    )
