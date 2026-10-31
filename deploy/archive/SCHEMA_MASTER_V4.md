# Schema/master backup V4

Production's observed Main schema is Alembic0023. `auth_session` and
`access_invitation` are absent; `users` and `form_submission` exist. This contract
backs up that real schema without creating authentication tables or changing an
Alembic head. It never labels a V2 file as V3 or V4.

New Gateway captures use V4. Exactly11 existing master tables are required only
when the explicit public-schema catalog proves both auth relations absent and
the sole Alembic head is0023. All other existing V3 authority/master tables,
including users, permissions, assignments and form_submission, remain mandatory.
One auth table alone, a view/sequence under an auth name, an absent/multiple/
unsupported head, or missing permissions/dependencies blocks capture. Both auth
tables require the complete13-table scope. Known full-scope heads are0023,
0024 and0025; later unknown heads fail closed pending explicit compatibility.

The proof binds exact heads, both relation-presence facts, and catalog hashes.
Existing auth-table shapes include columns/defaults, primary/foreign/unique/
check constraints and indexes. Master rows retain the existing ordered typed
content checksums and64MiB streaming budget. A consistent exported snapshot
spans both native dumps and typed row proof. Separate short read-only repeatable
read catalog captures before and after detect concurrent authority-schema/head
changes. Their additional connection is closed and never changes source data.

V4 has exactly four files:

1. Parquet index, with explicit `schema-master-v4` format and scope/hash bindings.
2. AES-GCM-encrypted schema dump.
3. AES-GCM-encrypted master-row dump.
4. AES-GCM-encrypted proof-authentication record, at most256KiB plaintext.

The fourth record authenticates the complete proof, source, capture timestamp,
proof hash, and both original dump identities (including plaintext and cipher
hashes). Restore authenticates this record and both dumps before any SQL client
or target connection. Source/capture/scope/hash discrepancies stop restoration.
After isolated SQL restoration, actual catalog presence/shapes, exact head and
all typed master values must match. No success proof is returned on mismatch.

`require_current` requires all four V4 descriptors and a fresh matching live
schema scope. Both existing WAV-deletion backup gates obtain that scope through
the already configured, source-matched Gateway backup binding. If authentication
tables or the head change after capture, a prior absence backup cannot continue
authorizing deletion within its24-hour window. Missing bindings fail closed;
this change creates no roles, credentials, permissions or profiles.

Existing complete13-table Gateway V3 proofs remain accepted under their existing
source/readback/currentness contract. Existing V2/V3 three-file archives remain
restorable in their original explicit versions. V2 never satisfies current
backup eligibility. Direct retains its independent three-table V3 scope; no
Main-Alembic or Main-authentication assumption is applied to its database.

Original client/worker resource limits, encrypted64MiB part bound, snapshot
currentness, seven-day WAV reserve and disabled deletion flags remain unchanged.
No full-production database export is enabled. Local SQL/crypto/Parquet tests
and mocked native-client orchestration do not establish a native production
pg_dump/restore, live throughput or deployment acceptance.
