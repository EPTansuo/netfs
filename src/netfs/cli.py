from __future__ import print_function

import argparse
import json
import os
import signal
import sys
import time

from netfs.fusefs import mount_foreground
from netfs.rpc_client import RpcClient
from netfs.tunnel import local_tunnel_command, reverse_tunnel_command


def build_parser():
    parser = argparse.ArgumentParser(description="netfs client CLI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47001)

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    subparsers.add_parser("ping")

    ls_parser = subparsers.add_parser("ls")
    ls_parser.add_argument("path")

    stat_parser = subparsers.add_parser("stat")
    stat_parser.add_argument("path")
    stat_parser.add_argument("--nofollow", action="store_true")

    cat_parser = subparsers.add_parser("cat")
    cat_parser.add_argument("path")
    cat_parser.add_argument("--chunk-size", type=int, default=131072)

    exec_parser = subparsers.add_parser("exec")
    exec_parser.add_argument("--cwd", default="/")
    exec_parser.add_argument("--env", action="append", default=[])
    exec_parser.add_argument("--input", default=None)
    exec_parser.add_argument("argv", nargs=argparse.REMAINDER)

    mount_parser = subparsers.add_parser("mount")
    mount_parser.add_argument("mountpoint")
    mount_parser.add_argument("--remote-root", default="/")
    mount_parser.add_argument("--allow-other", action="store_true")

    tunnel_parser = subparsers.add_parser("tunnel")
    tunnel_sub = tunnel_parser.add_subparsers(dest="tunnel_command")
    tunnel_sub.required = True

    reverse_parser = tunnel_sub.add_parser("reverse")
    reverse_parser.add_argument("--server", required=True)
    reverse_parser.add_argument("--ssh-port", type=int, default=22)
    reverse_parser.add_argument("--remote-port", type=int, default=47001)
    reverse_parser.add_argument("--local-port", type=int, default=47001)

    local_parser = tunnel_sub.add_parser("local")
    local_parser.add_argument("--server", required=True)
    local_parser.add_argument("--ssh-port", type=int, default=22)
    local_parser.add_argument("--remote-port", type=int, default=47001)
    local_parser.add_argument("--local-port", type=int, default=47001)
    return parser


def _client_from_args(args):
    client = RpcClient(args.host, args.port)
    client.connect()
    return client


def _env_dict(env_items):
    env = {}
    for item in env_items:
        if "=" not in item:
            raise SystemExit("invalid env assignment: %s" % item)
        key, value = item.split("=", 1)
        env[key] = value
    return env


def _stream_exec_output(client, session_id):
    stdout_eof = False
    stderr_eof = False
    while not (stdout_eof and stderr_eof):
        if not stdout_eof:
            data, stdout_eof = client.exec_read_stdout(session_id, wait=True, timeout=0.2)
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        if not stderr_eof:
            data, stderr_eof = client.exec_read_stderr(session_id, wait=False, timeout=0.0)
            if data:
                sys.stderr.buffer.write(data)
                sys.stderr.buffer.flush()
        if not (stdout_eof and stderr_eof):
            snapshot = client.exec_poll(session_id)
            if snapshot["returncode"] is not None and snapshot["stdout_buffered"] == 0 and snapshot["stderr_buffered"] == 0:
                time.sleep(0.05)


def run(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "tunnel":
        if args.tunnel_command == "reverse":
            command = reverse_tunnel_command(
                server=args.server,
                port=args.remote_port,
                remote_port=args.remote_port,
                local_port=args.local_port,
                ssh_port=args.ssh_port,
            )
        else:
            command = local_tunnel_command(
                server=args.server,
                port=args.remote_port,
                local_port=args.local_port,
                remote_port=args.remote_port,
                ssh_port=args.ssh_port,
            )
        print(" ".join(command))
        return 0

    client = _client_from_args(args)
    try:
        if args.command == "ping":
            print(json.dumps(client.ping(), sort_keys=True))
            return 0

        if args.command == "ls":
            for entry in client.readdir(args.path):
                print(entry["name"])
            return 0

        if args.command == "stat":
            result = client.stat(args.path, follow_symlinks=not args.nofollow)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.command == "cat":
            handle = client.open(args.path, os.O_RDONLY)
            try:
                offset = 0
                while True:
                    data, eof = client.read(handle, offset, args.chunk_size)
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        offset += len(data)
                    if eof:
                        break
            finally:
                client.close_handle(handle)
            return 0

        if args.command == "exec":
            command = list(args.argv)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise SystemExit("usage: exec -- <argv...>")
            input_mode = "pipe" if args.input else "closed"
            session_id = client.exec_start(
                command,
                cwd=args.cwd,
                env=_env_dict(args.env),
                stdin_mode=input_mode,
            )
            try:
                if args.input:
                    with open(args.input, "rb") as infile:
                        while True:
                            chunk = infile.read(65536)
                            if not chunk:
                                break
                            client.exec_write_stdin(session_id, chunk)
                    client.exec_close_stdin(session_id)
                _stream_exec_output(client, session_id)
                wait_result = client.exec_wait(session_id)
                return wait_result["returncode"]
            finally:
                try:
                    client.exec_close(session_id)
                except Exception:
                    pass

        if args.command == "mount":
            mount_foreground(
                host=args.host,
                port=args.port,
                mountpoint=args.mountpoint,
                remote_root=args.remote_root,
                allow_other=args.allow_other,
            )
            return 0
    finally:
        client.close()
    return 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()
