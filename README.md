# netfs

`netfs` 是一个面向三机环境的远程工作目录工具：

- `device`: Ubuntu 16.04，无 root、无 `sshd`、无 `sshfs`
- `server`: 暴露在公网的跳板机，`<user>@<server>`
- `pc`: Arch Linux，本地可安装依赖，可运行 FUSE

目标是：

- 在 `pc` 上挂载 `device` 的目录
- 在 `pc` 上执行 `device` 上的命令
- 不要求在 `server` 上运行额外服务
- `device` 端只依赖 Python 标准库

## 当前状态

当前已经实现：

- 基于长连接 RPC 的文件访问
- 基于会话的远程命令执行
- FUSE 挂载
- 保守写支持：
  - `create/open/write/truncate/fsync/release`
  - `mkdir/rename/unlink/rmdir/fsyncdir`
  - 单文件单 writer
  - `direct_io`
  - 目录项变更后补目录 `fsync`

明确不支持：

- 多写者并发
- `O_APPEND`
- `mmap`
- 分布式文件锁
- hard link / xattr
- 稀疏文件优化

## 仓库结构

```text
agent/          device 侧入口
client/         pc 侧 CLI 入口
mount/          挂载入口
src/netfs/      核心实现
docs/           协议和部署文档
tests/          本机集成测试和挂载测试
```

## 快速开始

### 1. device 端启动 agent

```bash
python3 agent/device_agent.py --root /path/to/export --host 127.0.0.1 --port 47001
```

### 2. device 到 server 建反向隧道

```bash
ssh -NT \
  -p 22 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -R 47001:127.0.0.1:47001 \
  <user>@<server>
```

### 3. pc 到 server 建本地转发

```bash
ssh -NT \
  -p 22 \
  -o ExitOnForwardFailure=yes \
  -L 47001:127.0.0.1:47001 \
  <user>@<server>
```

### 4. pc 侧使用

```bash
python3 client/netfs_cli.py ping
python3 client/netfs_cli.py ls /
python3 client/netfs_cli.py exec --cwd / -- /bin/uname -a
mkdir -p /tmp/netfs-mount
python3 client/netfs_cli.py mount /tmp/netfs-mount
```

挂载后可以直接操作：

```bash
echo hello >/tmp/netfs-mount/demo.txt
cat /tmp/netfs-mount/demo.txt
mv /tmp/netfs-mount/demo.txt /tmp/netfs-mount/demo2.txt
rm /tmp/netfs-mount/demo2.txt
```

## 开发环境

`pc` 端可以自由使用第三方依赖，当前本机测试使用的是 conda 环境：

```bash
conda create -y -n netfs-dev python=3.11 pip pytest
conda run -n netfs-dev python -m pip install --index-url https://pypi.org/simple fusepy
conda run -n netfs-dev python -m pip install --no-build-isolation -e .
```

## 测试

```bash
conda run -n netfs-dev pytest -q
```

当前本机测试覆盖了：

- RPC 文件读写
- 远程 exec
- 单 writer 约束
- 目录项操作
- FUSE 挂载后的真实读写、`fsync`、`truncate`、`rename`、`unlink`

更多设计说明见 [PLAN.md](PLAN.md)、[docs/DEPLOY.md](docs/DEPLOY.md)、[docs/PROTOCOL.md](docs/PROTOCOL.md)。
