from __future__ import print_function

import json
import struct


HEADER_STRUCT = struct.Struct("!I")


def _read_exact(reader, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            raise EOFError("unexpected end of stream")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_frame(writer, header, payload=None):
    payload = payload or b""
    header = dict(header)
    header["payload_len"] = len(payload)
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    writer.write(HEADER_STRUCT.pack(len(encoded)))
    writer.write(encoded)
    if payload:
        writer.write(payload)
    writer.flush()


def read_frame(reader):
    header_len = HEADER_STRUCT.unpack(_read_exact(reader, HEADER_STRUCT.size))[0]
    header = json.loads(_read_exact(reader, header_len).decode("utf-8"))
    payload_len = int(header.get("payload_len", 0))
    payload = b""
    if payload_len:
        payload = _read_exact(reader, payload_len)
    return header, payload
