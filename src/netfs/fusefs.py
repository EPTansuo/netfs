from __future__ import print_function

import errno
import os
import posixpath

from netfs.fs import normalize_remote_path
from netfs.rpc_client import RpcClient

try:
    from fuse import FUSE, FuseOSError, Operations
except ImportError:  # pragma: no cover
    FUSE = None
    Operations = object

    class FuseOSError(OSError):
        pass


def _join_remote(root, path):
    path = normalize_remote_path(path)
    if root == "/":
        return path
    return normalize_remote_path(posixpath.join(root, path.lstrip("/")))


class NetfsFuseOperations(Operations):
    def __init__(self, client, remote_root="/"):
        self.client = client
        self.remote_root = normalize_remote_path(remote_root)

    def _remote(self, path):
        return _join_remote(self.remote_root, path)

    def access(self, path, mode):
        if not self.client.access(self._remote(path), mode):
            raise FuseOSError(errno.EACCES)

    def getattr(self, path, fh=None):
        try:
            return self.client.stat(self._remote(path), follow_symlinks=False)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def readlink(self, path):
        try:
            return self.client.readlink(self._remote(path))
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def readdir(self, path, fh):
        yield "."
        yield ".."
        try:
            for entry in self.client.readdir(self._remote(path)):
                yield entry["name"]
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def open(self, path, flags):
        write_mask = os.O_WRONLY | os.O_RDWR | getattr(os, "O_APPEND", 0)
        if flags & write_mask:
            raise FuseOSError(errno.EROFS)
        try:
            return self.client.open(self._remote(path), flags)
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def read(self, path, size, offset, fh):
        try:
            payload, _ = self.client.read(fh, offset, size)
            return payload
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def release(self, path, fh):
        try:
            self.client.close_handle(fh)
            return 0
        except OSError as exc:
            raise FuseOSError(exc.errno)

    def statfs(self, path):
        try:
            return self.client.statfs(self._remote(path))
        except OSError as exc:
            raise FuseOSError(exc.errno)


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
        ro=True,
        nothreads=False,
        allow_other=allow_other,
    )
