from __future__ import print_function

import subprocess


def reverse_tunnel_command(server, port, remote_port, local_port, ssh_port=22):
    return [
        "ssh",
        "-NT",
        "-p",
        str(ssh_port),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-R",
        "%s:127.0.0.1:%s" % (remote_port, local_port),
        "%s" % server,
    ]


def local_tunnel_command(server, port, local_port, remote_port, ssh_port=22):
    return [
        "ssh",
        "-NT",
        "-p",
        str(ssh_port),
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        "%s:127.0.0.1:%s" % (local_port, remote_port),
        "%s" % server,
    ]


class TunnelProcess(object):
    def __init__(self, argv):
        self.argv = argv
        self.process = None

    def start(self):
        if self.process is not None:
            return
        self.process = subprocess.Popen(self.argv)

    def wait(self):
        if self.process is None:
            return 0
        return self.process.wait()

    def terminate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
