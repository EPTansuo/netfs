# netfs 开发规划

## 1. 目标

在以下前提下，提供一个可长期使用的远程目录访问与远程执行方案：

- `pc`: Arch Linux，可安装本地依赖，可运行 FUSE 挂载程序。
- `server`: 公网可达跳板机，账号为 `<user>@<server>`。
- `device`: Ubuntu 16.04，无 root 权限，无 `sshd`，无 `sshfs`，但可以主动通过 `ssh` 连接 `server`。

目标能力：

- 在 `pc` 上挂载 `device` 的某个目录。
- 在 `pc` 上执行 `device` 上的命令。
- 不要求在 `server` 上部署额外守护进程。
- 不依赖 `device` 上的系统级安装权限。

## 2. 设计约束

- `device` 不能被动接受连接，只能主动连 `server`。
- `device` 环境老旧，首要目标是兼容 Python 3.5/3.6 这一类可用环境。
- `server` 应尽量保持 dumb relay，不承担协议解析、文件缓存或会话管理。
- `pc` 才是主要控制面，挂载、缓存、错误处理、CLI 都应落在 `pc`。
- 第一版先追求可用性和稳定性，不追求极限吞吐。

## 3. 技术选型

结论：**第一版继续使用 Python 实现，不切到 C/C++。**

原因：

1. `device` 是最难部署的一端，Python 最容易落地。
2. 当前主要问题是协议、会话、断线恢复和 FUSE 语义，不是算力瓶颈。
3. 用 Python 可以先把产品边界和协议做对，再根据性能瓶颈做局部重写。
4. 如果后续性能不足，最值得单独重写的是 `pc` 侧 FUSE daemon，而不是整套系统。

建议语言分工：

- `device agent`: Python
- `pc daemon`: Python
- `pc CLI`: Python
- `FUSE 挂载层`: Python 起步；确认瓶颈后再考虑迁移到 C++/libfuse3

## 4. 总体架构

### 4.1 节点职责

#### `device`

- 运行 `agent`
- 暴露文件访问 RPC
- 暴露命令执行 RPC
- 主动建立到 `server` 的反向 SSH 隧道

#### `server`

- 仅作为 SSH relay
- 不保存业务状态
- 不处理文件协议

#### `pc`

- 运行本地 `netfsd`
- 维护到 `device agent` 的逻辑连接
- 提供 FUSE 挂载
- 提供 `netfs exec` CLI
- 负责缓存、重试、错误映射、日志

### 4.2 连接路径

1. `device` 本地启动 `agent`，监听 `127.0.0.1:<agent_port>`
2. `device -> server` 建立 `ssh -R`
3. `pc -> server` 建立 `ssh -L`
4. `pc` 本地 `netfsd` 通过本地回环端口连接到 `device agent`

这样 `device` 无需 `sshd`，也不会把 agent 暴露到公网。

## 5. 组件划分

建议仓库结构：

```text
agent/
  device_agent.py
  tunnel.py
  exec_session.py
  fs_ops.py

client/
  netfs_cli.py
  rpc_client.py
  tunnel_manager.py

mount/
  fuse_main.py
  inode_cache.py
  read_cache.py

proto/
  schema.py
  codec.py
  errors.py

docs/
  DEMO.md
  PLAN.md
  DEPLOY.md
  PROTOCOL.md
```

## 6. 协议规划

当前 `DEMO.md` 已经验证了最小可行链路，但正式实现需要把协议做完整。

### 6.1 传输层

第一版建议：

- 单 TCP 长连接
- 长度前缀帧，而不是按行 JSON
- 请求/响应带 `request_id`
- 支持并发中的多路复用
- 二进制负载直接传，不再统一用 base64 包裹

编码建议：

- 首选 `msgpack`，前提是 `device` 端可安装
- 若 `device` 端不适合安装依赖，则退回：
  - 控制消息：JSON
  - 数据块：长度前缀原始 bytes

