"""The connector's work, without any user interface: pair, add terminals, and run the polling cycle.

A local page, a tray icon and the command line all drive this same object, so what they show agrees.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from . import __version__
from .adapters import EsslX2008Adapter
from .delivery import deliver
from .branding import contrast_text, fetch_logo
from .cloud import CloudClient, CloudConfig, CloudError, normalize_server_url
from .devices import DeviceInfo, ProbeError, probe, validate_address
from .events import EventEnvelope
from .outbox import Outbox
from .state import LocalDevice, State, StateStore
from .transport import EventTransport

# Per-terminal states a page can show. "ok" means polled and delivered in the last cycle.
OK, UNREACHABLE, PENDING, REJECTED, DISABLED, UNKNOWN = "ok", "unreachable", "pending", "rejected", "disabled", "unknown"


@dataclass
class DeviceStatus:
    id: str
    host: str
    port: int
    serial: str | None
    model: str | None
    state: str
    message: str
    sent: int = 0
    queued: int = 0


@dataclass
class CycleReport:
    at: str
    # ok | unpaired | offline | revoked
    connector: str
    message: str
    poll_seconds: int
    devices: list[DeviceStatus] = field(default_factory=list)


Reader = Callable[[LocalDevice, str], list[EventEnvelope]]


def read_terminal(device: LocalDevice, timezone_name: str) -> list[EventEnvelope]:
    return EsslX2008Adapter(device.host, device.port, device.password, 10, timezone_name).read_events()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ConnectorService:
    def __init__(
        self,
        store: StateStore | None = None,
        *,
        client_factory: Callable[..., CloudClient] = CloudClient,
        prober: Callable[..., DeviceInfo] = probe,
        reader: Reader = read_terminal,
        http_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        probe_timeout: int | None = None,
        logo_fetcher: Callable[[str], str | None] = fetch_logo,
    ):
        self.store = store or StateStore()
        self.client_factory = client_factory
        self.prober = prober
        self.reader = reader
        self.logo_fetcher = logo_fetcher
        self.http_retries = http_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        # How long to wait for a terminal when adding it. Windows takes a few seconds to give up on a closed port.
        self.probe_timeout = probe_timeout or int(os.environ.get("OVIGYAN_PROBE_TIMEOUT", "0") or 0) or 5
        # The loop and a page can both change the saved state, so changes are serialised.
        self._lock = threading.RLock()
        self._wake: threading.Event | None = None

    def enable_wake(self) -> None:
        """Let `request_cycle` cut the pause short (used when a page is attached)."""
        self._wake = threading.Event()

    def request_cycle(self) -> None:
        """Run the next cycle now instead of at the next interval, for example after pairing."""
        if self._wake:
            self._wake.set()

    # ── setup ──────────────────────────────────────────────────────────────────────────────────────

    def pair(self, server: str, key: str) -> State:
        with self._lock:
            return self._pair(server, key)

    def _pair(self, server: str, key: str) -> State:
        """Swap the connection key from Settings > Connectors for this connector's own credential."""
        origin = normalize_server_url(server)
        client = self.client_factory(origin, max_retries=self.http_retries, retry_backoff_seconds=self.retry_backoff_seconds)
        result = client.pair(key)
        state = self.store.load()
        state.server = origin
        state.credential = result.credential
        state.connector_id = result.connector_id
        state.connector_name = result.name
        state.status = "paired"
        self._apply_branding(state, result.branding or {})
        self.store.save(state)
        return state

    def unpair(self) -> State:
        with self._lock:
            return self._unpair()

    def _unpair(self) -> State:
        """Forget the credential (terminals stay listed). The school can also revoke it from the web."""
        state = self.store.load()
        state.credential, state.connector_id, state.connector_name, state.status = None, None, None, "unpaired"
        state.branding = {}
        self.store.save(state)
        return state

    def add_device(self, host: str, port: int = 4370, password: int = 0) -> tuple[LocalDevice, bool]:
        with self._lock:
            return self._add_device(host, port, password)

    def _add_device(self, host: str, port: int = 4370, password: int = 0) -> tuple[LocalDevice, bool]:
        """Check a terminal is reachable, remember it, and tell the cloud. Returns (device, already_known)."""
        host, port = validate_address(host, port)
        info = self.prober(host, port, password, self.probe_timeout)
        state = self.store.load()
        existing = next((d for d in state.devices if d.serial == info.serial), None)
        if existing:
            existing.host, existing.port, existing.password = host, port, password
            existing.model, existing.firmware = info.model or existing.model, info.firmware or existing.firmware
            device = existing
        else:
            device = LocalDevice(host=host, port=port, password=password, serial=info.serial, model=info.model, firmware=info.firmware)
            state.devices.append(device)
        self.store.save(state)
        if state.paired:
            try:
                self._client(state).report_devices(self._report_payload([device]))
            except CloudError:
                pass  # the next cycle reports it
        return device, existing is not None

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            return self._remove_device(device_id)

    def _remove_device(self, device_id: str) -> bool:
        state = self.store.load()
        kept = [d for d in state.devices if d.id != device_id]
        if len(kept) == len(state.devices):
            return False
        state.devices = kept
        self.store.save(state)
        return True

    # ── one polling cycle ──────────────────────────────────────────────────────────────────────────

    def _client(self, state: State) -> CloudClient:
        assert state.server and state.credential
        return self.client_factory(
            state.server, state.credential, max_retries=self.http_retries, retry_backoff_seconds=self.retry_backoff_seconds
        )

    @staticmethod
    def _report_payload(devices: list[LocalDevice]) -> list[dict]:
        return [
            {"serialNumber": d.serial, "vendor": "essl" if (d.model or "").upper().startswith(("X", "E")) else "zkteco",
             "model": d.model, "host": d.host, "port": d.port}
            for d in devices
            if d.serial
        ]

    def run_cycle(self) -> CycleReport:
        with self._lock:
            state = self.store.load()
        if not state.paired:
            revoked = state.status == "revoked"
            return self._finish(
                CycleReport(
                    _utc_now(),
                    "revoked" if revoked else "unpaired",
                    "The school's Ovigyan site no longer accepts this connector. Pair it again with a new key."
                    if revoked
                    else "Not paired yet. Enter the web address and connection key.",
                    60,
                    [self._status(d, UNKNOWN, "Not paired yet.") for d in state.devices],
                )
            )
        client = self._client(state)
        try:
            config = client.config()
            client.report_devices(self._report_payload(state.devices))
        except CloudError as error:
            if error.kind == "unauthorized":
                with self._lock:
                    state = self.store.load()
                    state.status = "revoked"
                    self.store.save(state)
                return self._finish(
                    CycleReport(_utc_now(), "revoked", error.message, 60, [self._status(d, UNKNOWN, error.message) for d in state.devices])
                )
            return self._finish(
                CycleReport(_utc_now(), "offline", error.message, 60, [self._status(d, UNKNOWN, "Waiting for the connection.") for d in state.devices])
            )
        self._adopt_branding(config.branding)
        devices = [self._poll(state, client, device, config) for device in state.devices]
        return self._finish(CycleReport(_utc_now(), "ok", "Connected.", config.poll_seconds, devices))

    def _apply_branding(self, state: State, branding: dict) -> bool:
        """Adopt the school's look (an empty block means the site sets none, so the defaults return).
        The logo is downloaded only when its address changes, or when an earlier download failed."""
        old = state.branding
        new = {key: branding[key] for key in ("name", "color", "logo_url") if key in branding}
        logo = old.get("logo_png") if old.get("logo_url") == new.get("logo_url") else None
        if new.get("logo_url") and not logo:
            logo = self.logo_fetcher(new["logo_url"])
        if logo:
            new["logo_png"] = logo
        state.branding = new
        return new != old

    def _adopt_branding(self, branding: dict | None) -> None:
        if branding is None:  # an older site that sends none: leave whatever is stored
            return
        with self._lock:
            state = self.store.load()
            if self._apply_branding(state, branding):
                self.store.save(state)

    def logo_png(self) -> bytes | None:
        encoded = self.store.load().branding.get("logo_png")
        return base64.b64decode(encoded) if encoded else None

    @staticmethod
    def _brand_summary(branding: dict) -> dict:
        logo = branding.get("logo_png")
        return {
            "name": branding.get("name"),
            "color": branding.get("color"),
            "text": contrast_text(branding["color"]) if branding.get("color") else None,
            "logo": hashlib.sha1(logo.encode()).hexdigest()[:10] if logo else None,
        }

    def _status(self, device: LocalDevice, state: str, message: str, **extra: int) -> DeviceStatus:
        return DeviceStatus(device.id, device.host, device.port, device.serial, device.model, state, message, **extra)

    def _poll(self, state: State, client: CloudClient, device: LocalDevice, config: CloudConfig) -> DeviceStatus:
        serial = device.serial
        if not serial:
            return self._status(device, UNKNOWN, "Not identified yet. Remove it and add it again.")
        if serial in config.rejected:
            return self._status(device, REJECTED, "The school declined this terminal.")
        approved = config.devices.get(serial)
        if approved is None:
            return self._status(device, PENDING, "Waiting for the school to approve this terminal in Ovigyan.")
        if not approved.enabled:
            return self._status(device, DISABLED, "Switched off in Ovigyan.")

        outbox = Outbox(self.store.outbox_path(serial))
        try:
            reachable, problem = True, ""
            try:
                for event in self.reader(device, approved.timezone):
                    if approved.ignore_before and datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")) < approved.ignore_before:
                        continue  # history from before go-live is never sent
                    outbox.enqueue(event.event_id, event.as_payload())
            except Exception as error:  # noqa: BLE001 - a terminal that is off must not stop the others
                reachable, problem = False, f"Can't reach the terminal at {device.host}:{device.port}."
                print(f"ovigyan-connector: {device.host}: {error}", file=sys.stderr)
            transport = EventTransport(
                state.server.rstrip("/") + "/api/attendance/device-events",  # type: ignore[union-attr]
                "",
                serial,
                credential=state.credential or "",
                max_retries=self.http_retries,
                retry_backoff_seconds=self.retry_backoff_seconds,
            )
            sent = 0
            deferred: set[str] = set()
            try:
                if reachable:
                    transport.heartbeat()
                while batch := outbox.pending(500, deferred):
                    sent += deliver(transport, outbox, batch, deferred)
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    return self._status(device, UNKNOWN, "The school no longer accepts this connector.", sent=sent, queued=outbox.count())
                return self._status(device, OK if reachable else UNREACHABLE, f"The school's server refused a batch (HTTP {error.code}).", sent=sent, queued=outbox.count())
            except (urllib.error.URLError, TimeoutError, OSError):
                return self._status(device, OK if reachable else UNREACHABLE, "Could not reach the school's server; will retry.", sent=sent, queued=outbox.count())
            if not reachable:
                return self._status(device, UNREACHABLE, problem, sent=sent, queued=outbox.count())
            return self._status(device, OK, "Working.", sent=sent, queued=outbox.count())
        finally:
            outbox.close()

    def _finish(self, report: CycleReport) -> CycleReport:
        """Leave a note for whatever is showing the status; it holds nothing secret."""
        try:
            self.store.home.mkdir(parents=True, exist_ok=True)
            brand = self.store.load().branding
            payload = {
                "version": __version__,
                **asdict(report),
                "brand": {k: brand.get(k) for k in ("name", "color", "logo_png")},
            }
            self.store.status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass
        return report

    def status(self) -> dict:
        """Saved facts plus the last cycle's report, for a page or the command line."""
        state = self.store.load()
        last = None
        try:
            last = json.loads(self.store.status_path.read_text(encoding="utf-8"))
            last.pop("brand", None)  # for the tray; the page gets a summary and loads the logo itself
        except (OSError, ValueError, AttributeError):
            pass
        return {
            "version": __version__,
            "paired": state.paired,
            "status": state.status,
            "server": state.server,
            "connectorName": state.connector_name,
            "brand": self._brand_summary(state.branding),
            "devices": [
                {"id": d.id, "host": d.host, "port": d.port, "serial": d.serial, "model": d.model, "firmware": d.firmware}
                for d in state.devices
            ],
            "lastCycle": last,
        }

    # ── running ────────────────────────────────────────────────────────────────────────────────────

    def run_forever(self, stop: threading.Event | None = None, fixed_wait: float | None = None) -> None:
        """Poll until `stop` is set. `fixed_wait` replaces the computed pause (for tests and diagnostics)."""
        stop = stop or threading.Event()
        failures = 0
        while not stop.is_set():
            wait = 60.0
            try:
                report = self.run_cycle()
                wait = float(report.poll_seconds)
                if report.connector in {"unpaired", "revoked"}:
                    wait = 15.0  # idle until someone pairs, but notice it quickly
                failures = 0 if report.connector != "offline" else failures + 1
            except Exception as error:  # noqa: BLE001 - the service must outlive any single bad cycle
                failures += 1
                print(f"ovigyan-connector: {error}", file=sys.stderr)
            if failures:
                wait = min(300.0, wait * (2 ** min(failures, 3)))
            self._pause(stop, wait if fixed_wait is None else fixed_wait)

    def _pause(self, stop, seconds: float) -> None:
        if self._wake is None:
            stop.wait(seconds)
            return
        # With a page attached, wake early when it asks for a cycle; check for stop in short slices.
        self._wake.clear()
        end = time.monotonic() + seconds
        while not stop.is_set() and not self._wake.is_set() and time.monotonic() < end:
            stop.wait(min(0.25, max(0.0, end - time.monotonic())))
