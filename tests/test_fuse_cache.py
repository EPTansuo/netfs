from __future__ import print_function

from netfs.fusefs import NetfsFuseOperations


class FakeClient(object):
    def __init__(self):
        self.readdir_calls = 0
        self.lstat_calls = 0

    def readdir(self, path):
        self.readdir_calls += 1
        return [
            {
                "name": "hello.txt",
                "stat": {
                    "st_mode": 33188,
                    "st_ino": 11,
                    "st_dev": 1,
                    "st_nlink": 1,
                    "st_uid": 1000,
                    "st_gid": 1000,
                    "st_size": 5,
                    "st_atime_ns": 1,
                    "st_mtime_ns": 2,
                    "st_ctime_ns": 3,
                },
            }
        ]

    def lstat(self, path):
        self.lstat_calls += 1
        raise AssertionError("getattr should hit metadata cache before calling lstat")


def test_readdir_populates_metadata_cache():
    client = FakeClient()
    operations = NetfsFuseOperations(client, remote_root="/")

    entries = list(operations.readdir("/", None))
    assert entries == [".", "..", "hello.txt"]
    stat_result = operations.getattr("/hello.txt")

    assert stat_result["st_size"] == 5
    assert client.readdir_calls == 1
    assert client.lstat_calls == 0
