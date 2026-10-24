# Connector Code Review & Site Audit — 2026-10-23

Reviewed at `ff6c7e0`, branch `main`, version 0.4.0.

> **Handle as confidential material.** This report names specific weaknesses in the release and
> auto-update path. Do not paste it wholesale into a public issue, chat room, or third-party
> service. Note that `ci.yml:26` records this repository as **public**.

## Scope

Whole-product review of the current state at HEAD (no diff base) plus a repo-only white-box security
audit. Three review axes ran as independent sub-agents so their findings would not contaminate each
other; the audit ran as a separate tooling pass.

Nothing in the repository was modified by this review except the creation of this file. The audit
ledger was written outside the repo, to
`~/.qoder/audit-out/ovigyan-connector-2026-10-23/report-1.md`.

Every finding is marked **verified** (reproduced by hand during the review, with the command or file
read that confirmed it) or **reported** (sub-agent finding, citations intact but not independently
reproduced). False positives and over-statements are corrected explicitly so they are not actioned
on the wrong premise.

**Subject:** a Python edge connector, 3,069 LOC across 22 files in `src/ovigyan_connector/`, with
3,494 LOC across 28 test files in `tests/`. It runs on a school's PC, pulls punch events from
biometric attendance terminals (eSSL X2008 / ZK-compatible TCP), queues them in a local SQLite WAL
outbox, and delivers them over signed HTTPS to the Ovigyan ERP. It ships as a Windows Inno Setup
installer running as a never-timing-out **SYSTEM** scheduled task plus a per-user tray process, and
as a Linux executable with a systemd unit. It updates itself.

---

## Standards

### A. Documented-standard conformance

This repo has no `AGENTS.md`, no `CONTRIBUTING.md` and (before this file) no `docs/` directory. Its
documented standards are `DESIGN.md` and `README.md`.

**Tooling baseline, verified.** There is no linter, formatter, or type-checker anywhere.
`requirements-dev.txt` is exactly two lines — `pytest==8.4.2` and `pyinstaller==6.16.0` — and
`grep -rn "ruff|black|mypy|flake8|pylint"` across `pyproject.toml`, both workflows and that file
returns nothing. CI runs `python -m pytest -q` and `python -m compileall -q src`; `compileall`
validates **syntax only**. Style and type issues are therefore not tooling-covered, but since none
are documented standards either, none are raised as violations. This baseline matters for reading
section B: several smells there have no automated defence at all.

#### 1. The update-signing private key is written to an unignored path in a public repo — hard violation, verified (corrected severity)

Standard: `README.md:191` — *"Protect the private key: whoever holds it can push code to every
school."*

`scripts/make-update-key.py:32-35` writes the Ed25519 private key to
`out = root / "update-signing-key.txt"` in the repository root. `.gitignore` has no such entry and no
`*.txt` rule; `git check-ignore -v update-signing-key.txt` confirms **NOT IGNORED**.

**Correction to the reported severity — this is a latent risk, not an active incident.** Verified:

- `git log --all --diff-filter=A -- "update-signing-key.txt" "*.key" "*signing-key*"` returns
  nothing. No key has ever been committed.
- The working tree is clean; no key file is present.
- The script's own hygiene is good: it creates the file with `os.open(..., 0o600)`, its docstring
  gives the exact `gh secret set` then `shred -u update-signing-key.txt` procedure, states *"never
  commit or share it"*, and it refuses to regenerate an existing key without `--force`, warning
  *"Rotating it strands every installed connector."*

The defect is that **no mechanical guard backs correct instructions**. Between generation and
deletion the key sits untracked-but-unignored in the repo root of a public repository. A single
`git add -A` in that window commits the fleet code-signing key to GitHub history permanently, and
history rewriting is the only remedy.

Stakes are live, not hypothetical: `src/ovigyan_connector/update_key.py` has a populated
`PUBLIC_KEY = "c4QIwEK0A873LUYkWfHoGqeWJCX30kgFhsYerB9uQQk="`, so signed auto-update is in
production. Fix is one line in `.gitignore`.

#### 2. `eventId` is not stable across a cloud timezone change — hard violation, verified

