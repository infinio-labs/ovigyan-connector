"""Talking to a terminal directly: check it is reachable and learn what it is."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass


class ProbeError(Exception):
    """A terminal problem described for a school administrator."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # bad_address | unreachable | wrong_password | unsupported
        self.message = message


@dataclass
class DeviceInfo:
    serial: str
    model: str | None
    firmware: str | None


_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _valid_host(text: str) -> bool:
    if not text or "/" in text or any(char.isspace() for char in text):
        return False
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return bool(_HOST.match(text))


def validate_address(host: str, port: int | str) -> tuple[str, int]:
    text = (host or "").strip()
    if not _valid_host(text):
        raise ProbeError("bad_address", "Enter the terminal's IP address, for example 192.168.1.50.")
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise ProbeError("bad_address", "The port must be a number (terminals normally use 4370).") from None
    if not 1 <= number <= 65535:
        raise ProbeError("bad_address", "The port must be between 1 and 65535 (terminals normally use 4370).")
    return text, number


def probe(host: str, port: int = 4370, password: int = 0, timeout: int = 10) -> DeviceInfo:
    """Connect, read the serial number, model and firmware, disconnect. Raises ProbeError."""
    host, port = validate_address(host, port)
    try:
        from zk import ZK
        from zk.exception import ZKError
    except ImportError as error:  # pragma: no cover - the package pins pyzk
        raise ProbeError("unsupported", "A required component (pyzk) is missing from this installation.") from error
    connection = None
    try:
        connection = ZK(host, port=port, timeout=timeout, password=password).connect()
        serial = (connection.get_serialnumber() or "").strip()
        model = _optional(connection.get_device_name)
        firmware = _optional(connection.get_firmware_version)
    except (ZKError, OSError, socket.timeout) as error:
        raise _explain(error, host, port) from None
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:  # noqa: BLE001 - closing a half-open connection is best effort
                pass
    if not serial:
        raise ProbeError("unsupported", "Connected, but this does not look like a supported terminal (no serial number).")
    return DeviceInfo(serial=serial, model=model, firmware=firmware)


def _optional(read) -> str | None:
    try:
        value = read()
    except Exception:  # noqa: BLE001 - model and firmware are nice to have, never required
        return None
    return str(value).strip() or None


def _explain(error: Exception, host: str, port: int) -> ProbeError:
    if "unauth" in str(error).lower():
        return ProbeError(
            "wrong_password",
            "The terminal refused the communication password. Check it on the terminal under Menu > Comm > Comm Key (0 means none).",
        )
    return ProbeError(
        "unreachable",
        f"Can't reach {host}:{port}. Check the IP address, that the terminal is switched on, and that this PC is on the same network.",
    )
