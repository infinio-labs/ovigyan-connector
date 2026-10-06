"""The connector's side of the Ovigyan connector API: pair, report terminals, fetch configuration."""
from __future__ import annotations

import http.client
import json
import os
import platform
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from . import __version__
from .branding import parse_branding
from .updater import Policy

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class CloudError(Exception):
    """A failure with a message fit to show a school administrator, and a kind the caller can branch on."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind  # unreachable | invalid_key | expired | unauthorized | rate_limited | server | bad_response
        self.message = message
        self.status = status


def normalize_server_url(value: str) -> str:
    """Turn what a person types ("school.example.com", "https://school.example.com/login") into the site's origin."""
    text = (value or "").strip()
    if not text:
        raise CloudError("bad_address", "Enter the web address of your Ovigyan site.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = parsed.hostname
    if not host or parsed.username or parsed.password:
        raise CloudError("bad_address", "That does not look like a web address.")
    insecure_ok = host in LOCAL_HOSTS and (
        os.environ.get("OVIGYAN_ALLOW_INSECURE_HTTP", "").strip().lower() == "true"
        or os.environ.get("ATTENDANCE_ALLOW_INSECURE_HTTP", "").strip().lower() == "true"
    )
    if parsed.scheme != "https" and not (parsed.scheme == "http" and insecure_ok):
        raise CloudError("bad_address", "The address must start with https:// (a secure connection).")
    try:
        port = parsed.port
    except ValueError:
        raise CloudError("bad_address", "That does not look like a web address.") from None
    netloc = f"{host}:{port}" if port else host
    return f"{parsed.scheme}://{netloc}"


def _os_name() -> str:
    return {"darwin": "macos"}.get(platform.system().lower(), platform.system().lower())


@dataclass
class PairResult:
    connector_id: str
    name: str
    credential: str
    poll_seconds: int
    branding: dict | None = None


@dataclass
class ConfigDevice:
    serial: str
    name: str
    timezone: str
    ignore_before: datetime | None
    enabled: bool


@dataclass
class CloudConfig:
    poll_seconds: int = 60
    devices: dict[str, ConfigDevice] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    rejected: set[str] = field(default_factory=set)
    branding: dict | None = None  # None: the site did not send a block at all
    update: Policy | None = None  # None: an older site that has no opinion on versions


def _parse_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_policy(raw: object) -> Policy | None:
    if not isinstance(raw, dict):
        return None
    text = lambda key: raw.get(key) if isinstance(raw.get(key), str) else None  # noqa: E731
    return Policy(target=text("target"), minimum=text("minimum"), auto=raw.get("auto") is not False)


class CloudClient:
    def __init__(
        self,
        server: str,
        credential: str | None = None,
        timeout: int = 30,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
    ):
        self.server = server.rstrip("/")
        self.credential = credential
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    def _request(self, method: str, path: str, body: dict | None = None, authenticated: bool = True) -> dict:
        headers = {"accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["content-type"] = "application/json"
        if authenticated:
            if not self.credential:
                raise CloudError("unauthorized", "This connector is not paired yet.")
            headers["authorization"] = f"Bearer {self.credential}"
            headers["x-connector-version"] = __version__
            headers["x-connector-os"] = _os_name()
            headers["x-connector-hostname"] = socket.gethostname()
        request = urllib.request.Request(self.server + path, data=data, method=method, headers=headers)
        last: CloudError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    parsed = json.loads(response.read() or b"{}")
                if not isinstance(parsed, dict):
                    raise CloudError("bad_response", "The server sent an unexpected answer.")
                return parsed
            except urllib.error.HTTPError as error:
                mapped = self._map_http_error(error)
                if mapped.kind != "server":
                    raise mapped from None
                last = mapped
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError, OSError):
                last = CloudError(
                    "unreachable",
                    f"Can't reach {self.server}. Check this PC's internet connection and the address.",
                )
            except ValueError:
                raise CloudError("bad_response", "The server sent an unexpected answer.") from None
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * (2**attempt))
        assert last is not None
        raise last

    @staticmethod
    def _map_http_error(error: urllib.error.HTTPError) -> CloudError:
        try:
            detail = json.loads(error.read()).get("error", "")
        except (ValueError, AttributeError, OSError):
            detail = ""
        detail = detail if isinstance(detail, str) else ""
        if error.code == 401:
            return CloudError("unauthorized", "The school's Ovigyan site no longer accepts this connector. Pair it again.", 401)
        if error.code == 410:
            return CloudError("expired", detail or "This key has expired. Generate a new one in Settings.", 410)
        if error.code == 429:
            return CloudError("rate_limited", detail or "Too many attempts. Wait a minute and try again.", 429)
        if error.code == 400:
            return CloudError("invalid_key", detail or "That key is not valid.", 400)
        if error.code == 404:
            return CloudError("bad_address", "That address is not an Ovigyan site, or it is too old for this connector.", 404)
        return CloudError("server", detail or f"The server had a problem (HTTP {error.code}).", error.code)

    def pair(self, key: str) -> PairResult:
        result = self._request(
            "POST",
            "/api/connectors/pair",
            {"key": key, "hostname": socket.gethostname(), "os": _os_name(), "version": __version__},
            authenticated=False,
        )
        try:
            return PairResult(
                connector_id=str(result["connectorId"]),
                name=str(result["name"]),
                credential=str(result["credential"]),
                poll_seconds=int(result.get("pollSeconds", 60)),
                branding=parse_branding(result["branding"]) if "branding" in result else None,
            )
        except (KeyError, TypeError, ValueError):
            raise CloudError("bad_response", "The server sent an unexpected answer.") from None

    def report_devices(self, devices: list[dict]) -> dict[str, str]:
        """Tell the cloud which terminals this connector can reach; returns each serial's approval state."""
        result = self._request("POST", "/api/connectors/devices", {"devices": devices})
        return {
            str(item.get("serialNumber")): str(item.get("state"))
            for item in result.get("devices", [])
            if isinstance(item, dict)
        }

    def config(self) -> CloudConfig:
        result = self._request("GET", "/api/connectors/config")
        devices: dict[str, ConfigDevice] = {}
        for item in result.get("devices", []):
            if not isinstance(item, dict) or not item.get("serialNumber"):
                continue
            serial = str(item["serialNumber"])
            devices[serial] = ConfigDevice(
                serial=serial,
                name=str(item.get("name") or serial),
                timezone=str(item.get("timezone") or "Asia/Kolkata"),
                ignore_before=_parse_instant(item.get("ignoreBefore")),
                enabled=bool(item.get("enabled", True)),
            )
        poll = result.get("pollSeconds")
        return CloudConfig(
            poll_seconds=max(10, int(poll)) if isinstance(poll, int) else 60,
            devices=devices,
            pending={str(value) for value in result.get("pending", [])},
            rejected={str(value) for value in result.get("rejected", [])},
            branding=parse_branding(result["branding"]) if "branding" in result else None,
            update=_parse_policy(result.get("update")),
        )
