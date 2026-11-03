# Bounded archive-export copies (S7)

This change belongs to the maintained optional export service in the current
Receiver history. The Step-4 publication history does not contain that service;
this patch does not import the archive architecture into that history.

## Lifecycle

Raw spools are removed after each source, including read/hash/write/close
failure. Complete ZIPs retain the existing names and manifests. Their temporary
hardlink names are removed after publication. A capacity pause keeps only the
persisted, verified ZIP parts needed for resume. Failure/cancellation removes
generated copies. Original recordings and archive objects are read only.

Every existing worker entrypoint reaps before checking for an empty queue,
including the archive-host wrapper and Docker dispatcher. The existing timer
can therefore clean expired ready jobs even when there is no new export.
Expiration remains 24 hours after job creation. Failed/cancelled copies are
eligible immediately; unreferenced UUID export copies without a job have a
positive provenance journal; a UUID filename alone never authorizes deletion. Unknown files, original WAV files and restoration proof
JSON are preserved. Job history/schema are retained.

## Locks and downloads

An exclusive directory lock serializes admission, storage accounting and short
metadata/file changes. A fixed set of 64 private process-lock stripes protects
each job throughout worker execution or a download. Collisions conservatively
delay unrelated jobs rather than risking a live file.

Downloads retain a shared lease and the validated open inode until ASGI
completion or disconnect. Existing Range downloads remain supported. Reaping
skips a live lease even past expiry and retries next cycle. Cancelling queued,
working or ready jobs is effective: workers observe cancellation, new downloads
stop, and current downloads finish before files can be removed.

All workers and API processes must be updated together; old processes do not
participate in these locks. Checkpoint preparation also takes the directory
lock. Ephemeral lock/scratch directories are excluded from checkpoints and
merges; older checkpoint schemas remain compatible.

## Capacity and filesystem boundary

Admission and writes count all files retained under the private export root,
including ready/failed/cancelled copies, raw spools, ZIP metadata, unreferenced
files and SQLite metadata. Hardlinks are counted conservatively. Queued/working
jobs reserve output, ZIP overhead and up to three source-sized temporary copies, including broker readback. A cancelled
job retains its reservation while the worker still holds its lease.
Limits are 8 GiB total and 4 GiB per user; the existing two-open-job and bounded
input limits remain. All write checks and admission decisions share the lock.

New archive-read fallback copies are scoped to `.scratch/{job-id}` inside the
export root, counted in the same budget and cleaned with that job. Export reads use a separate private broker operation with its own tracked
`export-copies` root, a 320 MiB budget and crash reaping at startup/next read.
Other archive readers retain their existing scratch behavior. Legacy/unattributed scratch
outside the tracked roots is never guessed or automatically removed: inventory it
separately during the controlled cutover. No claim of production disk cleanup
is made by this repository patch.

The root must be an existing private, owned, canonical directory. It must not
overlap configured original/Direct/visual archive storage. Cleanup uses directory
descriptors and no-follow checks, persisted file/directory inode provenance and
legacy persisted ZIP references. A matching filename alone never suffices. Symlinks/special files fail closed. It never calls object deletion,
enables original retention, follows archive paths or removes unknown evidence.

## Controlled introduction

1. Rehearse this exact build with a private disposable export directory.
2. Stop/drain all old export workers and private API writers/downloads.
3. Preserve a consistent private checkpoint. Inventory old external scratch and
   resolve unattributed files separately; do not delete originals or shared
   reader caches. Confirm the intended state directory and service UID.
4. Update API, all worker wrappers and checkpoint tooling together. Keep the
   export feature disabled until private-directory/lock/ownership checks pass.
5. Exercise Gateway and Direct ZIP verification, pause/resume, cancellation,
   expiry, Range/disconnect and quotas before enabling only the approved export
   schedule. Original retention/deletion flags remain disabled. The fixed root Docker
   dispatcher delegates reaping to the actual state-directory UID/GID with a
   minimal environment; it never creates root-owned worker metadata.

Rollback requires stopping all new participants first. Do not run old workers
alongside new cleanup. This task does not perform deployment, enable schedules,
clean production files, merge branches, modify firmware or touch Peter's boxes.

## Tests

`test_export_cleanup.py` covers residual copies, expiry, unknown/original
canaries, quotas, cancellation, scoped fallback, symlinks, crash-lock release,
active ASGI downloads/disconnects, Range and concurrent admission. Existing ZIP
hash/resume, Gateway/Direct fallback, real broker socket and checkpoint tests
remain. Entrypoint tests cover empty queues and compile deployment scripts
without executing their privileged operations.

Existing unregistered legacy partial copies require a reviewed inventory of
exact filenames, byte sizes, SHA256 digests and `generated_export_copy: true`.
`python -m app.visualization.archive_export_reap --state <private-root>
--adopt-reviewed <inventory.json>` validates that proof before journal admission
and cleanup. It is an explicit maintenance action, never automatic guessing.
Without that proof, even old UUID-shaped foreign files remain unchanged and
count toward the storage limit. The existing jobs table is unchanged; new
private provenance tables track only generated file/directory inode identities.
