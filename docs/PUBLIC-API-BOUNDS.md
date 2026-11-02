# Public API resource bounds and legacy credential transition

Public authentication never scans Gateway password hashes. Modern `gmk_*` keys
resolve their UUID directly. Enrolled legacy keys resolve an indexed SHA-256
digest. A legacy bcrypt key that has not been enrolled must identify its Gateway
with `X-Gateway-Id`; ordinary ingest can also use its validated `gateway_serial`.
An identity is a lookup hint, never proof of possession or zone authority.

The common verifier performs at most one bcrypt operation per cold request,
allows one operation in flight, and rejects excess work with 429 and Retry-After.
Every actual operation consumes the budget, including success: five per transport
peer and thirty per process per minute. It never trusts forwarded peer headers.
The five-minute, 1024-entry success cache rereads activation and the current hash.
Digest verification does not spend the password budget. Fleet size does not
increase the number of password verifications for a request.

## Staged rollout without changing deployed secrets

1. Inventory existing Gateway clients and legacy credentials read-only. Record
   each Gateway UUID. Unenrolled keys without an identity hint intentionally
   fail with 401; do not deploy the backend before the compatible clients.
2. On Staging, install the matching GreenMindRPIv1 client **and standalone agent**.
   Its shared `gateway_headers.py` must be installed beside the agent. The header
   addition is compatible with the preceding backend and does not rotate keys.
   Verify all configured HTTP, WAV, OTA and optional provisioning/WS callers.
3. Prepare the database index independently of the Alembic revision:
   `python -m app.gateway_key_migration prepare-index`. PostgreSQL uses
   `CREATE INDEX CONCURRENTLY`; no invitation, feature, archive or deletion
   migration is requested. An invalid index must be repaired before release.
4. Start the matching backend only after client coverage and index acceptance.
   Verify that existing keys still authenticate with their Gateway identity.
   Any older Staging source overlay must include the middleware and common
   verifier changes, rather than reintroducing an old authentication copy.
5. Enroll individual known legacy keys using an owner-only local secret file:
   `python -m app.gateway_key_migration enroll --gateway-id UUID --key-file PATH`.
   The command verifies one current credential, checks activation, and replaces
   only its matching hash in a compare-and-swap transaction. It does not change
   the device secret, issue a new key, print a key, scan the fleet or re-pair.
   The file must be a regular file owned by the invoking user, mode 0600 or
   stricter, and must not be a symlink. Never put the secret in command arguments.
6. Confirm enrollment with `python -m app.gateway_key_migration status` and
   authenticate the same secret without a hint after clearing caches/restarting
   the test process. Repeat activation and credential-change revocation tests.

Plain bcrypt credentials longer than 72 bytes require a separate controlled
rotation; the command refuses to choose one of multiple truncated spellings.
Enrollment is idempotent and refuses a disabled Gateway or a concurrent hash/
activation change. Ordinary authentication does not commit or mutate credentials.
New registration/re-provisioning retains existing possession and tenant checks.

Keep the identity-capable clients during rollback. Before rolling back the
backend, verify that the selected older version supports enrolled unprefixed
digest keys; a pre-transition version does not. Do not automatically downgrade
credential hashes or restore stale activation flags.

Production deployment, enrollment, client installation and any OTA distribution
require the existing separate operational release. Peter's four Direct office
boxes remain excluded from OTA until the accepted replacement and agreed handoff.
This change neither enables OTA nor contacts devices or the temporary live site.

## Request and field limits

The receiving application's ASGI middleware counts actual body bytes before JSON
decoding, validation, dependencies and endpoint rate decorators. Missing, chunked
or misleading Content-Length cannot bypass the byte counter. Duplicate or invalid
Content-Length is rejected with 400; unsupported Content-Encoding with 415.
Oversize requests return 413. Limits do not depend on JSON Content-Type spelling.

| Request | Byte cap |
|---|---|
| Ordinary requests, including public observation/evaluation JSON | 64 KiB |
| Gateway ingest batches | 16 MiB |
| WAV multipart upload | configured `MAX_WAV_UPLOAD_BYTES` plus 64 KiB multipart overhead |
| Photo multipart upload | 10 MiB plus 64 KiB overhead |
| Firmware multipart upload | 16 MiB plus 64 KiB overhead |
| Gateway app-release multipart archive | 100 MiB plus 64 KiB overhead |

JSON buffering is bounded before entering the parser. Multipart bodies retain
Starlette's spooling/streaming behavior and are counted while being consumed,
before the upload handler runs. Existing file-size, decoded-image, WAV, ownership,
signature and authorization checks still apply; the request cap does not replace
them. A proxy can impose a smaller cap. Increasing WAV configuration requires
matching edge-proxy acceptance; it does not increase the JSON cap.

Public notes are limited to 4000 characters, condition to 50, and leaf color,
soil condition and suspected stress type to 100. Public access IDs are limited to 128
characters; canonical, hexadecimal, braced and URN UUID forms still reach the
existing UUID validation and malformed short IDs retain its 400 response. Anomaly lists are limited to five
entries, retaining existing score, enum, empty-list and `none` combination checks.
Errors remain normal 422 validation responses; data is never silently truncated.

The application-level limits also protect the firmware hostname and alternate
proxies routing to this app. The independent Direct receiver retains its own
binary/metadata/pairing limits; raw uploads and historical readers are not
rerouted through this change.

## Existing dependency audit gate

The first CI run also flagged preceding production dependency pins. The patch
updates Pillow to 13.0.0, PyJWT to 2.15.0 (retaining the current receiver
version), Next.js and its lint configuration to 16.4.4, and resolves
Sharp 0.35.5 and source-map-js 1.2.2 in the lockfile. Other existing newer pins
are retained. The application byte cap does not replace maintained image/JWT
parsers. Regression checks cover normal images, login/session behavior and the
frontend build; production dependency audits must pass before integration.

Sources: [PyJWT advisory](https://github.com/advisories/GHSA-pxh4-856f-4h89),
[Pillow release and security notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html),
[Next.js advisory](https://github.com/advisories/GHSA-vcvr-r3jv-pc5j).
