"""Fake eSSL/ZK terminal: speaks just enough of the ZK TCP protocol for pyzk's get_attendance().

Library:  with FakeZkDevice(records) as dev: ... dev.port ...; dev.records is mutable.
Script:   python tests/fake_zk_device.py --port 4370 --records rows.json
Each record: {"uid": int, "user_id": "42", "timestamp": "2026-09-10T08:00:00", "punch": 0, "status": 1}
"""
from __future__ import annotations

import argparse
import json
import socket
import socketserver
import struct
import threading
from datetime import datetime

CMD_CONNECT, CMD_EXIT, CMD_GET_FREE_SIZES, CMD_FREE_DATA = 1000, 1001, 50, 1502
CMD_OPTIONS_RRQ, CMD_GET_VERSION, CMD_AUTH, CMD_ACK_UNAUTH = 11, 1100, 1102, 2005
CMD_READ_BUFFER, CMD_ACK_OK, CMD_DATA = 1503, 2000, 1501


def encode_time(value: datetime) -> bytes:
    t = ((((value.year - 2000) * 12 + value.month - 1) * 31 + value.day - 1) * 24 + value.hour) * 60 + value.minute
    return struct.pack("<I", t * 60 + value.second)


def _checksum(packet: bytes) -> int:
    total = sum(struct.unpack("<H", packet[i : i + 2])[0] for i in range(0, len(packet) - 1, 2))
    if len(packet) % 2:
        total += packet[-1]
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def _reply(command: int, session: int, reply_id: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<4H", command, 0, session, reply_id) + payload
    body = struct.pack("<4H", command, _checksum(body), session, reply_id) + payload
    return struct.pack("<HHI", 0x5050, 0x7D82, len(body)) + body


def _attendance_blob(records: list[dict]) -> bytes:
    rows = b"".join(
        struct.pack(
            "<H24sB4sB8s",
            int(r["uid"]),
            str(r["user_id"]).encode(),
            int(r.get("status", 1)),
            encode_time(datetime.fromisoformat(r["timestamp"])),
            int(r.get("punch", 0)),
            b"",
        )
        for r in records
    )
    return struct.pack("<I", len(rows)) + rows  # 40-byte rows: pyzk derives size from total/records


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        records = self.server.device.records  # type: ignore[attr-defined]
        while True:
            head = self._read(8)
            if not head:
                return  # pyzk's test_tcp() connects and closes immediately
            size = struct.unpack("<I", head[4:])[0]
            body = self._read(size)
            if not body:
                return
            command, _, session, reply_id = struct.unpack("<4H", body[:8])
            device = self.server.device  # type: ignore[attr-defined]
            if command == CMD_CONNECT:
                # A terminal with a communication password answers "unauthorized" until CMD_AUTH succeeds.
                # The fake never accepts it, which is all the connector's wrong-password path needs.
                code = CMD_ACK_UNAUTH if device.require_password else CMD_ACK_OK
                self.request.sendall(_reply(code, 1, reply_id))
            elif command == CMD_AUTH:
                self.request.sendall(_reply(CMD_ACK_UNAUTH, session, reply_id))
            elif command == CMD_OPTIONS_RRQ:
                name = body[8:].split(b"\x00")[0].decode()
                value = {"~SerialNumber": device.serial, "~DeviceName": device.model}.get(name, "")
                self.request.sendall(_reply(CMD_ACK_OK, session, reply_id, f"{name}={value}".encode() + b"\x00"))
            elif command == CMD_GET_VERSION:
                self.request.sendall(_reply(CMD_ACK_OK, session, reply_id, device.firmware.encode() + b"\x00"))
            elif command == CMD_GET_FREE_SIZES:
                fields = [0] * 20
                fields[8] = len(records)  # records
                self.request.sendall(_reply(CMD_ACK_OK, session, reply_id, struct.pack("<20i", *fields)))
            elif command == CMD_READ_BUFFER:
                self.request.sendall(_reply(CMD_DATA, session, reply_id, _attendance_blob(records)))
            elif command == CMD_EXIT:
                self.request.sendall(_reply(CMD_ACK_OK, session, reply_id))
                return
            else:
                self.request.sendall(_reply(CMD_ACK_OK, session, reply_id))

    def _read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.request.recv(n - len(data))
            if not chunk:
                return b""
            data += chunk
        return data


class FakeZkDevice(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        records: list[dict] | None = None,
        port: int = 0,
        host: str = "127.0.0.1",
        serial: str = "FAKE0001",
        model: str = "X2008",
        firmware: str = "Ver 6.60 Jan 1 2024",
        require_password: bool = False,
    ):
        super().__init__((host, port), _Handler)
        self.device = self
        self.records = records if records is not None else []
        self.serial, self.model, self.firmware, self.require_password = serial, model, firmware, require_password
        self.port = self.server_address[1]

    def __enter__(self) -> "FakeZkDevice":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
        self.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4370)
    parser.add_argument("--records", required=True)
    args = parser.parse_args()
    with open(args.records, encoding="utf-8") as handle:
        device = FakeZkDevice(json.load(handle), args.port)
    print(f"fake ZK device on 127.0.0.1:{device.port}", flush=True)
    device.serve_forever()