### 6.2 文件 RPC

第一版按“保守写支持”设计，读写分层如下。

基础读取：

- `ping`
- `stat`
- `lstat`
- `readdir`
- `readlink`
- `open`
- `read`
- `close`
- `statfs`
- `access`

写入与目录项：

- `create`
- `write`
- `flush`
- `fsync`
- `truncate`
- `mkdir`
- `rename`
- `unlink`
- `rmdir`
- `fsyncdir`

### 6.3 写路径策略

首版写支持遵循以下约束：

1. 挂载侧开启 `direct_io`，关闭 `kernel_cache` 和 `writeback_cache`。
2. 属性和目录项缓存时间设短，不依赖内核长期缓存。
3. 单文件同一时刻只允许一个 writer；第二个 writer 直接返回 `EBUSY`。
4. 每个 `open/create` 在 `device` 上都对应一个真实 fd / handle。
5. `write` 直接映射为 `device` 上的 `pwrite`。
6. 真正的提交点是 `fsync`，不是 `flush`。
7. 如果写请求超时或链路断开，该 handle 会被标记为 uncertain，后续直接返回 `EIO`，不做盲目重放。

### 6.4 v1 支持边界

v1 支持：

- 读：`getattr / readdir / open / read / release`
- 写：`create / open / write / truncate / fsync / release`
- 目录项：`mkdir / rename / unlink / rmdir / fsyncdir`

v1 明确不支持：

- 多写者并发
- `O_APPEND`
- `mmap`
- 分布式文件锁
- hard link / xattr
- 稀疏文件优化

### 6.5 exec RPC

正式实现不能只有一次性 `exec`，需要会话化：

- `exec_start`
- `exec_poll`
- `exec_read_stdout`
- `exec_read_stderr`
- `exec_write_stdin`
- `exec_signal`
- `exec_wait`
- `exec_close`

可选能力：

- `pty_start`
- `resize_pty`

## 7. v1 范围

`v1` 定义为一个可以稳定使用的远程工作目录版本：支持保守的读写挂载和远程执行。

### 7.1 必须实现

- `pc` 上可以挂载 `device` 指定目录
- 挂载默认可写
- `pc` 上可以执行 `device` 命令
- 支持长时间运行命令
- 支持流式读取 stdout/stderr
- 支持 `cwd` 和自定义环境变量
- 支持 SSH 隧道保活
- 支持基本的自动重连
- 写路径采用 `direct_io`
- 单文件单 writer
- `fsync` 和 `fsyncdir` 提供提交点
- 目录项操作支持 `mkdir/rename/unlink/rmdir`

### 7.2 明确不做

- 文件锁语义
- `mmap`
- 多用户权限隔离
- server 端守护进程
- Windows/macOS 客户端适配
- `O_APPEND`
- hard link / xattr
- 稀疏文件优化

## 8. 实施阶段

### Phase 0: 仓库整理

目标：

- 把 demo 代码从文档中拆出来
- 建立基础目录结构
- 增加最小运行脚本和部署文档

产出：

- `agent/device_agent.py`
- `client/netfs_cli.py`
- `docs/DEPLOY.md`

### Phase 1: 正规化协议与连接管理

目标：

- 从 demo 的“单请求单连接”升级为长连接协议
- 引入 `request_id`
- 引入错误码体系
- 增加心跳和超时控制

关键点：

- `device` 侧 agent 连接可长期驻留
- `pc` 侧断线能尽快识别
- RPC 错误能稳定映射到 CLI 和 FUSE 层

### Phase 2: 文件系统读路径

目标：

- 在 `pc` 上实现稳定的 FUSE 读挂载

功能：

- `getattr`
- `readdir`
- `open/read/release`
- `readlink`
- `statfs`

关键点：

- 正确处理 inode / mode / mtime / symlink
- 对目录项和属性做短期缓存
- 顺序读做预取

### Phase 3: 完整 exec 会话

目标：

- 提供可靠的远程命令执行

