# 协议说明

## 传输

- 单 TCP 长连接
- 每个消息都有 `request_id`
- 帧格式为：
  1. 4 字节大端整数，表示 JSON 头长度
  2. JSON 头
  3. 可选二进制 payload

JSON 头里的 `payload_len` 描述后续原始字节长度。

## 请求格式

```json
{
  "type": "request",
  "id": 1,
  "op": "read",
  "params": {
    "handle": 10,
    "offset": 0,
    "size": 65536
  },
  "payload_len": 0
}
```

## 响应格式

```json
{
  "type": "response",
  "id": 1,
  "ok": true,
  "result": {
    "size": 12,
    "eof": false
  },
  "error": null,
  "payload_len": 12
}
```

## 已实现操作

### 文件和目录

- `ping`
- `stat`
- `lstat`
- `access`
- `readlink`
- `readdir`
- `open`
- `create`
- `read`
- `write`
- `truncate`
- `flush`
- `fsync`
- `close`
- `mkdir`
- `rename`
- `unlink`
- `rmdir`
- `fsyncdir`
- `statfs`

## 写语义

- 每个 `open/create` 在 `device` 上都对应一个真实 fd
- `write` 直接映射为远端 `pwrite`
- 不支持 `O_APPEND`
- 单文件同一时刻只允许一个 writer；第二个 writer 返回 `EBUSY`
- `flush` 不是提交点，真正的提交点是 `fsync`
- 如果链路在写操作期间断开，挂载侧会把相关 handle 标成 uncertain，后续对该 handle 返回 `EIO`

### exec

- `exec_start`
- `exec_poll`
- `exec_read_stdout`
- `exec_read_stderr`
- `exec_write_stdin`
- `exec_close_stdin`
- `exec_wait`
- `exec_signal`
- `exec_close`
