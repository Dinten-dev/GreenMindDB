# Typed Legacy pairing (S3/S6)

Pairing codes carry an explicit `gateway` or `sensor` purpose. The existing
six-character code and request formats stay compatible. Responses additionally
include `purpose`.

| Issuer | Purpose | Redeemer |
|---|---|---|
| `POST /api/v1/gateways/pairing-code` | gateway | `POST /api/v1/gateways/register` |
| `POST /api/v1/zones/{id}/pairing-codes` | gateway | `POST /api/v1/gateways/register` |
| `POST /api/v1/sensors/pairing-code` | sensor | `POST /api/v1/gateways/{id}/sensors/register` |

The zone alias retains its Gateway issuance purpose. Sensor callers use the
sensor endpoint. Optional experimental provisioning jobs accept only sensor
codes, including their pending/update joins. Authenticated `/sensors/claim`
remains a separate administrative enrollment flow without a code.

Both Legacy redeemers lock the unique code row, refresh its state, and validate
purpose, expiry, unused status and the applicable zone. Device creation or
update, credential rotation and code consumption commit together. Conflicts
roll back everything and preserve an unconsumed code. Used codes return 400;
there is no MAC-only retry exception. Existing Gateway reprovisioning still
requires its current key, an active Gateway and the same organization. An
additional Gateway lock prevents simultaneous rotations using a stale key.
Sensor registration rechecks changed credentials after obtaining that lock;
same-key digest enrollment remains valid. Issuance also avoids code strings
still referenced by orphaned provisioning jobs after metadata retention.
Same-Gateway sensor registration with a fresh sensor code remains supported.
Direct uses its existing separate code model and authenticated retry rules.

## Controlled schema upgrade

There are two maintained schema histories: publication head `0023`, and the
current receiver's existing `0024`/`0025` auth/result history. The independent
`app.pairing_upgrade` capability upgrade works on their common `pairing_code`
table and does **not** alter `alembic_version`, run unrelated migrations, or
create conflicting Alembic heads.

1. Test the exact application/schema combination on a disposable database.
2. Back up the database and stop/drain **all Legacy pairing issuers and
   redeemers**, including optional provisioning workers. Prevent a mixed old/new
   worker rollout: old redeemers ignore code purpose.
3. With the matching new backend environment and intended database configured:
   `python -m app.pairing_upgrade prepare`.
4. Verify `python -m app.pairing_upgrade status` and activate matching new
   workers only after successful preparation.
5. Issue fresh codes and smoke-test both authorized device workflows.

Preparation takes a short exclusive lock on `pairing_code` in one transaction.
It labels historical untyped rows `legacy`, invalidates every unused untyped
code (also expired ones), preserves prior consumption timestamps, and enforces
a non-null purpose without a default. Historical code rows remain for audit.
Repeated preparation preserves fresh typed unused codes. Unknown purpose values
abort preparation without a partial upgrade. Old issuers omitting the purpose
fail closed. Status prints counts only, never codes or credentials.

Do not roll back to old pairing workers against the new schema. Disable Legacy
pairing and deploy a corrected compatible build if rollback is necessary;
invalidated codes must not be reactivated. Direct code tables, data ingestion,
archives and firmware are outside this upgrade. No production execution or OTA
is part of this change; Peter's office boxes remain protected.

## Verification

`tests/test_typed_pairing.py` covers both confused-code directions, normal
registration, lowercase normalization, wrong zones, used/expired/legacy/unknown
codes, existing-key proof, rollback, issuance collisions, the zone alias and
optional provisioning purpose checks.

`tests/test_pairing_postgres.py` exercises real concurrent redemption, stale-key
rotation, identity conflicts, lock-wait expiry and the idempotent schema upgrade.
Revision-marker cases establish that the pairing upgrade leaves existing
`0023`/`0024`/`0025` markers untouched; they do not claim to rerun the complete
auth/archive migration histories. Existing Direct PostgreSQL concurrency and
safe-retry tests continue in the same CI job.

Local SQLite checks cannot prove PostgreSQL row-lock behavior. The PostgreSQL
tests require the dedicated localhost `direct_test` database supplied by CI;
each uses an isolated disposable schema.
