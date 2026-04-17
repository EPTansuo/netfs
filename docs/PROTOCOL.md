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

### 文件

- `ping`
- `stat`
- `lstat`
- `access`
- `readlink`
- `readdir`
- `open`
- `read`
- `close`
- `statfs`

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
