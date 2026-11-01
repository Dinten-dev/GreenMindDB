# Direct visual parent, observed COPY V2

`observed-direct-wav-context-copy-v2` adds the actual `direct_visual_segment`
row to Direct WAV scientific metadata. Its seven explicitly selected fields are
`segment_id`, `source_revision`, `seconds`, `source`, `updated_at`, `error` and
`retry_at`. Error text is private archive content and bounded to 4096 UTF-8 bytes.
No parent row, retry value, timestamp or assignment is inferred from WAV samples.
The row must refer to this exact segment and cannot name a future revision.

The current published-copy producer reads the row twice through its existing
scientific-source change fence. Restoration inserts and fully typed-compares the
captured row in the same isolated-target transaction as its `direct_segment`,
BEFORE registering/fingerprinting masters or loading SQL children. Existing rows
are never overwritten. A V2 `null` parent and old V1 archives still support WAV
restoration, but do not prove support for `direct_visual_point` children. Missing
parents cause an explicit HOLD before child insertion; foreign keys remain on.

V2 remains COPY-only and quarantined. It cannot authorize original deletion.
Legacy observed V1, historical Direct and Gateway V2 retain their old meanings.
The standard strict source/deletion producer does not acquire this COPY format.

## Existing-archive augmentation

For an old archive missing the parent, a new observed metadata bundle may reuse
its existing immutable WAV references. Authenticate and fully verify the old
manifest, Parquet and WAV first. Observe the current seven-field parent under a
bounded read-only source transaction; independently re-read and typed-compare the
current revision and selected SQL rows with the already archived rows. Require
the same segment, revision, tenant and unchanged old scientific metadata. Record
this as a NEW observation, never as proof that the parent state existed at the
old capture time. A changed source revision or selected SQL row holds the repair.

Write and publish only the new metadata Parquet. Retain the exact old recordings,
object snapshots and WAV references; publish the separately versioned manifest
through existing `pipeline.complete_staged_wav`, which independently reads every
referenced archive artifact. No new source WAV download or WAV publication is
required. Existing SQL and schema/master/provider artifacts stay unchanged.
Restore those archives normally into a fresh, independently marked isolated DB,
then the augmented WAV parents, then ordinary mutation-fenced SQL sessions and
full typed final audits. Never reset/remove an existing restore fence to insert
an additional master after its baseline. Keep the failed target and journals
until complete typed replacement and exact safe cleanup proofs exist.

## Verification limits

The regression suite exercises actual SELECT/Parquet and SQLite transactions,
foreign keys, rollback, absent/conflicting parents, original-state readback and
COPY-only deletion rejection. It does not establish PostgreSQL deployment,
current source observation, Box publication, fresh-target restore or production
readiness. Those bounded native steps remain necessary.