Standard: `DESIGN.md:61` — *"`eventId` is stable per device event."* `DESIGN.md:62` — Ovigyan
"deduplicates by device and event ID."

`src/ovigyan_connector/events.py:74`:

```python
event_id = hashlib.sha256(f"{user_id}|{occurred_at}|{punch}|{verify}".encode()).hexdigest()[:32]
```

`occurred_at` comes from `_timestamp(timestamp, timezone_name)` at `events.py:55-62`, which
interprets a naive device timestamp in `timezone_name` before converting to UTC. That zone is
cloud-supplied **every cycle**: `service.py:400` `self.reader(device, approved.timezone)`, sourced
from `cloud.py:75` / `cloud.py:220`.

Consequence: an administrator correcting a terminal's timezone rewrites every historical `eventId`.
The outbox's `delivered` ledger (`outbox.py:39-41`) stops matching, and because `DESIGN.md:70`
states "The device log is never cleared automatically," the same punches are re-enqueued under new
IDs and re-sent. Cloud dedup keys on device + event ID, so it cannot collapse them — **duplicate
attendance rows**.

Reachability verified across both repos: the ERP stores this as an ordinary editable column,
`ovigyan-web/src/db/attendance.ts:86` — `timezone: text('timezone').notNull().default('Asia/Kolkata')`.
A second drift vector exists at `service.py:469`: `zone = self._timezones.get(serial, "Asia/Kolkata")`,
a hardcoded fallback used when the cache is cold.

`tests/test_events.py:21` asserts only same-timezone determinism, so the suite does not cover this.

Note the adjacent code shows real care — `events.py:71-73` carries the comment *"Never use the ZK
`uid`: on the terminal it is the user's slot, repeated on every punch, so it would make a user's
later punches look like duplicates of the first."* The author reasoned about ID stability correctly
and then bound the ID to a mutable cloud input.

#### 3. Undocumented env vars override the update trust anchor — judgement call, verified context

Standard: `README.md:175-176` — *"installs it only if the signature matches the public key built
into the program."*

`updater.py:106-109` allows `OVIGYAN_ALLOW_INSECURE_HTTP=true` together with
`OVIGYAN_UPDATE_PUBLIC_KEY` / `OVIGYAN_UPDATE_BASE_URL` to replace both the trust anchor and the
release host. None appear in `README.md:100-105`'s variable list. Also undocumented:
`OVIGYAN_CONNECTOR_UI_PORT` (`commands.py:22`) and `OVIGYAN_PROBE_TIMEOUT` (`service.py:122`).

Exploiting it requires SYSTEM-level access to the machine's environment, so the practical risk is
low; the documentation claim is nonetheless stated unconditionally. All *documented* variables are
genuinely read.

#### 4. Lossy envelope re-encoding — judgement call, reported

Standard: `DESIGN.md:61` — *"`punch=255` maps to `unknown`; the connector preserves the raw value."*

`events.py:24-25` serialise punch values 2/3/4/5 as `255` and unknown verify as `-1` — a value
absent from the contract at `DESIGN.md:50-59`. Only `raw.punch` / `raw.status` carry the original.
The non-guessing intent is correct and tested (`tests/test_events.py:33-41`); the wire fields
silently renumber, so a reader of the contract cannot predict them.

#### 5. Edge-side permanent drop — judgement call, reported

`service.py:401-402` `continue`s past `approved.ignore_before` without ever enqueuing. The threshold
is cloud-set (`README.md:23-24`), but the effect is an unauditable local drop, which sits uneasily
with `DESIGN.md:34` placing "attendance corrections or ledger decisions" outside what the connector
owns. The inline comment states the intent: *"history from before go-live is never sent."*

#### Minor findings

- `release.yml:199-212` computes an `UNSIGNED` flag but never gates on it; the "refusing unsigned"
  error fires for *any* pre-existing non-draft release.
- `service.py:110/562/579` — `restart_requested` is write-only outside tests.
- `telemetry.py:21` — the `_IPV4` scrubbing pattern misses IPv6 literals, so `README.md:203`'s
  promised IP scrubbing is IPv4-only while `devices.py:32-39` accepts IPv6 hosts.