功能：

- 一次性执行
- 长命令执行
- stdout/stderr 分离
- stdin 写入
- 退出码获取
- signal 转发

关键点：

- 会话生命周期清晰
- 防止僵尸进程
- 支持用户手动中断

### Phase 4: 稳定性与可观测性

目标：

- 让系统能长时间挂着用

功能：

- 自动重连
- 隧道健康检查
- session 恢复策略
- 本地日志
- 调试命令

关键点：

- FUSE 层在断线时返回合理错误，而不是卡死
- exec 会话断线后的行为要有明确策略

### Phase 5: 保守写支持

目标：

- 在读路径稳定后，增加保守写能力和目录项修改

说明：

关键点：

- `direct_io` 下的 `write` 语义必须直接对应远端 `pwrite`
- 单文件单 writer
- `fsync` 负责文件落盘，目录项操作补 `fsyncdir`
- 断线后 writable handle 进入 uncertain 状态，不做写请求重放

## 9. 关键设计决定

### 9.1 为什么 server 不跑业务服务

- 降低运维复杂度
- 避免把 server 变成单点状态机
- 更符合当前已有条件：只有 SSH 是确定可用的

### 9.2 为什么 v1 的写支持要非常保守

- FUSE 写路径比读路径复杂很多
- 崩溃恢复、缓存一致性、错误语义都会复杂一个量级
- 先把“单 writer + direct_io + fsync 提交点”做对，优先保证语义可靠

### 9.3 为什么 exec 要做成会话

- 一次性 `subprocess.communicate()` 不适合长命令
- 无法流式输出
- 无法处理中断、超时、stdin 和后台进程

## 10. 风险与应对

### 风险 1: `device` Python 环境过旧或缺模块

应对：

- 代码兼容 Python 3.5+
- 首版尽量只依赖标准库
- 第三方依赖限定在 `pc` 侧

### 风险 2: Python FUSE 性能不够

应对：

- v1 先做正确性
- 在 `pc` 侧增加属性缓存、目录缓存、顺序读预取
- 若确认瓶颈在 FUSE 层，再单独用 C++ 重写 `mount/`

### 风险 3: SSH 隧道不稳定

应对：

- 使用 `ServerAliveInterval` 与 `ServerAliveCountMax`
- `pc` 侧和 `device` 侧都提供保活与重连逻辑
- CLI 提供状态检查命令

### 风险 4: FUSE 阻塞影响用户体验

应对：

- 所有 RPC 设置超时
- FUSE 层避免无限等待
- 对易阻塞操作增加缓存和快速失败策略

## 11. 里程碑定义

### Milestone A: demo 工程化

验收标准：

- demo 中的 agent/client 从文档变成可运行代码
- 可通过 SSH 隧道打通 `ping/ls/read/exec`

### Milestone B: v1 alpha

验收标准：

- `pc` 上能挂载 `device` 目录
- `netfs exec` 可稳定执行命令
- 断线时能报出明确错误

### Milestone C: v1 beta

验收标准：

- 基本缓存和重连完成
- 长时间使用不易卡死
- 文档齐全，可重复部署

### Milestone D: v1 release

验收标准：

- 读写挂载稳定
- exec 会话稳定
- 部署脚本和排障文档齐备

## 12. 近期开发顺序建议

建议按下面顺序做：

1. 把 demo 代码落盘成工程文件
2. 把 RPC 协议改成长连接 + 请求编号
3. 完成 `netfs exec`
4. 完成 FUSE 读挂载
5. 完成保守写支持
6. 加缓存和重连

## 13. 最终建议

当前项目最合理的路线是：

- **先用 Python 把完整产品做对**
- **先交付保守写语义下的挂载 + 远程执行**
- **只有在 `pc` 侧 FUSE 明确成为瓶颈时，才局部迁移到 C/C++**

这条路线最符合当前三台机器的限制条件，也最容易尽快得到一个能长期使用的版本。
