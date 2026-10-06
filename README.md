# Ovigyan Connector

Cross-platform edge runtime for attendance terminals. It runs inside the
branch LAN, reads vendor protocols, buffers events locally, and delivers signed
batches to the Ovigyan cloud control plane. It contains no attendance business
rules.

## X2008 quick start

The first adapter targets the supplied eSSL X2008 (`NFZ824090078`) through the
ZK-compatible TCP pull protocol. Configure the device IP and port `4370` in
the Ovigyan Device Management screen first.

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\\Scripts\\Activate.ps1
python -m pip install -e .

export ATTENDANCE_DEVICE_HOST=192.168.1.50
export ATTENDANCE_MACHINE_ID=NFZ824090078
export ATTENDANCE_INGEST_URL=https://school.example.com/api/attendance/device-events
# Prefer the one-time token issued from Ovigyan Device Management.
export ATTENDANCE_DEVICE_TOKEN='ovigyan_dev_...'
# Legacy bootstrap fallback only:
# export ATTENDANCE_INGEST_SECRET='server-ATTENDANCE_INGEST_SECRET'
python -m ovigyan_connector.cli --once
```

Windows PowerShell uses `$env:NAME="value"`. The runtime uses only
cross-platform Python standard library features plus the ZK adapter dependency.
Linux and Windows are the supported production release targets. macOS support
is deferred until Apple Developer signing and notarization are enabled.

Optional variables: `ATTENDANCE_DEVICE_PORT` (default `4370`),
`ATTENDANCE_DEVICE_PASSWORD` (default `0`), `ATTENDANCE_DEVICE_TIMEOUT`
(default `10`), `ATTENDANCE_TIMEZONE` (default `Asia/Kolkata`),
`ATTENDANCE_POLL_SECONDS` (default `60`), `ATTENDANCE_HTTP_TIMEOUT` (default `30`),
`ATTENDANCE_HTTP_RETRIES` (default `3`), `ATTENDANCE_RETRY_BACKOFF_SECONDS`
(default `2`), and `ATTENDANCE_OUTBOX_PATH`.

The ingest URL must use HTTPS. For local-only testing, set
`ATTENDANCE_ALLOW_INSECURE_HTTP=true` and use a localhost URL; remote HTTP is
always rejected.

If the cloud refuses a single punch because of its own content (a `Device event ...` 400), the
connector splits the batch, quarantines just that punch (kept in the outbox `rejected` table with the
reason, and reported as `rejected=N` in the run summary) and delivers the rest. Errors about the
request or credentials (401, 409, other 400s) still stop the run and leave the queue untouched.

Transient cloud failures (timeouts, connection errors, HTTP 408/429/5xx) are
retried with bounded exponential backoff. Permanent HTTP errors are surfaced
immediately; queued events remain durable for the next polling cycle.

`ATTENDANCE_DEVICE_TOKEN` is preferred. It is shown once when an administrator
issues or rotates a device credential; store it in the connector's local
service configuration. The cloud stores only its hash. The shared
`ATTENDANCE_INGEST_SECRET` remains for migration of older installations.

## Windows installer

Release builds publish `ovigyan-connector-setup-<version>.exe` (Inno Setup). Run it as an
administrator on an always-on PC **in the terminal's network**. The wizard asks for the terminal IP,
Machine ID, the Ovigyan ingest URL (HTTPS) and the connector token issued in Ovigyan, then:

- installs to `C:\Program Files\Ovigyan Connector`;
- writes `C:\ProgramData\Ovigyan Connector\connector.env` (readable only by SYSTEM and Administrators);
- registers a boot-time scheduled task running as SYSTEM, restarts on failure, and starts it now;
- logs to `C:\ProgramData\Ovigyan Connector\connector.log`; the undelivered-punch queue lives next to it.

Silent roll-out:

```powershell
.\ovigyan-connector-setup-1.2.3.exe /VERYSILENT /SUPPRESSMSGBOXES `
  /DEVICE_HOST=192.168.1.50 /MACHINE_ID=NFZ824090078 `
  /INGEST_URL=https://school.example.com/api/attendance/device-events /TOKEN=ovigyan_dev_...
```

Re-running a newer installer upgrades in place and keeps the existing settings. Uninstalling removes the program and
task but keeps `connector.env`, the log and the queue so no punch is lost; delete that folder by hand to remove them.
CI builds the installer and smoke-tests install, config permissions and uninstall on `windows-latest`.
Signing: set repository secrets `WINDOWS_SIGN_CERT_BASE64` (PFX, base64) and `WINDOWS_SIGN_CERT_PASSWORD`;
without them the release job warns and the output is unsigned.

Run continuously with Windows Task Scheduler or Linux `systemd`. Service
templates are in `packaging/`; keep the environment file outside the repository.
The Windows registration script restricts it to SYSTEM and local administrators
and loads values into the connector process only. Releases are unsigned until the signing secrets above are set; do not deploy unsigned assets.

The connector never clears device attendance logs. Events are queued in a
local SQLite WAL database, retried after network failure, and removed only
after a successful server response. The server remains responsible for
identity mapping, quarantine, attendance policy, and ledger writes.

## Development

```bash
python -m pip install -e . -r requirements-dev.txt
python -m pytest -q
```

The test suite uses fake device rows and transport responses; it does not need
hardware or production credentials.
