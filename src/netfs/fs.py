from __future__ import print_function

import errno
import os
import posixpath
import stat
import threading


WRITE_FLAG_MASK = (
    os.O_WRONLY
    | os.O_RDWR
    | getattr(os, "O_APPEND", 0)
    | getattr(os, "O_CREAT", 0)
    | getattr(os, "O_TRUNC", 0)
)


def normalize_remote_path(path):
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    norm = posixpath.normpath(path)
    if not norm.startswith("/"):
        norm = "/" + norm
    return norm


def stat_to_dict(st):
    return {
        "st_mode": st.st_mode,
        "st_ino": st.st_ino,
        "st_dev": st.st_dev,
        "st_nlink": st.st_nlink,
        "st_uid": st.st_uid,
        "st_gid": st.st_gid,
        "st_size": st.st_size,
        "st_atime_ns": _stat_ns(st, "st_atime_ns", "st_atime"),
        "st_mtime_ns": _stat_ns(st, "st_mtime_ns", "st_mtime"),
        "st_ctime_ns": _stat_ns(st, "st_ctime_ns", "st_ctime"),
    }


def _stat_ns(st, attr_ns, attr_s):
    value = getattr(st, attr_ns, None)
    if value is not None:
        return int(value)
    return int(getattr(st, attr_s) * 1000000000)


class ExportedRoot(object):
    def __init__(self, root_path):
        self.root_path = os.path.realpath(root_path)

    def resolve(self, remote_path, follow_symlinks=True):
        remote_path = normalize_remote_path(remote_path)
        joined = os.path.join(self.root_path, remote_path.lstrip("/"))
        if follow_symlinks:
            target = os.path.realpath(joined)
        else:
            base = os.path.realpath(os.path.dirname(joined))
            target = os.path.join(base, os.path.basename(joined))
        if target != self.root_path and not target.startswith(self.root_path + os.sep):
            raise OSError(errno.EPERM, "path escapes exported root")
        return target


class FileHandleTable(object):
    def __init__(self):
        self._next_handle = 1
        self._handles = {}
        self._lock = threading.Lock()

    def open(self, path, flags):
        if flags & WRITE_FLAG_MASK:
            raise OSError(errno.EROFS, "filesystem is read-only")
        fd = os.open(path, flags)
        with self._lock:
            handle = self._next_handle
            self._next_handle += 1
            self._handles[handle] = fd
            return handle

    def get(self, handle):
        with self._lock:
            fd = self._handles.get(handle)
        if fd is None:
            raise OSError(errno.EBADF, "invalid file handle")
        return fd

    def close(self, handle):
        with self._lock:
            fd = self._handles.pop(handle, None)
        if fd is None:
            raise OSError(errno.EBADF, "invalid file handle")
        os.close(fd)

    def close_all(self):
        with self._lock:
            handles = list(self._handles.items())
            self._handles.clear()
        for _, fd in handles:
            try:
                os.close(fd)
            except OSError:
                pass


class FilesystemService(object):
    def __init__(self, exported_root):
        self.exported_root = ExportedRoot(exported_root)
        self.handles = FileHandleTable()

    def stat(self, path):
        target = self.exported_root.resolve(path)
        return stat_to_dict(os.stat(target))

    def lstat(self, path):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        return stat_to_dict(os.lstat(target))

    def access(self, path, mode):
        target = self.exported_root.resolve(path)
        return {"allowed": os.access(target, mode)}

    def readlink(self, path):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        if not stat.S_ISLNK(os.lstat(target).st_mode):
            raise OSError(errno.EINVAL, "not a symbolic link")
        return {"target": os.readlink(target)}

    def readdir(self, path):
        target = self.exported_root.resolve(path)
        entries = []
        for name in sorted(os.listdir(target)):
            entry_path = os.path.join(target, name)
            entries.append({
                "name": name,
                "stat": stat_to_dict(os.lstat(entry_path)),
            })
        return {"entries": entries}

    def open(self, path, flags):
        target = self.exported_root.resolve(path)
        return {"handle": self.handles.open(target, flags)}

    def read(self, handle, offset, size):
        fd = self.handles.get(handle)
        return os.pread(fd, size, offset)

    def close(self, handle):
        self.handles.close(handle)
        return {"closed": True}

    def statfs(self, path):
        target = self.exported_root.resolve(path)
        st = os.statvfs(target)
        return {
            "f_bsize": st.f_bsize,
            "f_frsize": st.f_frsize,
            "f_blocks": st.f_blocks,
            "f_bfree": st.f_bfree,
            "f_bavail": st.f_bavail,
            "f_files": st.f_files,
            "f_ffree": st.f_ffree,
            "f_favail": getattr(st, "f_favail", st.f_ffree),
            "f_flag": getattr(st, "f_flag", 0),
            "f_namemax": getattr(st, "f_namemax", 255),
        }