#### Verified clean

These were checked specifically and passed. They are recorded so a later round does not re-litigate them.

- **Secret-free `status.json` — VERIFIED CLEAN.** `service.py:482-487` writes only the version, a
  `CycleReport`, and `brand{name,color,logo_png}`. `DeviceStatus` (`service.py:41-50`) holds no
  credential or password. `tray.py:61-66` is read-only. `register-task.ps1:47-49` ACLs match
  `README.md:136-139`.
- **Outbound connections only — VERIFIED CLEAN.** The sole listener is `ui.py:248`,
  `super().__init__(("127.0.0.1", port), _Handler)` — loopback-bound, not `0.0.0.0` — with
  Host-header validation (`ui.py:84`), a token in the URL (`ui.py:49`) and origin checks.
  `ui.py:4` documents the threat model: *"anything on the PC (or a web page in the user's browser)
  could try to reach 127.0.0.1."* `discovery.py` only dials out. Documented at `DESIGN.md:99` and
  `README.md:39-41`.
- **Signed, staged, rollback-capable updates — VERIFIED FAIL-CLOSED.** `updater.py:200-208` verifies
  the Ed25519 signature *before* JSON parsing and staging; `:210-217` checksums every asset against
  the signed manifest; `verify_signature:65-74` returns `False` on any exception including an empty
  key; `apply_staged:268-300` re-verifies as root with symlink and monotonicity checks and leaves
  the working binary alone on error; `roll_back:184-197` reuses the signed path. **No signature-skip
  path was found.** The weakness in this subsystem is key *generation* (finding 1) and key *custody
  in CI* (see Site Audit), not verification.
- **Reliability invariants — clean.** `outbox.py:37-44` idempotent enqueue guarded by `delivered`
  plus `rejected`; `delivery.py:47` acknowledges only after `transport.send` returns; failures
  re-raise leaving rows queued; no `clear_attendance` or log-clearing call exists anywhere in `src/`.
- **Adapter seam and the "does not own" boundary — clean.** `adapters.py:8-11` defines the Protocol;
  clock and user reads are best-effort and cannot block delivery (`service.py:436-460`).
- **Packaging — clean** against `DESIGN.md:93-100`, which itself documents the unsigned-with-warning
  fallback.

One caveat on the clean verification above: `verify_signature`'s broad `except Exception` also
swallows a **missing `cryptography` library** as "do not trust." That is correctly fail-closed for
security, but it means a broken dependency silently disables all fleet updates with no diagnostic
distinguishing it from a genuinely bad signature.

### B. Fowler smell baseline

All findings here are **judgement calls**. Three shapes were suppressed because `DESIGN.md` endorses
them: the `DeviceAdapter` Protocol and the deliberately small interface
(*"read device → normalize envelope → enqueue → deliver → report failure"*) are the documented
design with a stated multi-vendor roadmap behind them, and `telemetry_dsn.py` is build-time
injection. The Windows `.ps1`/`.iss` versus Linux `install.sh` split is cross-language and not
extractable — inherent per-OS packaging, not a Fowler smell.

#### 1. Duplicated Code — parallel HTTP and delivery paths (highest cost)

The reliability- and security-critical logic is maintained twice.

- **Retry scaffolding:** identical backoff at `cloud.py:156` and `transport.py:64`
  (`time.sleep(self.retry_backoff_seconds * (2**attempt))`); identical constructor clamps at
  `transport.py:31-33` and `cloud.py:117-119`.
- **Delivery loop duplicated across both operating modes:** `cli.py:101-107` versus
  `service.py:400-423` — the same `enqueue → heartbeat → while batch := outbox.pending(500, deferred):
  deliver(...)`, with `500` hardcoded in both.
- **Connection boilerplate:** `devices.py:58-76` (`probe`) duplicates `devices.py:106-122`
  (`connected`) — same import guard, same `except (ZKError, OSError, socket.timeout) → _explain`,
  same best-effort disconnect in `finally`. `probe` could be written *with* `connected`.
