from __future__ import print_function

import os
import subprocess
import sys
import time

import pytest


pytest.importorskip("fuse")


def _wait_for_mount(mountpoint, expected):
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            if sorted(os.listdir(mountpoint)) == expected:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("mount did not become ready")


def test_mount_read_only(agent, tmp_path):
    mountpoint = tmp_path / "mnt"
    mountpoint.mkdir()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "netfs.cli",
            "--host",
            "127.0.0.1",
            "--port",
            str(agent),
            "mount",
            str(mountpoint),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_mount(str(mountpoint), ["hello.link", "hello.txt", "subdir"])
        with open(str(mountpoint / "hello.txt"), "rb") as infile:
            assert infile.read() == b"hello from netfs\n"
        with pytest.raises(OSError):
            open(str(mountpoint / "new.txt"), "wb")
    finally:
        subprocess.check_call(["fusermount3", "-u", str(mountpoint)])
        proc.terminate()
        proc.wait(timeout=10)
