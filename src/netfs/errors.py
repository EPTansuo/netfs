from __future__ import print_function

import errno


class RpcError(Exception):
    def __init__(self, code, message, remote_errno=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.remote_errno = remote_errno

    def to_exception(self):
        if self.remote_errno is not None:
            return OSError(self.remote_errno, self.message)
        return self


def error_payload_from_exception(exc):
    remote_errno = getattr(exc, "errno", None)
    message = str(exc) or exc.__class__.__name__
    if remote_errno is None and isinstance(exc, ValueError):
        remote_errno = errno.EINVAL
    if remote_errno is None:
        remote_errno = errno.EIO
    return {
        "code": exc.__class__.__name__,
        "message": message,
        "errno": remote_errno,
    }
