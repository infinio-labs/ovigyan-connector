# IDeS device connector design

## Status

Phase 1 implementation: eSSL X2008/ZK-compatible TCP pull adapter, signed
HTTPS delivery, durable local outbox, and cross-platform test matrix.

## Responsibilities

The connector is a deep edge module with a deliberately small interface:

```text
read device → normalize envelope → enqueue → deliver → report failure
```

It owns:

- vendor protocol and device connection
- local device credentials
- event batching and retry
- local cursor/deduplication queue
- connector process health

It does not own:

- tenant or branch authorization
- student/teacher identity mapping
- punch interpretation policy
- attendance corrections or ledger decisions
- notification decisions

## Adapter seam

The common runtime depends on `DeviceAdapter.read_events()`. A manufacturer
adapter converts device records into `EventEnvelope`. Future adapters can use
TCP pull, HTTP push, ADMS, vendor cloud, USB, or file import without changing
the outbox or cloud contract.

The adapter declares model capabilities in the next phase. Unsupported
operations are explicit; the runtime must not assume that every device can
read users, write users, set its clock, or clear logs.

## Event contract

```json
{
  "eventId": "device-native-id-or-content-hash",
  "deviceUserId": "42",
  "timestamp": "2026-09-10T03:45:00.000Z",
  "punch": 255,
  "verify": 1,
  "raw": {}
}
```

`eventId` is stable per device event. `punch=255` maps to `unknown`; the
connector preserves the raw value. IDeS deduplicates by device and event ID,
then applies an administrator-created user mapping. Unmapped or ambiguous
events never create ledger rows.

## Reliability

SQLite WAL is the local outbox. Enqueue is idempotent. A batch is acknowledged
only after a successful HTTP response. A failed request leaves rows queued.
The device log is never cleared automatically, so rereads are safe through
cloud idempotency even after connector loss or restart.

Phase 2 adds a connector cursor, bounded retention, encrypted local storage,
clock-drift reporting, and explicit acknowledgement IDs from the cloud.

## Security

The first compatibility path uses an environment-held HMAC secret and exact
raw-body signing. It is a bootstrap mechanism, not the fleet end state.

Phase 2 replaces the shared environment secret with one-time pairing and a
device-scoped mTLS credential. The connector makes outbound connections only;
the cloud never opens a connection into the branch LAN. Updates are signed,
versioned, staged, and rollback-capable.

## Packaging

The repository is independent because it produces OS-specific artifacts and
has a different release cadence from the Next.js control plane. The web app
owns the versioned event contract and server-side projection. CI tests the
connector on Windows, macOS, and Linux. Production releases must publish signed
per-OS installers; macOS distribution is deferred until Apple Developer signing
and notarization are enabled. The current workflow produces unsigned standalone
executables rather than installers, so it does not yet meet the production
release requirement. Customers do not install Python manually.

## Roadmap

1. Validate X2008 against the client terminal.
2. Add one-time pairing and per-connector credentials.
3. Add Windows/macOS/Linux signed installers and auto-update rollback.
4. Add capabilities, health, desired/reported state, and remote diagnostics.
5. Add ZKTeco ADMS, eSSL push, Suprema, Anviz, and Hikvision adapters.
