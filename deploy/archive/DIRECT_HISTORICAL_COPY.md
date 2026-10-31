# Historical Direct revisions: COPY only

Tiered `publish_wav`, bounded `stage_wav`, and their scheduling catalog explicitly
request `copy_published_history=True`. The ordinary catalog and every writer
retain the existing current, sealed, published-revision rules. A writer cannot
request the new copy mode.

The copy catalog can enumerate and copy already published **older** revisions
without requiring them to equal today's latest published revision. It requires
an existing undeleted, verified revision, exact source/session/bucket/revision
identity, canonical manifest checksum, complete contiguous original frames, no
missing ranges, original configuration/calibration, complete original channel
features and their extractor/parameter identities. It uses those stored feature
results, not today's calculation version. Partial or corrupted historical
recordings are blocked. Current revisions use exactly the existing strict path.

No new assignment field, producer, schema, permission, account, or backfill is
introduced. Existing manifests remain byte-for-byte unchanged. Their original
manifest, WAV checksums, features, calibration and sample layout remain archived.

Existing source models do not preserve immutable capture-time organization,
zone, plant or enrollment history. Historical archives therefore distinguish:

* `revision`: the original verified manifest and its original verification data.
* `session`: original config and capture mode from that manifest.
* `observed_segment`: source metadata observed during this archive operation.
* `observed_assignment`: today's source-device/enrollment values, explicitly
  labeled `observed_at_archive`. Their organization retains the existing current
  access-routing semantics; it is not asserted as the capture organization.
* `capture_assignment`: explicitly `unknown`, with a quarantine reason. No plant
  value or historical owner is inferred from today's device.

The physical archive sensor folder uses the stable Direct device identity;
today's hardware MAC is not presented as historical acquisition provenance.
Original WAV keys and the existing legacy receipt/read-broker format remain
usable. Completed transfers can re-publish the same legacy URL receipt after
interruption without downloading source WAVs again.

Historical envelopes carry the unique scope
`historical-direct-revision-copy-v1`. Their candidate has `quarantined=True`,
`complete=False`, `dependencies_complete=False`, and
`projections_verified=False`; original feature verification is retained. A
successful `wav_copy.advance.complete=True` means all package bytes were copied
and independently read back; it does not claim complete capture context.

These flags can never satisfy common deletion eligibility. Additionally, every
source-deletion entry point rejects the historical copy marker, and copy-mode
adapters reject all deletion operations even for current recordings. Snapshot
verification cannot turn a historical copy into a deletion permit. Existing
seven-day snapshot reserve, backups, writer work leases and deletion flags are
unchanged. Retiring historical S3 object versions remains separately blocked
and requires its own fully proved contract.

Copies retain existing owner/source leases, resource budgets and resume logic.
Metadata is checked again before publication, including current observed source
state; all original source-object snapshots must still match. Changes during
copying prevent final publication. Completed content-addressed files may remain
as harmless unpublished partial work, but no completion/deletion proof is issued.

Local tests exercise real SQL reads, Parquet roundtrip and complete WAV copy,
resume without repeated copy, legacy receipt lookup, unchanged current-revision
metadata, unknown-context quarantine, source/object changes, invalid historical
features/frames/checksums and explicit deletion rejection. No native production
copy, upload, deployment or deletion acceptance is implied.
