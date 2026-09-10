from __future__ import annotations

from typing import Any, Protocol

from .events import EventEnvelope, from_zk_row


class DeviceAdapter(Protocol):
    name: str

    def read_events(self) -> list[EventEnvelope]: ...


class EsslX2008Adapter:
    name = "essl-x2008-zk-tcp"

    def __init__(self, host: str, port: int, password: int, timeout: int, timezone_name: str):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.timezone_name = timezone_name

    def read_events(self) -> list[EventEnvelope]:
        try:
            from zk import ZK
        except ImportError as error:
            raise RuntimeError("Install connector dependencies: python -m pip install -e .") from error
        device = ZK(self.host, port=self.port, timeout=self.timeout, password=self.password)
        connection = None
        try:
            connection = device.connect()
            return [
                from_zk_row(row, self.timezone_name)
                for row in (connection.get_attendance() or [])
                if str(getattr(row, "user_id", "")).strip()
            ]
        finally:
            if connection is not None:
                connection.disconnect()
