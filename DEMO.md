先给你一个最小可跑通的 exec + 文件读取原型。先把链路打通，挂载再接上。

1) device 上的 agent
#!/usr/bin/env python3
# device_agent.py
from __future__ import print_function
import argparse
import base64
import json
import os
import socketserver
import subprocess

ROOT = None

def b64(bs):
    return base64.b64encode(bs).decode("ascii")

def resolve_path(relpath):
    relpath = relpath or "/"
    if not relpath.startswith("/"):
        relpath = "/" + relpath
    root = os.path.realpath(ROOT)
    target = os.path.realpath(os.path.join(root, relpath.lstrip("/")))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("path escapes exported root")
    return target

def op_ping(req):
    return {"pong": True}

def op_stat(req):
    p = resolve_path(req["path"])
    st = os.lstat(p)
    return {
        "mode": st.st_mode,
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "uid": st.st_uid,
        "gid": st.st_gid,
    }

def op_listdir(req):
    p = resolve_path(req.get("path", "/"))
    out = []
    for name in sorted(os.listdir(p)):
        full = os.path.join(p, name)
        st = os.lstat(full)
        out.append({
            "name": name,
            "mode": st.st_mode,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        })
    return {"entries": out}

def op_readfile(req):
    p = resolve_path(req["path"])
    offset = int(req.get("offset", 0))
    size = int(req.get("size", 131072))
    with open(p, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    return {
        "data_b64": b64(data),
        "eof": len(data) < size,
    }

def op_exec(req):
    argv = req["argv"]
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv must be a non-empty list")
    cwd = resolve_path(req.get("cwd", "/"))
    env = os.environ.copy()
    env.update(req.get("env", {}))
    p = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = p.communicate()
    return {
        "code": p.returncode,
        "stdout_b64": b64(out),
        "stderr_b64": b64(err),
    }

OPS = {
    "ping": op_ping,
    "stat": op_stat,
    "listdir": op_listdir,
    "readfile": op_readfile,
    "exec": op_exec,
}

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw.decode("utf-8"))
                op = req["op"]
                if op not in OPS:
                    raise ValueError("unknown op: %s" % op)
                resp = {"ok": True}
                resp.update(OPS[op](req))
            except Exception as e:
                resp = {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}
            self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
            self.wfile.flush()

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="exported root dir")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47001)
    args = ap.parse_args()

    ROOT = os.path.realpath(args.root)
    srv = ThreadedTCPServer((args.host, args.port), Handler)
    print("serving", ROOT, "on", args.host, args.port)
    srv.serve_forever()

if __name__ == "__main__":
    main()

这个 agent 只用 Python 标准库，适合你这种老 Ubuntu 机器先跑通。它把 device 上某个目录当“导出根目录”，支持 exec/stat/listdir/readfile。exec 是以 device 当前用户 权限执行的。

2) PC 上的测试客户端
#!/usr/bin/env python3
# pc_client.py
from __future__ import print_function
import argparse
import base64
import json
import socket
import sys

def rpc(host, port, req):
    s = socket.create_connection((host, port), timeout=10)
    f = s.makefile("rwb")
    f.write((json.dumps(req) + "\n").encode("utf-8"))
    f.flush()
    line = f.readline()
    if not line:
        raise SystemExit("no response")
    resp = json.loads(line.decode("utf-8"))
    if not resp.get("ok"):
        raise SystemExit(resp.get("error", "unknown error"))
    return resp

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47001)

    sub = ap.add_subparsers(dest="cmd")
    sub.required = True

    p = sub.add_parser("ping")

    p = sub.add_parser("ls")
    p.add_argument("path")

    p = sub.add_parser("stat")
    p.add_argument("path")

    p = sub.add_parser("cat")
    p.add_argument("path")

    p = sub.add_parser("exec")
    p.add_argument("--cwd", default="/")
    p.add_argument("argv", nargs=argparse.REMAINDER)

    args = ap.parse_args()

    if args.cmd == "ping":
        print(rpc(args.host, args.port, {"op": "ping"}))

    elif args.cmd == "ls":
        r = rpc(args.host, args.port, {"op": "listdir", "path": args.path})
        for e in r["entries"]:
            print(e["name"])

    elif args.cmd == "stat":
        r = rpc(args.host, args.port, {"op": "stat", "path": args.path})
        print(json.dumps(r, indent=2, sort_keys=True))

    elif args.cmd == "cat":
        r = rpc(args.host, args.port, {
            "op": "readfile",
            "path": args.path,
            "offset": 0,
            "size": 1024 * 1024,
        })
        sys.stdout.buffer.write(base64.b64decode(r["data_b64"]))

    elif args.cmd == "exec":
        argv = args.argv
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            raise SystemExit("usage: exec -- <argv...>")
        r = rpc(args.host, args.port, {
            "op": "exec",
            "cwd": args.cwd,
            "argv": argv,
        })
        sys.stdout.buffer.write(base64.b64decode(r["stdout_b64"]))
        sys.stderr.buffer.write(base64.b64decode(r["stderr_b64"]))
        raise SystemExit(r["code"])

if __name__ == "__main__":
    main()
3) 先在 device 上把 SSH 首次连接确认掉

你刚才是因为在 host key 提示处按了 Ctrl-C，所以连接根本没建立。先做一次：

ssh <user>@<server>

核对指纹没问题就输入 yes。
想自动加新主机的话，可以试：

ssh -o StrictHostKeyChecking=accept-new <user>@<server>

第一次 host key 确认本来就是 OpenSSH 的正常行为；accept-new 会自动接受“新主机 key”，但仍拒绝“已知主机 key 变化”。

4) device 上启动 agent，然后反向转发到 server

先跑 agent：

`device_agent.py` 的 `--port` 参数用于指定 agent 监听端口，默认值是 `47001`。

python3 device_agent.py --root /home/your_user/EXPORT_DIR --host 127.0.0.1 --port 47001

再开第二个终端，跑反向隧道：

ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -R 47001:127.0.0.1:47001 \
  <user>@<server>

-N 是“不执行远程命令，只做转发”；ExitOnForwardFailure=yes 能保证端口没转成功就直接报错；ServerAliveInterval/CountMax 用来让隧道在网络抖动后更容易发现断线。

5) PC 上把 server 回环端口再拉回本机
ssh -NT \
  -o ExitOnForwardFailure=yes \
  -L 47001:127.0.0.1:47001 \
  <user>@<server>

现在 PC 上的 `127.0.0.1:47001` 就通到 device 的 agent 了。这里之所以还要再做一次 `-L`，是因为 `-R` 在 server 侧默认只绑定 loopback；这反而更安全，不用把 agent 端口暴露给公网。只有你想让别的机器直接连 server 上那个远程端口时，才需要改 `GatewayPorts`。

6) 在 PC 上测试
python3 pc_client.py ping
python3 pc_client.py ls /
python3 pc_client.py stat /some/file
python3 pc_client.py cat /some/file.txt
python3 pc_client.py exec --cwd / -- /bin/uname -a
python3 pc_client.py exec --cwd /project -- /bin/bash -lc 'pwd && ls'

到这一步，“PC 让 device 执行命令”已经成立了。而且文件访问也已经有了 listdir/stat/readfile。
