# Protocol-1 timestamp names: existing-service compatibility

The canonical implementation is commit `7412f67d99fdcbbab0f11b84e933dd063fba6614`.
These small, separately pinned patches document the equivalent changes needed
when an existing Direct service carries earlier archive-preservation changes.
They are compatibility artifacts, not permission to replace a receiving stack.

- `legacy-writer.patch` supplies UTC start, first source sequence and first frame;
  timestamp names remain disabled unless explicitly enabled on the writer.
- `legacy-projection-reader.patch` and `legacy-archive-reader.patch` allow both
  hash-only and timestamped object names without loosening path containment.
- `legacy-recovery-reader.patch` lets the previous writer continue producing
  hash-only objects while reading both name formats after a partial rollout.

The manifest records exact source Git blobs and SHA-256 digests for every input
and result. Before applying any patch, reproduce its input files from the pinned
blobs, check every digest, run `git apply --unidiff-zero --check`, apply with
`git apply --unidiff-zero` to an isolated copy, and check every result digest.
A full Git history may be required for old blobs.
Do not apply these patches to an unidentified or different runtime.

On 2026-11-07 the canonical implementation was deployed to Staging. The
Production archive and projection readers received their compatible patches
before the legacy writer was switched with timestamp names enabled. The Direct
receiving API, sensor firmware and existing WAV objects were not changed. The
old writer is retained, and a separately prepared compatible recovery writer
can read both name formats. Preserve archive coordination and receiver health
when carrying out any later handover. Never restore a database snapshot over
writes made after a deployment or delete old WAVs as part of this procedure.

Software validation covered protocol-1 timestamps, source sequence, gaps,
ten-minute boundaries, retry identity, 380 Hz PCM/header preservation, old
hash-only reads, protocol-2 naming and archive manifest validation. Natural
Production WAV readback and exact nominal timestamp matching are separate
operational evidence; this document does not assert physical clock accuracy or
that a live sensor test was performed.