- **Localhost guard spelled out three times — verified.** `cloud.py:20` defines
  `LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}`, but `cli.py:46` and `cli.py:57` inline the same
  literal set instead of importing it. These gate whether **plaintext HTTP** is permitted, so editing
  the named constant alone leaves the CLI on the old rule. `ui.py:84` is genuinely different — a
  port-qualified `Host` header check — and is not part of this duplication.

Cost: a change to retry policy, batch size, deferred handling, or the insecure-HTTP rule must be made
in two modes and can silently drift, and the legacy `cli` path is the easy one to forget — a security
consequence, not just a tidiness one. Fix: extract a `RetryPolicy` and a `drain(transport, outbox)`
helper; express `probe` via `connected`; add one `is_local_host()`. Caveat: `DESIGN.md:79` frames the
env-var path as a bootstrap that is "not the fleet end state," so it may be slated for deletion — but
the duplication is live today.

#### 2. Data Clumps plus Primitive Obsession — device connection parameters

`(host, port, password, timeout)` travels as loose positionals through `adapters.py:17`,
`devices.py:55/103/125/133/139`, and is passed with a magic literal `10` at
`service.py:67/444/448/470/471` — e.g.
`EsslX2008Adapter(device.host, device.port, device.password, 10, timezone_name)`. `password: int` is
a bare int: a device credential with no type. Cost: adding per-device timeout or TLS touches every
signature, and `10` is an unnamed constant repeated five times. Fix: `LocalDevice` already holds
host/port/password — pass it, or a `DeviceAddress` value type; name the timeout.

#### 3. Divergent Change — `service.py`

`ConnectorService` (593 LOC) owns pairing, device CRUD and discovery, branding and logo, polling,
clock and user checks, a diagnostics memo, support-info redaction, status serialization, and
update/probation orchestration. Nearly every feature edit lands here. Fix: split branding,
update/probation and support-info formatting into collaborators. Caveat: `DESIGN.md:11` documents
`service.py` as the UI-free shared core, so being *shared* is justified; the *breadth* is not.

#### 4. Primitive Obsession leading to Repeated Switches and Shotgun Surgery — state strings

Device states are defined at `service.py:37`, re-enumerated in `commands.py:26-33`, `ui_page.py:106`
(embedded JS) and `tray.py:49-58`. Connector states are branched in `tray.py:42-48` and
`ui_page.py:146-150`. Update outcomes are matched in `updater.py:161`, `service.py:561/574/578` and
`commands.py:187`. There is no `Enum` and — verified above — no type-checker, so a new state is three
or four scattered edits with nothing to catch a misspelling. Fix: a shared `StrEnum` across the
Python layers. Caveat: the `ui_page.py` copy is embedded JavaScript and cannot share a Python enum;
that one site is justified.

#### 5. Primitive Obsession — the update-signature path

`updater.py:65` — `verify_signature(data: bytes, signature_b64: str, public_key_b64: str)` takes two
interchangeable base64 `str` parameters, called positionally at `:203` and `:278`; `PUBLIC_KEY` is a
bare str. Swapping them compiles and silently returns "untrusted" — **verified fail-closed, so not
exploitable**, but confusing and undetectable without a type-checker. Punch and verify codes are bare
too: `events.py` maps str↔int in both directions (`:24-25` versus `:43`/`:50`). Fix:
`NewType("SignatureB64"/"PublicKeyB64", str)`; small `Punch`/`Verify` enums.

#### Minor

- Possible Feature Envy — the vendor guess
  `"essl" if (d.model or "").upper().startswith(("X","E")) else "zkteco"` at `service.py:274` belongs
  on the device type.
- Fragile string-surgery coupling — `transport.py:34` derives the heartbeat URL via
  `url.removesuffix('/device-events') + '/heartbeat'`.

---

## Spec

Spec sources: `README.md` (~13 KB, the primary operator-facing contract) and `DESIGN.md`.

### Most consequential: the rollback trigger is far narrower than README promises — verified

`README.md:70-71` — *"A new version then spends 15 minutes on probation: reaching the site once
confirms it. If it keeps failing with errors of its own for the whole probation (not just a lost
connection), it puts the version it replaced back."*

