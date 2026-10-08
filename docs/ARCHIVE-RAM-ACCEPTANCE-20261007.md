# Archive acceptance with bounded memory

Deletion, pruning, source retention and legacy-null removal remain disabled.
This release coordinates archive jobs; it does not authorize removal.

The adaptive launcher now reserves **at least 512 MiB**, or a larger configured
reserve, or 20% of available RAM, whichever is largest. Its older 128 MiB floor
could override the configured guard; this was found and corrected during the
operator review. Load is capped at 2.4 or the stricter configured limit.
Resumable pauses return 75, accepted by the archive job service; their report
still says paused/blocked/incomplete and does not claim a completed backup.
Real metadata failures remain nonzero failures.

The deployed EnvironmentFile supplied an older PYTHONPATH, overriding systemd's
release setting. Actual scheduled-run logs exposed this import failure. The
adaptive launcher now selects its own repository/flat-package backend before
importing archive modules. A subprocess regression test supplies both a stale
PYTHONPATH and stale working directory. No credential file is rewritten.

## Runtime changes

- One private host lease covers copying, catalog extraction and ZIP generation.
- Contending jobs exit without opening SQL/S3 clients. Requests expire after five
  minutes. Copying yields between files; exports yield after sixty seconds at
  the next verified WAV boundary. Existing timers retain their schedules.
- The export dispatcher checks the SQLite queue before launching Docker. Its
  existing privileged Docker access runs through a fixed root launcher; the
  actual export container remains UID 996/GID 986, capped at 64 MiB and 10% CPU.
  The dispatcher is capped at 128 MiB. No new administrator account is created.
- The worker imports the small job store without importing API authorization,
  models or SQLAlchemy. Completed ZIP parts are validated and retained across
  retries. Only complete verified parts advance durable progress.
- Partial files and expired exports are retained during acceptance. They consume
  disk; the unchanged disk guard pauses work before capacity becomes unsafe.
- ZIP responses and job ownership/zone authorization retain their existing API
  contract. CSV visualization and ingestion are not changed by coordination.

## Mac catalog verification

The operator helper creates exactly one role named
`greenmind_wav_metadata_<release>`, with two connections, eight-second statements,
150 ms lock timeout, read-only default transactions, no role membership or
administrative attributes, and SELECT on only the named WAV/feature/projection
columns. No users, emails or password hashes are exported. The new role's private
credential file is the only SQL credential allowed onto the verification Mac.

`hold-export.py` takes the archive lease, checks the unchanged 640 MiB/load/receiver
guards, creates a consistent SQLite journal backup and permits a maximum
two-minute SQL export. Its root-owned heartbeat must remain current. The Mac
uses a loopback SSH database tunnel and `app.raw_archive.isolated_catalog`.
Default ten-row cursors and gzip parts bound memory. The Mac's separately leased
source stages use at most 1,000 rows and gzip level one to reduce network round
trips and local compression time, without changing production copier defaults.
Journal backup and local decoding no longer consume a SQL lease. Completed
source stages are reused only after hashes, counts and the source-window checks
pass. Each database has its own read-only
REPEATABLE READ snapshot; cross-database consistency must be reconciled rather
than claimed atomic. Only the complete export creates `manifest.json`.
Resource pauses withdraw the ready marker before releasing the lease. Reports
distinguish the unchanged 640 MiB reserve from the 2.4 load limit.

`hold-export.py --operation backup` creates only the consistent journal copy and
immediately releases its lease. `--operation sql` opens a fresh guarded two-minute
window without repeating that backup. Transfer both the journal and its
`backup-proof.json` privately, then set their Mac permissions to 600. On the Mac,
`isolated_catalog --kind ledger --ledger-proof BACKUP_PROOF` needs no server
connection. It verifies the source checksum and retains the original source
backup window; local processing does not refresh the inventory cutoff.
Earlier journal checkpoints without source provenance are rejected. The combined
export also requires `--ledger-proof`; it cannot relabel an old backup as fresh.
`--kind gateway` and `--kind direct` each require a
fresh root-owned session and the narrow SQL credentials. `catalog_stages`
assembles the three completed stages and fully restores them locally before
writing the final manifest. Its conservative cutoff is the oldest source start;
it explicitly reports that the different database snapshots are not atomic.

