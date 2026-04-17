# 部署说明

## device

`device` 端只依赖 Python 标准库，不需要安装第三方包。

默认 RPC 端口是 `47001`，默认 SSH 端口是 `22`。

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

`pc` 端建议使用独立环境安装开发和挂载依赖，例如：

```bash
conda create -y -n netfs-dev python=3.11 pip pytest
conda run -n netfs-dev python -m pip install --index-url https://pypi.org/simple fusepy
conda run -n netfs-dev python -m pip install --no-build-isolation -e .
```

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
mkdir -p /tmp/netfs-mount
python3 client/netfs_cli.py mount /tmp/netfs-mount
```

挂载默认是可写挂载，采用保守写语义：

- `direct_io`
- 关闭 `kernel_cache`
- 单文件同一时刻只允许一个 writer
- `fsync` 作为文件提交点
- `mkdir/rename/unlink/rmdir` 后会补父目录 `fsync`

可以直接在挂载点做读写：

```bash
echo hello >/tmp/netfs-mount/new.txt
python3 client/netfs_cli.py cat /new.txt
mv /tmp/netfs-mount/new.txt /tmp/netfs-mount/renamed.txt
rm /tmp/netfs-mount/renamed.txt
```

## 本机测试

```bash
conda run -n netfs-dev pytest -q
```