`_probation_faults += 1` appears at exactly **one** site: `service.py:537`, inside the
`except Exception` wrapping `report = self.run_cycle()` in `run_forever`. But `run_cycle` converts
every realistic failure into a **returned report** instead of raising:

- `client.config()` raising `CloudError` is caught at `service.py:301-312` and returns
  `CycleReport(..., "offline", ...)`.
- Per-terminal read failures are caught at `service.py:404`
  (`except Exception ... reachable, problem = False, ...`).
- Delivery failures are caught at `service.py:424-429` (`HTTPError`, `URLError`, `TimeoutError`,
  `OSError`).

So a bad update that breaks pairing, config parsing or delivery reports `"offline"` —
**indistinguishable from a lost connection** — `_probation_faults` stays at 0, and rollback never
fires. Confirmed at `run_forever:531`: `failures = 0 if report.connector != "offline" else failures + 1`
increments the backoff counter but not the probation counter, and `check_probation(report, stop)` is
then called with a report whose fault count never moved. The gate at `service.py:557` is
`if self._probation_faults >= PROBATION_FAULTS and self.updater.probation_over()`.

Only exceptions that *escape* `run_cycle` count — in practice `sqlite3.Error` and the timezone bug
below. `tests/test_rollback.py:156,168,177,187` sets `svc._probation_faults` **by hand** rather than
driving a real fault, so this path is untested. The signing, checksum, self-test and rollback
*mechanics* are correctly implemented (see Standards A, verified clean) — it is the **trigger** that
does not fire.

`README.md:72` already concedes one gap (*"A crash loop before the connector can run is not
covered"*). The gap found here is different and not conceded: failures *after* startup are absorbed
into "offline" and never counted.

### Same root cause breaks a second README promise — verified

`README.md:62-63` — *"Neither this nor the clock check can ever stop punches being delivered."*

`_check_terminal` is called at `service.py:408`, **before** the transport and delivery block at
`:410-429`, and its handler at `service.py:459` catches only `(ProbeError, CloudError, OSError)`.

A `ZoneInfoNotFoundError` raised from the cloud-supplied `approved.timezone`
(`devices.py:125-126` `clock_offset(..., timezone_name)`, with `from zoneinfo import ZoneInfo` at
`devices.py:10`) is a subclass of `KeyError` and is **not** in that tuple. It escapes
`_check_terminal`, escapes `_poll`'s inner `try` (which wraps only the reader loop at `:399-406`, not
`:408`), propagates through `run_cycle`'s comprehension at `:315`, and aborts the whole cycle. That
terminal's already-queued punches and **every later terminal's** go unsent — and it increments
`_probation_faults`.

The two findings interlock: the probation counter is driven not by update health but by an unrelated
terminal-diagnostics bug, while genuine update failures leave it at zero. The intent is documented in
the code itself — the comment at `service.py:404` reads *"a terminal that is off must not stop the
others"* — but `_check_terminal` sits outside that guard.

### `DESIGN.md` is stale about status and roadmap, not wrong about behaviour

- `:5` "Phase 1 implementation" and roadmap items 2 and 3 at `:105-106` are **done**: `pairlink.py`,
  `cloud.pair()`, a per-connector credential in `state.py`; `installer.iss`, `install.sh` plus the
  systemd unit, and `updater.py` (signed, checksummed, self-tested, probation, rollback) with a live
  key in `update_key.py`.
- `:107` roadmap item 4 is substantially done: desired state (`CloudConfig`), reported state
  (`_report_payload` — queueDepth, lastPollAt, lastError, rejectedCount, clockOffsetSeconds), health
  (`status.json`, `tray.py`). Only the `:45` **capabilities** declaration is genuinely absent —
  `DeviceAdapter` is just `name` plus `read_events()`.
- `:73` Phase-2 list is half-shipped: retention (90-day outbox prune) and clock-drift reporting are
  done; encrypted local storage is not (plaintext JSON); there are no per-event acknowledgement IDs.
- `:91` the CI cross-platform matrix claim is **accurate** — verified, `ci.yml` runs linux, windows
  and macos.

**Two real contradictions:**