The Mac performs full catalog restore and reconciliation into a new SQLite
database. Catalog downloads use the provider's read-only subaccount. Upload
credentials and MinIO operator credentials remain on the server. Publication
uses the existing bounded operator process after hashes and counts agree.

## Release and rollback

Package code by commit and file SHA-256. The installer preserves the live proxy
hash and receiver start times/restart counts. It adds only two archive service
overrides, applied on the next scheduled run. It never interrupts a running job
or receiver. Installation failure moves its overrides into private evidence and
reloads systemd definitions. `acceptance/rollback.py --manifest INSTALLATION`
does the same for a completed installation. It never rolls back the journal.

`upgrade-jobs.py --previous PREVIOUS_INSTALLATION_JSON` verifies each existing
override's SHA-256, retains its original file privately, installs a new immutable
package, and updates only the next archive job execution. Rollback verifies the
new hashes and restores the retained previous overrides. No running service is
restarted by either job installer.

`prepare-readonly-broker.py` creates a private whitelist of broker configuration
using only the existing `u676312-sub1` service key. Its prepared override clears
the old environment-file list completely, so upload credentials are absent.
Preparation alone does not register an override or restart the broker.

`enable-versioning.py` defaults to inspection. Its explicitly requested `--enable`
operation can only enable versioning on the two exact production buckets, after
both metadata states are readable and lifecycle is absent. It uses only the
regular human-configured operator alias. It pins the deployed image ID and the
unchanged contract/test hashes from the successful real 03.10.2026 fixtures.
Reuse of that proof is distinct from a fresh container-test execution. Original
WAV heads are compared before/after; no original is rewritten. A partial failure
never suspends versioning or removes a version. Legacy-null deletion remains off.

## Required acceptance before any pilot

1. Run synthetic legacy-null/version/concurrent-write tests against the exact
   production MinIO image. Enable versioning only after successful proof and
   review; confirm real state through the metadata identity. Do not rewrite WAVs.
2. Complete catalog publication, independent readback, empty SQLite restoration
   and fixed eligible-inventory reconciliation. Retain historical quarantine.
3. Accept the new read-only broker and candidate reader: real zone/job isolation,
   old URLs, local/archive-only reads, ZIP/SHA/WAV, CSV/display and actual download
   progress. Never delete originals to force fallback.
4. Rehearse and publish only the current hash-guarded reader overlay. Preserve
   all admin/static fallbacks, Gateway 8120, Direct 8003 and Legacy 8000.
5. Observe the accepted live release for a fresh 24 hours. Earlier observations
   and local tests are not live-release proof.
6. Finalize a catalog/inventory whose cutoff follows observation start. Publish
   the complete restore proof and bounded pilot index, then create the final
   provider snapshot. Restore the entire catalog and all proposed pilot WAVs
   independently from that snapshot. Refresh bucket/provider/readiness proofs.
7. Obtain separately explicit approval naming at most ten files / five MiB.
   Approval, snapshot and restoration freshness guards remain unchanged. A pilot
   does not authorize unlimited automatic deletion.

Insufficient 512/640/704 MiB reserves pause the relevant step. No ingestion,
database or Staging shutdown and no reduced safety threshold is a workaround.
Operator-created final snapshots remain required while only a read-only provider
API token is configured. Do not report deletion readiness before every real gate
passes.

## Operator evidence on 07.10.2026

