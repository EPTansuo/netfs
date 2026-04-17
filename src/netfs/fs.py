from __future__ import print_function

import errno
import os
import posixpath
import stat
import threading


WRITE_ACCESS_MASK = os.O_WRONLY | os.O_RDWR
UNSUPPORTED_WRITE_FLAGS = getattr(os, "O_APPEND", 0)
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)


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


def _is_writable_flags(flags):
    return bool(flags & WRITE_ACCESS_MASK)


def _ensure_supported_flags(flags):
    if flags & UNSUPPORTED_WRITE_FLAGS:
        raise OSError(errno.EOPNOTSUPP, "O_APPEND is not supported")


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

    def parent_remote_path(self, remote_path):
        remote_path = normalize_remote_path(remote_path)
        return normalize_remote_path(posixpath.dirname(remote_path))


class FileHandleTable(object):
    def __init__(self):
        self._next_handle = 1
        self._handles = {}
        self._writers = {}
        self._lock = threading.Lock()

    def _reserve_writer(self, inode_key, owner):
        with self._lock:
            current = self._writers.get(inode_key)
            if current is not None and current != owner:
                raise OSError(errno.EBUSY, "another writer already holds this file")
            self._writers[inode_key] = owner

    def _release_writer(self, inode_key, owner):
        with self._lock:
            current = self._writers.get(inode_key)
            if current == owner:
                del self._writers[inode_key]

    def _register_handle(self, fd, path, writable):
        st = os.fstat(fd)
        inode_key = (st.st_dev, st.st_ino)
        handle = None
        try:
            with self._lock:
                if writable:
                    current = self._writers.get(inode_key)
                    if current is not None:
                        raise OSError(errno.EBUSY, "another writer already holds this file")
                handle = self._next_handle
                self._next_handle += 1
                entry = {
                    "fd": fd,
                    "path": path,
                    "writable": writable,
                    "inode_key": inode_key,
                }
                self._handles[handle] = entry
                if writable:
                    self._writers[inode_key] = handle
                return handle
        except Exception:
            os.close(fd)
            raise

    def open(self, path, flags, mode=0o666):
        _ensure_supported_flags(flags)
        fd = os.open(path, flags, mode)
        return self._register_handle(fd, path, _is_writable_flags(flags))

    def create(self, path, flags, mode):
        flags = flags | os.O_CREAT
        return self.open(path, flags, mode=mode)

    def get(self, handle):
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            raise OSError(errno.EBADF, "invalid file handle")
        return entry

    def close(self, handle):
        with self._lock:
            entry = self._handles.pop(handle, None)
        if entry is None:
            raise OSError(errno.EBADF, "invalid file handle")
        try:
            os.close(entry["fd"])
        finally:
            if entry["writable"]:
                self._release_writer(entry["inode_key"], handle)

    def close_all(self):
        with self._lock:
            handles = list(self._handles.items())
            self._handles.clear()
            self._writers.clear()
        for _, entry in handles:
            try:
                os.close(entry["fd"])
            except OSError:
                pass

    def temporary_writer(self, path):
        _ensure_supported_flags(os.O_WRONLY)
        fd = os.open(path, os.O_WRONLY)
        st = os.fstat(fd)
        inode_key = (st.st_dev, st.st_ino)
        owner = ("temp", fd)
        try:
            self._reserve_writer(inode_key, owner)
        except Exception:
            os.close(fd)
            raise
        return fd, inode_key, owner


class FilesystemService(object):
    def __init__(self, exported_root):
        self.exported_root = ExportedRoot(exported_root)
        self.handles = FileHandleTable()

    def _fsync_directory_remote(self, remote_path):
        directory = self.exported_root.resolve(remote_path)
        fd = os.open(directory, DIRECTORY_OPEN_FLAGS)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _fsync_parents(self, *remote_paths):
        parents = []
        for remote_path in remote_paths:
            parent = self.exported_root.parent_remote_path(remote_path)
            if parent not in parents:
                parents.append(parent)
        for parent in parents:
            self._fsync_directory_remote(parent)

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

    def create(self, path, flags, mode):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        handle = self.handles.create(target, flags, mode)
        self._fsync_parents(path)
        return {"handle": handle}

    def read(self, handle, offset, size):
        entry = self.handles.get(handle)
        return os.pread(entry["fd"], size, offset)

    def write(self, handle, offset, data):
        entry = self.handles.get(handle)
        if not entry["writable"]:
            raise OSError(errno.EBADF, "file handle is not writable")
        total = 0
        while total < len(data):
            written = os.pwrite(entry["fd"], data[total:], offset + total)
            if written <= 0:
                raise OSError(errno.EIO, "short write to remote file")
            total += written
        return {"written": total}

    def truncate(self, handle, size):
        entry = self.handles.get(handle)
        if not entry["writable"]:
            raise OSError(errno.EBADF, "file handle is not writable")
        os.ftruncate(entry["fd"], size)
        return {"truncated": True}

    def truncate_path(self, path, size):
        target = self.exported_root.resolve(path)
        fd, inode_key, owner = self.handles.temporary_writer(target)
        try:
            os.ftruncate(fd, size)
            os.fsync(fd)
        finally:
            try:
                os.close(fd)
            finally:
                self.handles._release_writer(inode_key, owner)
        return {"truncated": True}

    def flush(self, handle):
        self.handles.get(handle)
        return {"flushed": True}

    def fsync(self, handle, datasync=False):
        entry = self.handles.get(handle)
        if datasync and hasattr(os, "fdatasync"):
            os.fdatasync(entry["fd"])
        else:
            os.fsync(entry["fd"])
        return {"synced": True}

    def close(self, handle):
        self.handles.close(handle)
        return {"closed": True}

    def mkdir(self, path, mode):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        os.mkdir(target, mode)
        self._fsync_parents(path)
        return {"created": True}

    def rename(self, old_path, new_path):
        old_target = self.exported_root.resolve(old_path, follow_symlinks=False)
        new_target = self.exported_root.resolve(new_path, follow_symlinks=False)
        os.rename(old_target, new_target)
        self._fsync_parents(old_path, new_path)
        return {"renamed": True}

    def unlink(self, path):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        os.unlink(target)
        self._fsync_parents(path)
        return {"removed": True}

    def rmdir(self, path):
        target = self.exported_root.resolve(path, follow_symlinks=False)
        os.rmdir(target)
        self._fsync_parents(path)
        return {"removed": True}

    def fsyncdir(self, path):
        self._fsync_directory_remote(path)
        return {"synced": True}

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