- `:81-82` promises "a device-scoped **mTLS** credential." What shipped is an HTTP `Bearer` token
  (`cloud.py:130`, `transport.py:44`). mTLS and a bearer token have materially different theft
  characteristics; a reader trusting `DESIGN.md` would over-estimate the credential's protection.
- `:11` says the core is held "with no user interface," which reads as "no UI exists." `ui.py`,
  `ui_page.py` and `tray.py` all ship.

### Operator instructions contradict shipped text

`README.md:27` and `:33` direct the customer to **Academics > Attendance devices**. Every shipped
string says **Settings → Connectors** — `ui_page.py:65,195,216`, `commands.py:42,98`,
`cloud.py:170`. Customers are sent to the wrong menu during onboarding, which is the exact moment
they are least equipped to improvise.

`README.md:61-62` says the people-on-terminal list is sent *"once, and again only when it changes."*
`service.py:452` also re-sends unchanged lists every 24 hours, and `_users_sent` is in-memory, so
every process restart re-sends.

### macOS: README contradicts itself

`:97-98` "macOS support is deferred" versus `:168` "the Windows and macOS builds" and `:185` "macOS
builds do not self-update." `release.yml` publishes linux and windows only, and
`packaging/macos/com.ovigyan.connector.plist` ships with a `REPLACE_WITH_ACCOUNT` placeholder.

### Linux "Copy support info" is partial

`README.md:56` promises a support summary. `service.py:242` tails `home/connector.log`, which only
Windows creates (`run-connector.ps1`). systemd logs to journald, so on Linux the support bundle
silently omits the log — the one artifact support actually needs.

### Scope creep

- `--test-events PATH` (`cli.py:136-140`) is undocumented.
- `update --target` uses `force=True`, bypassing both the hourly hold and the `rolled_back_from`
  block (`updater.py:150-152`). `README.md:184` describes this only as "(support)".
- `policy.minimum` (`updater.py:146`) forces an update **with punches still queued**, contradicting
  `README.md:174` "waits until it has nothing queued." Undocumented.
- The outbox re-sends events once after the 90-day `delivered` prune (`outbox.py:33`) — in neither
  document, and a second duplicate-delivery vector alongside Standards A.2.

### Two update sections, neither complete

`README.md:67` and `:170` do not contradict each other, but each omits half the model: `:170` (beside
the install steps) never mentions probation or rollback; `:67` never mentions the queue-empty gate,
the self-test, or the hourly limit. An operator reading only one gets a wrong mental model of what
the connector will do to their machine.

### Faithful — verified, no findings

The event contract (`DESIGN.md:50-64`) matches `events.py` exactly, including `punch=255`↔`unknown`
and raw preservation. Dashboard claims (`README.md:53-65`), including the 10-minute clock read, the
300 s warning threshold, Set to PC time, discovery and the pair link. Error reports
(`README.md:198-205`) versus `telemetry.py`: IP scrubbing, no locals, bundled source, CI self-test
gate. Windows install claims (`README.md:125-152`) versus `register-task.ps1`, `installer.iss` and the
smoke script — never-timing-out SYSTEM task, ACLs, `ui-token` plus user-readable `status.json`,
settings kept on uninstall, all confirmed. Linux install and uninstall including `--purge`. Legacy
env mode and all 12 `ATTENDANCE_*` variables. Quarantine and retry semantics (`README.md:111-118`)
versus `delivery.py` and `transport.py`.

---

## Site Audit

Tooling pass, repo-only white-box mode. **48/48 applicable checks recorded, 0 gaps — 1 fail, 2 pass,
45 not-run. Exit code 2, which is not a pass.**

**The headline is that this tool barely applies to this repository, and it said so rather than faking
a pass.** The audit is written for static JavaScript/TypeScript sites; this is a Python application
with a PowerShell and Inno Setup release pipeline. Capability matrix: `repo=yes git=yes ci=yes`, but
`dist=no replay=no net=no browser=no links=no`, with the tool noting *"no package.json: replay-class
checks (BLD-1/3, SEC-6/7, CI-2) will record not-run."*

