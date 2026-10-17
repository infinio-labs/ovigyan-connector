"""What the connector remembers between runs: where it is paired and which terminals it was given.

One JSON file in the connector's home directory. It holds the cloud credential and terminal passwords,
so it is written atomically and, on POSIX, readable by its owner only (on Windows the installer's
ProgramData folder is already restricted to SYSTEM and Administrators).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_home() -> Path:
    override = os.environ.get("OVIGYAN_CONNECTOR_HOME", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Ovigyan Connector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Ovigyan Connector"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/var/lib/ovigyan-connector")
    return Path.home() / ".ovigyan-connector"


@dataclass
class LocalDevice:
    """A terminal added on this PC. The password never leaves it; the cloud learns the serial and address only."""

    host: str
    port: int = 4370
    password: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    serial: str | None = None
    model: str | None = None
    firmware: str | None = None


@dataclass
class State:
    server: str | None = None
    credential: str | None = None
    connector_id: str | None = None
    connector_name: str | None = None
    # 'unpaired' -> 'paired'; 'revoked' once the cloud refuses the credential (pair again with a new key).
    status: str = "unpaired"
    devices: list[LocalDevice] = field(default_factory=list)
    # The school's look from its Ovigyan site: name, color, logo_url, logo_png (base64). Empty = Ovigyan defaults.
    branding: dict = field(default_factory=dict)

    @property
    def paired(self) -> bool:
        return self.status == "paired" and bool(self.server and self.credential)


class StateStore:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home) if home else default_home()
        self.path = self.home / "connector.json"

    def load(self) -> State:
        if not self.path.exists():
            return State()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        devices = [LocalDevice(**item) for item in raw.pop("devices", [])]
        known = {name for name in State.__dataclass_fields__}
        return State(devices=devices, **{key: value for key, value in raw.items() if key in known})

    def save(self, state: State) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.home, 0o700)
        payload = json.dumps(asdict(state), indent=2)
        handle, temp_name = tempfile.mkstemp(dir=self.home, prefix=".connector-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "posix":
                os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def outbox_path(self, serial: str) -> Path:
        """One queue per terminal: event ids are only unique within a terminal."""
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in serial)[:64]
        return self.home / f"outbox-{safe}.db"

    @property
    def status_path(self) -> Path:
        return self.home / "status.json"