The regular MinIO operator alias is human-configured. The diagnostic identity
`greenmind-diagnostic-20261007-48d6f68755ae94ac` can inspect only versioning and
lifecycle on the two production WAV buckets. Actual queries found neither
versioning nor lifecycle enabled initially. After the guarded inspection and
contract checks, versioning was enabled on both buckets at 17:12 UTC. Independent
queries through the diagnostic identity confirmed `Enabled` and absent lifecycle
at 17:13:41 UTC. Existing sample heads were unchanged apart from the expected
absent-to-null version header. No original was rewritten and legacy-null deletion
stays disabled. Copier credentials could still HEAD both exact legacy objects
with explicit null versions at 17:16:30 UTC; this is not an all-object GET proof.

The narrow SQL role `greenmind_wav_metadata_b4ae52d674aa` was created and its real
column grants/read-only/non-administrative properties were verified. It exports
no users, emails, password hashes or device credentials. The dedicated
`u676312-sub1` key produced successful SHA-256/full-WAV readbacks for two
previously approved recordings. Those samples do not accept a complete catalog.

Protected receiver start times/restart counts and Production/Staging proxy
hashes stayed unchanged. Job definitions were updated without restarting any
receiver. The read-only broker replacement is prepared, not activated. The new
reader is not accepted/live. Its actual startup paused at 608.7 MiB available,
below the unchanged 704 MiB startup reserve. A consistent 295,194,624-byte journal
backup was created; the lease then paused on receiver headroom before SQL export
began. It is retained and does not constitute a complete catalog.

The existing private quarantine entry was already independently investigated on
03.10.2026: incomplete WAV and missing source checksum. It remains excluded,
with the original untouched. Full catalog publication/restore/reconciliation,
the final provider snapshot, accepted reader/proxy and fresh 24-hour proof
remain mandatory; no activation-only readiness is claimed.

## Safe continuation

1. Provide sufficient sustained host headroom, or an approved isolated read host.
   The prepared SQL role and loopback tunnel support isolated verification.
   Do not stop receivers/Staging or reduce the safety reserves to obtain capacity.
2. Complete the bounded catalog workflow above. Retain failed/partial evidence;
   only a completed manifest plus independent full restore accepts publication.
3. Accept and activate the prepared dedicated read-only broker and candidate.
   Run every required real download/authorization/CSV check before the minimal
   proxy rehearsal and switch. Current candidate manifests remain inactive.
4. Observe the accepted live release for 24 hours, then finish fresh fixed-cohort
   reconciliation, catalog/restore-proof/pilot-index publication and operator
   creation of the final snapshot. The configured provider API token is read-only.
5. Independently restore the complete catalog and all proposed pilot WAVs from
   that snapshot. Refresh metadata/provider evidence and obtain separately
   explicit human approval for the named ten-file/five-MiB maximum pilot.

There is intentionally no unrestricted deletion toggle in the ordinary copier.
An instruction to activate cannot substitute for missing or stale real evidence.

## Technical preparation is not consent

`preflight --verify-proof PRIVATE_PROPOSAL --proof-sha256 SHA256` accepts an
unsigned bounded technical proposal. It independently checks snapshot recovery,
release/observation/reconciliation evidence and the exact S3 version target.
Only complete coverage can report `READY_FOR_PILOT_APPROVAL`; the report always
has `approved=false` and `delete_enabled=false`. It never calls S3 deletion.

The separate deletion manifest must additionally contain an `authorization`
object with `approved=true`, `action=bounded_wav_pilot`, the approving operator
and `approved_at` within its validity window. A draft or technical report cannot
serve as that manifest. This object is prepared only after separate explicit
human approval of the exact named ten-file/five-MiB maximum pilot. The ordinary
copier remains unable to enable deletion through its existing schedule.

ZIP retries now apply resource guards while decoding saved parts and writing
verified spools into ZIPs. The completed ZIP's directory is synced before durable
job progress advances. A paused partial file never advances verified progress.

The prepared reader pins its dashboard authorization URL to the preserved
production receiver's unique container name, after confirming it is running on
a shared network. The generic `application` DNS alias is shared by older retained
releases; absence from Nginx alone does not prove those releases unused. This
preparation changes no live caller or receiver. Actual authorization and endpoint
compatibility remain mandatory before candidate publication or retirement.