- **PIPE-1..6 — all not-run**, with the reason *"not a single js/ts source file was read: this is not
  'clean release pipeline', it is 'no subject'."* Every check that would have examined
  `packaging/windows/sign.ps1`, `installer.iss`, `register-task.ps1` or `updater.py` had nothing to
  inspect. This is the largest coverage hole, and it lands exactly on the highest-risk code in the
  repo.
- **`ci-hygiene` crashed** with `testFiles is not iterable`. It wrote 2 best-effort lines and
  correctly reported *"treat as exit 3 (not-run ≠ pass), fix and re-run."*
- **INJ-1..3 — not-run**, no standalone renderer.
- **SEC-1/4/5/6/7 — not-run**: no credential-scanner script, no `package.json` gate scripts, no npm,
  no `package-lock.json`.
- **BLD, CTX, SEO, A11Y, LNK, LIVE, HDR — not-run**: no artifacts, no network, no browser, no prior
  ledger.

The one finding the tool produced:

**CI-1 (P2)** — 6 hardening gaps across 2 workflows; 5 third-party actions pinned to mutable tags.
Confirmed and expanded below.

**Not-run is not a pass.** Under the audit's own discipline these 45 items have no conclusion either
way.

### Manual pipeline review (substituting for PIPE, which could not run)

Because PIPE refused the repo, the release pipeline was reviewed by hand.

**The trust root for fleet-wide code execution sits behind an unpinned PyPI resolve.** In
`release.yml`, the `publish` job (`:155` onward, `runs-on: ubuntu-24.04`,
`permissions: contents: write`) holds `SIGNING_KEY: ${{ secrets.CONNECTOR_UPDATE_SIGNING_KEY }}`
(`:167`) and, in that same job:

1. `:159` `uses: actions/download-artifact@v8` — mutable tag.
2. `:174` `python -m pip install -q cryptography` — **unpinned, no hash**, in the step that signs the
   manifest.

The `package` job is the same shape with the Windows certificate: it holds
`WINDOWS_SIGN_CERT_BASE64` and `WINDOWS_SIGN_CERT_PASSWORD` (`:36-38`) and runs
`actions/checkout@v7` plus `actions/setup-python@v7` (`:41,:43`) and
`pip install -e . pyinstaller==6.16.0` (`:63`).

There is **no lockfile** — no `uv.lock`, no `poetry.lock`, no hashed `requirements.txt` — and
`grep -rn "require-hashes"` across the repo returns nothing. `pyproject.toml` exact-pins
`pyzk==0.9`, `tzdata==2025.2` and `pystray==0.19.5` but floats `cryptography>=42`, `sentry-sdk>=2.0`
and `Pillow>=10`. Every workflow install is therefore a floating resolve at build time.

A compromised `cryptography` release, or a malicious transitive dependency of it, executing in the
`publish` job yields the Ed25519 key. Combined with finding Standards A.1, the two ends of key
custody — generation and use — are both weaker than the verification logic that depends on them.

**All third-party actions are on mutable tags, zero SHA pins** (verified, 8 sites):

```
ci.yml:40,41,74,75,123      actions/checkout@v7, actions/setup-python@v7 (x2),
                            actions/upload-artifact@v7
release.yml:41,43,144,159   actions/checkout@v7, actions/setup-python@v7,
                            actions/upload-artifact@v7, actions/download-artifact@v8
```

**The repo violates its own stated runner-pinning policy.** `ci.yml:29` comments *"Runners are
pinned, not `-latest`: ubuntu-latest moves to Ubuntu 26 in Oct-Nov 2026, and the arm64 macOS pool is
capacity-constrained."* Linux (`ubuntu-24.04`) and macOS (`macos-15-intel`) honour this. Windows is
`windows-latest` in all three places it appears — `ci.yml:34`, `ci.yml:69`, `release.yml:33` —
including the job that builds the **shipped** Windows installer and the only place the `.ps1` scripts
receive AST validation.

### Genuine strengths

The release design is sound in ways worth preserving:

- The update manifest is canonical sorted-keys JSON with a per-file SHA-256, and
  `release.yml:170-172` rejects duplicate artifact names (`raise SystemExit`).
