# 部署说明

## device

`device` 端只依赖 Python 标准库，不需要安装第三方包。

在 `device` 上运行：

```bash
python3 agent/device_agent.py --root /path/to/export --host 127.0.0.1 --port 47001
```

建立反向隧道：

```bash
ssh -NT \
  -p 22 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -R 47001:127.0.0.1:47001 \
  <user>@<server>
```

## pc

建立本地转发：

```bash
ssh -NT \
  -p 22 \
  -o ExitOnForwardFailure=yes \
  -L 47001:127.0.0.1:47001 \
  <user>@<server>
```

测试：

```bash
python3 client/netfs_cli.py ping
python3 client/netfs_cli.py ls /
python3 client/netfs_cli.py exec --cwd / -- /bin/uname -a
python3 client/netfs_cli.py mount /tmp/netfs-mount
```
