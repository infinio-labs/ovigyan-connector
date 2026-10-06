# Ovigyan Connector

Cross-platform edge runtime for attendance terminals. It runs inside the
branch LAN, reads vendor protocols, buffers events locally, and delivers signed
batches to the Ovigyan cloud control plane. It contains no attendance business
rules.

## Paired mode (recommended)

Instead of environment variables, pair the connector with your Ovigyan site once and add terminals by IP.
Everything else (which branch a terminal belongs to, its name, mapping people to terminal users, the polling
interval, and the date before which old punches are ignored) is managed in Ovigyan.

```bash
# 1. In Ovigyan: Settings > Connectors > Generate key. Then on this PC:
ovigyan-connector pair --server https://school.example.com --key OVG-XXXXX-XXXXX-XXXXX-XXXXX

# 2. Add each terminal (reads its serial number, model and firmware to check it works):
ovigyan-connector add-device --host 192.168.1.50            # --port 4370 --password 0 by default

# 3. An administrator approves the terminal in Ovigyan (Settings > Connectors > New terminals).

# 4. Run it (a service in production):
ovigyan-connector run                                       # or: run --once
```

`ovigyan-connector run` also serves a small page on this PC only (`http://127.0.0.1:47890`, change with
`--ui-port`) where you connect, add terminals and see their status. Run `ovigyan-connector open` to open it in the
browser; the link carries a secret, so other users and web pages cannot use it. `--no-ui` turns it off.

Other commands: `devices`, `status`, `remove-device <id>`, `unpair`. Settings live in
`C:\ProgramData\Ovigyan Connector` (Windows), `/var/lib/ovigyan-connector` (Linux, as root) or
`~/.ovigyan-connector`; override with `OVIGYAN_CONNECTOR_HOME` or `--home`. The file holds the connection
credential and the terminals' communication passwords, so it is readable only by its owner. The terminal
passwords never leave this PC.

If the school revokes the connector in Ovigyan, the next check shows "revoked": pair again with a new key.
Terminals the connector cannot reach are reported without stopping the others, and punches that could not be
delivered stay queued (one queue per terminal) and go out when the connection returns.

## X2008 quick start (environment variables)

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

## Install on Windows

Release builds publish `ovigyan-connector-setup-<version>.exe` (Inno Setup). Run it as an administrator on an
always-on PC **in the terminal's network**. It asks nothing. It installs the connector as a background task and, at
the end, opens the connector's page, where you enter the Ovigyan web address and the connection key and add
terminals (see "Paired mode").

- installs to `C:\Program Files\Ovigyan Connector`;
- registers a boot-time scheduled task running as SYSTEM that never times out and restarts if it stops;
- adds an **Ovigyan Connector** Start-menu and desktop shortcut that opens the page, and a status icon in the
  notification area for whoever signs in (green working, amber needs attention, red a problem);
- settings, the credential and the terminals' passwords live in `C:\ProgramData\Ovigyan Connector`, readable only by
  SYSTEM and Administrators. Two files in it are readable by standard users because the shortcut and the icon need
  them and neither is a secret to the school's data: `ui-token` (only stops web pages and other programs from using the
  page) and `status.json`. Anyone who can sign in to this PC can open the page, so use a PC only staff can sign in to.
- logs to `connector.log` in that folder; each terminal's undelivered-punch queue is there too.

Silent roll-out: `ovigyan-connector-setup-1.2.3.exe /VERYSILENT /SUPPRESSMSGBOXES`, then pair from the PC
(`ovigyan-connector pair --server ... --key ...`) or open the page.

Re-running a newer installer upgrades in place and keeps everything. Uninstalling removes the program, the task and the
icon but keeps the settings folder so no punch is lost; delete it by hand to remove it. CI builds the installer and
smoke-tests, on `windows-latest`: install, the task (no time limit), the folder's permissions, the page answering with
and without its secret link, the command line, reinstall, and uninstall.

Signing: set repository secrets `WINDOWS_SIGN_CERT_BASE64` (PFX, base64) and `WINDOWS_SIGN_CERT_PASSWORD`; without them
the release job warns and the output is unsigned (Windows SmartScreen will warn). Do not deploy unsigned assets.

## Install on Linux

Download `ovigyan-connector`, `ovigyan-connector-install.sh` and `ovigyan-connector.service` from the release into one
folder, then:

```bash
chmod +x ovigyan-connector ovigyan-connector-install.sh
sudo ./ovigyan-connector-install.sh      # installs to /usr/local/bin, creates a service user, starts the service
sudo ovigyan-connector open               # prints the link to the connector's page
```

On a server without a screen, forward the port from your PC first (`ssh -L 47890:127.0.0.1:47890 you@server`) and open
the link it prints. Settings are in `/var/lib/ovigyan-connector` (owner-only). `ovigyan-connector-uninstall.sh` removes
the service and keeps the settings; add `--purge` to delete them too. The notification-area icon is built into the
Windows and macOS builds only.

## Updates

Nobody has to do anything after the first install. Each Ovigyan site tells its connectors which connector version it
was tested with (the web app pins it; `CONNECTOR_AUTO_UPDATE=false` on the site holds them). A connector that is
behind waits until it has nothing queued, downloads that release's `manifest.json` and signature, and installs it only
if the signature matches the public key built into the program (`src/ovigyan_connector/update_key.py`), every file
matches the manifest, and the new build passes `ovigyan-connector self-test` on this computer. It never installs
anything other than the version the site asked for, never an older one, and tries at most once an hour. Pairing,
terminals and queued punches are untouched.

- **Windows:** the signed setup runs silently as a one-off SYSTEM task (the same upgrade as a manual reinstall).
- **Linux:** the verified build is staged, the service restarts, and systemd's root-only `ExecStartPre`
  (`ovigyan-connector apply-update`) checks it again and swaps `/usr/local/bin/ovigyan-connector`, keeping the old
  one as `ovigyan-connector.previous`. Reinstall the unit (`ovigyan-connector-install.sh`) once to get that hook.
- **Manually:** `ovigyan-connector update` (what the site wants) or `--target X.Y.Z` (support); `self-test` checks a build.
- macOS builds do not self-update.

One-time setup for maintainers: run `python scripts/make-update-key.py`, commit the public key it writes, and store the
private key it prints as the repository secret `CONNECTOR_UPDATE_SIGNING_KEY` (instructions are in the script). Release
workflow signs `manifest.json` with it; without the secret the release is built but not offered to connectors. Publish
the draft release for connectors to see it, then bump the pinned version in the web app (`update-policy.ts`).
Protect the private key: whoever holds it can push code to every school.

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
