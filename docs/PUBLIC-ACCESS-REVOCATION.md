# Revocable public plant access (S4/S5)

Each plant has at most one active public observation grant. Application
transactions serialize on the plant row; a partial unique index additionally
enforces the invariant in PostgreSQL. Issuance returns the existing active
grant, including under concurrent requests.

Revocation disables every grant for that plant and all derived sessions,
including sessions of previously inactive grants. The shared public validator
checks the live parent, its revocation timestamp, matching plant identity,
session state and expiry. This covers public context, observations, evaluations
and photo uploads. Observations, evaluations and existing photos are retained.

All public operations hold the same plant lock through their transaction and
storage work. A revocation waits for an already validated operation to finish;
after revocation completes, subsequent operations fail. Session issuance cannot
insert a live session after revocation cleanup. PostgreSQL `FOR NO KEY UPDATE`
allows the foreign-key reference locks required by observations and sessions.
Operations for the same plant serialize; a slow photo upload can delay its
revocation. Existing upload size limits remain in force.

## Read and explicit reissue

- `GET /api/v1/plants/{id}/observation-access` reads the active grant, the latest
  inactive grant, or `null`; it never issues a grant.
- Existing owner/admin and organization/zone checks apply to every management
  route, including this new read endpoint.
- `POST` without a flag still creates the first grant or returns the active one.
  When only historical inactive grants remain it returns 409.
- `POST .../observation-access?reissue=true` explicitly creates a new grant after
  revocation. It never reactivates old identifiers or sessions.
- `DELETE` revokes all grants/sessions. Repeated revocation with existing grant
  history returns its inactive state; a plant without grant history returns 400.
- An ambiguous set of active grants fails management reads/creation with 409
  until controlled repair or complete revocation; no arbitrary winner is chosen.
  Public session issuance/validation also fail closed on ambiguous legacy grants
  before schema preparation.

The detail and print pages now use the read endpoint. The detail-page button
requires confirmation before explicit reissue. The print page cannot issue a
grant and shows an inactive-access message instead of a usable QR code. Older
clients that automatically POST on page load must be updated: after revocation
they receive 409 rather than silently enabling public access again.

## Controlled schema upgrade

`app.observation_access_upgrade` upgrades the common access/session tables
without changing `alembic_version` or the maintained `0023` versus `0024`/`0025`
histories. This does not run on application startup.

1. Rehearse the exact build/database combination and take a database backup.
2. Drain/stop all old public-access workers, including session consumers and
   management writers. Old validators ignore parent state; no mixed rollout.
3. Using the matching new backend and intended database configuration, run
   `python -m app.observation_access_upgrade prepare`.
4. Check `python -m app.observation_access_upgrade status`, then start matching
   backend/frontend versions. Verify authorized normal flows and revocation.
5. Explicitly reissue affected grants after review and replace their printed QR
   codes. Preparation does not silently issue replacement public identifiers.

Preparation locks both tables in one transaction. For every plant with multiple
active grants it disables **all** of those grants. It also normalizes grants
that are active despite a revocation timestamp, disables sessions without a
live matching parent and creates the partial unique index. Unambiguous active
grants and historical records remain; reruns preserve newly reissued grants.
Status prints counts only, never public identifiers or session tokens.

Do not roll back to old public-access workers. Disable public access and deploy
a compatible corrected build; do not reactivate invalidated grants. No actual
database upgrade, deployment, merge or OTA is included in this repository work.
Peter's four office boxes and GmLive are untouched.

## Validation

`tests/test_public_access_revocation.py` covers both original bypasses, every
public consumer, live parent/plant checks, explicit reissue, tenant boundaries,
inactive-parent cleanup, rollback, history retention and database uniqueness.
The existing public observation flow also runs in the native CI job.

`tests/test_observation_access_postgres.py` exercises real concurrent issuance,
creation/revocation races, session issuance/revocation races and revocation
waiting for an already validated write. It checks actual PostgreSQL lock waits,
foreign-key inserts, unique-index enforcement and the idempotent upgrade with
ambiguous/historical grants and preserved `0023`/`0024`/`0025` markers. Marker
preservation does not claim full auth/archive migration acceptance.

Frontend regression tests cover read-only loading/printing and confirmed versus
cancelled reissue, and uncertain access status after load errors. Those errors
leave revocation available and do not claim inactivity. The upgrade status check
verifies the actual table, column and active predicate of the unique index.
SQLite tests cannot prove PostgreSQL lock semantics; native
tests use isolated schemas in the dedicated localhost CI database.