- A missing `CONNECTOR_UPDATE_SIGNING_KEY` **fails safe**: `release.yml:167-170` emits a warning and
  `exit 0`, so an unsigned release is *not* offered to installed connectors.
- Least privilege: `permissions: contents: read` at the top of both workflows, with `contents: write`
  scoped only to the `publish` job.
- `concurrency` groups with `cancel-in-progress: false` on release, `true` on CI — correct in both
  directions.
- The CI matrix is deliberate and well-commented: 5 entries across linux/windows/macos and Python
  3.10/3.13, `fail-fast: false`, `timeout-minutes: 20`.
- `ci.yml:47-59` parses every `packaging/windows/*.ps1` with the PowerShell AST parser and throws on
  syntax errors — real validation of scripts that only run on customer machines.
- `make-update-key.py:25-26` refuses to rotate an existing public key without `--force`, warning that
  rotation strands every installed connector.

### How to unlock the not-run items

| To unlock | Requires |
| --- | --- |
| PIPE-1..6 | JS/TS source. **Not achievable for this repo** — the tool cannot analyse a Python and PowerShell release pipeline. Cover this ground by hand, as above, or with a Python-aware supply-chain scanner. |
| SEC-1/4/5 | `--scanner <path>` pointing at a credential scanner; none exists in-repo |
| SEC-6/7, CI-2, BLD-1/3 | a `package.json` and npm lockfile. Not applicable to a Python project |
| CI-1..4 | the `ci-hygiene` crash (`testFiles is not iterable`) fixed upstream |
| INJ-1..3 | `--renderer <path>`; not applicable, the connector renders no web pages |
| BLD-2/4/5/6, CTX, SEO, A11Y, LNK | a `--dist` artifact directory; would require building the installers |
| LIVE-1..3, HDR-1..4 | a deployed URL plus explicit authorization to probe it |
| DOC-2, LIVE-3 | a prior round's ledger — this was round 1 |

---

## Summary

Findings are reported per axis and are deliberately **not** ranked against each other; a change can
pass one axis and fail the other, and merging them would let one mask the other.

| Axis | Findings | Worst within axis |
| --- | --- | --- |
| **Standards** | 11 (2 hard documented-standard breaches, 3 judgement calls, 6 minor, plus 5 smell groups) | `eventId` is derived from a cloud-supplied timezone, so an administrator correcting a terminal's zone rewrites every historical ID and causes duplicate attendance — verified reachable from the ERP's editable `attendance.timezone` column |
| **Spec** | 14 (2 high, 4 documentation contradictions, 4 scope creep, 4 partial) | The rollback trigger cannot fire: every realistic post-update failure is converted to a returned `"offline"` report rather than a raised exception, so `_probation_faults` stays at 0 while `README.md:70-71` promises rollback |
| **Site Audit** | 1 tool finding (CI-1), 45 not-run, plus 3 manual pipeline findings | The Ed25519 fleet update-signing key is used in a job that runs `actions/download-artifact@v8` on a mutable tag and `pip install -q cryptography` unpinned with no lockfile and no `--require-hashes` |

### The two highest-value fixes

Both are small, and both concern the auto-update path — the only mechanism in this product that can
execute arbitrary code as SYSTEM on every school's PC.

1. **Add `update-signing-key.txt` to `.gitignore`** (Standards A.1). One line. No key has been
   committed yet; this closes the window before one is.
2. **Make the probation counter see real failures** (Spec, top finding). Either increment
   `_probation_faults` from the returned report's failure classification rather than only from
   escaped exceptions, or stop collapsing update-caused failures into the same `"offline"` state as a
   lost connection — `README.md:70-71` already promises the distinction exists. Add a test that drives
   a genuine fault instead of setting `_probation_faults` by hand as `tests/test_rollback.py`
   currently does.

Worth pairing with fix 2: move the `_check_terminal` call at `service.py:408` after the delivery
block, or widen its `except` at `:459` beyond `(ProbeError, CloudError, OSError)`. Until then a
malformed cloud timezone on one terminal aborts delivery for all of them, contradicting
`README.md:62-63`.
