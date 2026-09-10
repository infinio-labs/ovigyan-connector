# IDeS Device Connector

Cross-platform edge runtime for attendance terminals. It runs inside the
branch LAN, reads vendor protocols, buffers events locally, and delivers signed
batches to the IDeS cloud control plane. It contains no attendance business
rules.

## X2008 quick start

The first adapter targets the supplied eSSL X2008 (`NFZ824090078`) through the
ZK-compatible TCP pull protocol. Configure the device IP and port `4370` in
the IDeS Device Management screen first.

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\\Scripts\\Activate.ps1
python -m pip install -e .

export ATTENDANCE_DEVICE_HOST=192.168.1.50
export ATTENDANCE_MACHINE_ID=NFZ824090078
export ATTENDANCE_INGEST_URL=https://school.example.com/api/attendance/device-events
export ATTENDANCE_INGEST_SECRET='server-ATTENDANCE_INGEST_SECRET'
python -m ides_device_connector.cli --once
```

Windows PowerShell uses `$env:NAME="value"`; macOS uses the same shell
commands as Linux. The runtime uses only cross-platform Python standard
library features plus the ZK adapter dependency.

Optional variables: `ATTENDANCE_DEVICE_PORT` (default `4370`),
`ATTENDANCE_DEVICE_PASSWORD` (default `0`), `ATTENDANCE_DEVICE_TIMEOUT`
(default `10`), `ATTENDANCE_TIMEZONE` (default `Asia/Kolkata`),
`ATTENDANCE_POLL_SECONDS` (default `60`), and `ATTENDANCE_OUTBOX_PATH`.

Run continuously with Windows Task Scheduler, macOS `launchd`, or Linux
`systemd`. Native installers are built in the release pipeline with
PyInstaller from `launcher.py`; build each artifact on its target OS and sign it there.

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
