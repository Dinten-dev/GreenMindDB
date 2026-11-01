# Direct first-accept assignment

Status: local implementation; not installed or enabled in production.

`DIRECT_CAPTURE_ASSIGNMENT_ENABLED` defaults to `false`. No automatic schema
creation, Gateway Alembic migration, credential change or data backfill occurs.
Direct currently uses explicit `provision --create-schema` / `Base.create_all`,
which cannot migrate existing columns. This change requires no new SQL column:
it stores a strictly versioned, server-owned field in existing Session JSON.

When enabled, `accept_chunk` creates `_server_capture_assignment_v1` only for a
genuinely new Session, after authenticating and locking its Device. Snapshot,
original PCM, sequence identity and quota commit together before acknowledgement.
Client metadata still forbids extra fields. Existing sessions never gain this
field; known sessions reject organization/zone/mode changes before new PCM writes.
An exact already-persisted duplicate still receives its normal acknowledgement.

The proof means **authenticated Device assignment at first server acceptance**.
It is not proof of physical plant attachment or ownership at an earlier sample
timestamp. The trusted boundary is the authenticated receiver and source database;
SHA-256 detects binding changes, and is not a signature against source administrators.

The original PCM client configuration and chunk digest remain unchanged. New
revision manifests add `metadata_version=2` plus the same captured snapshot.
Content-addressed WAV keys incorporate its digest; S3 metadata records that
digest and organization. Mutable S3 headers alone never establish provenance.

Scientific COPY v3 requires identical validated Session and Revision snapshots.
It routes by captured organization, retaining current assignment as observation.
Older COPY v1/v2 and old manifests remain UNKNOWN/quarantined. Known context
alone never enables deletion: completeness, dependencies and projections remain
false, and the COPY receipt explicitly forbids deletion. Parent restoration
authorizes the requested historical organization only against the validated
captured Session/Revision pair. Restored Device/Enrollment masters must still
exactly match the archive-time observation, even when its organization differs.
Neither historical masters nor current organization assignments are synthesized
or rewritten. Existing SQL/API tenant-query routing is not automatically replaced
by this WAV metadata contract.

## Rollout order

1. Prepare compatible readers, assembler, payload reconstruction and archive runtime.
2. Verify actual isolated PostgreSQL and installed-image compatibility.
3. Keep capture disabled while updating every Direct receiver/worker instance.
4. Verify receiver identity, healthy ACKs, source leases and unchanged limits.
5. Enable capture only after all consumers support new metadata.
6. Observe a genuinely new authenticated Session and archive its proof.
7. Retain old sessions and originals; do not manufacture historical assignments.

No deployment, restart, source DDL or permission operation is supplied here.
Existing backup schema and Master dumps already include Session JSON. The WAV
metadata allowlist has a new digest; its previous exact digest remains supported
for old-file restoration, but cannot label a new server-capture row.

## Rollback

Disabling capture stops creation of proofs for future new sessions. Existing
known sessions keep their immutable snapshots and conflict checks. Keep the new
parsers/assembler deployed once any proof exists: an old receiver compares the
expanded Session JSON directly with client config and can reject valid retries
of further chunks; an old assembler cannot preserve the new provenance contract.
Use a compatible forward fix rather than reverting only one process. Never
remove captured fields, rewrite old manifests, or relabel UNKNOWN recordings.
