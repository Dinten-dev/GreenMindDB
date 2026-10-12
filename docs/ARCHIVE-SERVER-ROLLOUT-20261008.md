# Archive host rollout — 08 October 2026

Status: **PARTIAL / NOT READY FOR DELETION**. No source WAV, archived WAV,
snapshot, historical record or original evidence was deleted. Deletion,
retention and visualization pruning remain disabled.

## Completed and verified

| Item | Result |
| --- | --- |
| Archive host | `traver@2.31.22.33`, Ubuntu 26.04.1, 2 CPUs, 3814 MiB RAM |
| Initial disk | Approximately 34 GiB available; `/tmp` is memory-backed |
| Runtime | Docker and Compose installed; no full OS upgrade or reboot |
| SSH access | Existing Mac public key installed; key login verified |
| Budgets | Total 2560 MiB / 150% CPU; archive 1536 MiB; Staging 1024 MiB |
| Private transport | SSH tunnel established; database/S3 and receiver health reachable |
| Telemetry | Authenticated loopback source probe; 32 MiB limit; missing token returns 404 |
| Images | Ten exact source configurations and every filesystem layer verified |
| Image transport | 2,492,029,952 bytes; SHA-256 `d59e23938cd924f7469b317e688bc49c32197dd8eddb0ff4f938e3e790e579c6` |
| Staging data | Consistent cold copy, 234,096,640 bytes |
| Data SHA-256 | `d79de2ef1821b6793d17b41cde3dc5969648eb58874c48bfd2ed3d762f9a0cac` |
| Restored table counts | Three zones, two sensors, two Gateway WAV records; one Direct device, 42 revisions |
| ZIP correction | Download uses the worker's persisted filename; ownership and path rejection tested |
| Local suite | 236 passed, seven skipped, four integration deselected |
| Staging rollback | Independent fourteen-minute watchdog restored original proxy and restarted original Staging |

Docker's unnamed multi-image export contained ten legacy entries but only one
OCI index entry. A separately retained transport completed the OCI index without
changing any configuration blob or layer. New Docker manifest IDs differ from
the old configuration IDs; the importer checks the complete original config
digest, actual OCI manifest, operating system, architecture and every filesystem
layer. Docker 29 omits deprecated build-container inspection fields; executable
configuration and original config bytes are retained and checked separately.

## Actual deployment boundary

Production receivers were not stopped or restarted. After rollback, Gateway
8120, Direct 8003, Legacy 8000 and the existing reader 8140 returned HTTP 200.
Receiver starts and restart counts remained:

| Receiver | Started | Restarts |
| --- | --- | --- |
| Gateway | 2026-09-22 08:47:54 UTC | 0 |
| Direct | 2026-09-27 11:39:25 UTC | 2, pre-existing |
| Legacy | 2026-09-02 15:38:58 UTC | 0 |

The original Staging proxy hash is again
`6b0737287aa88ffaa15a26cf5351f78088a9df3198837fdc94c9dc2ae08e3b2c`.
Public Staging health and visualization health responded after rollback.
Its existing visualization worker reported `paused_for_host_load`, correctly
preserving its source guard.

The new Staging stack was started internally, but its API endpoints reset
connections and its frontend probes timed out. It was **not accepted** and is
not the public Staging target. The operators' SSH session was interrupted during
the final switch. The independent rollback recovered the original environment.

Read-only cgroup evidence on the archive host showed no OOM or OOM kill, a peak
of 819,695,616 bytes and 555,570 `memory.high` events. Approximately 2362 MiB
host memory remained available. The 768 MiB soft Staging threshold is a likely
startup bottleneck; this is not yet a complete diagnosis without container
state and sanitized logs. Do not weaken production reserves to hide this.

## Access audit

| Identity/access | Purpose and restriction |
| --- | --- |
| Existing SSH `traver` | Authorized host preparation and scoped migration |
| Mac public key | Added on archive host; private Mac key was not copied |
| `greenmind-archive-copy`, archive uid 999 | Separate non-admin, non-login archive identity |
| `greenmind-archive-read`, archive uid 995 | Separate non-admin, non-login read identity |
| `greenmind-archive-link`, source uid 995 | Source accepts only archive IP, listed forwards and loopback reverse listeners; no interactive shell |
| Dynamic source-probe identity | Bounded telemetry, no command/write endpoint |
| New dedicated SSH key | Archive-to-source link only; separate from upload access |
| New telemetry token | Source health only; private files/credential mounts |
| Regular Staging configuration | Privately transferred for the approved migration |
| MinIO administrator | No root/container credential extraction or password reset |

The new transport exposes no public database or S3 listeners. Staging and source
bridge listeners remain private. Upload credentials were not mounted into a
public reader. The WAV-only offsite catalog has not been published in this run;
the separate private Staging database migration is not that catalog export.

Archive-host sudo was explicitly authorized by the human. An automatic review
then rejected a password submission because it identified an existing session
as a production session. That session was not reused for secrets. A fresh key
SSH session independently identified `ubuntu-4gb-fsn1-1` before scoped sudo.
All sixteen new private Staging containers, including optional-profile workers,
were stopped. Original Staging remains public; production receivers unchanged.

## Remaining sequence

1. Diagnose new Staging startup with actual container status and sanitized logs;
   stop duplicate private writers while original Staging is active.
2. Correct soft resource throttling within the existing hard budgets, preserving
   original production reserves and the host's safety reserve.
3. Automate the complete consistent cutover under one bounded coordinator,
   rather than depending on a long-lived operators' terminal. A fresh attempt
   must preserve the earlier cold copy and proofs, reconcile changes since that
   copy and rehearse rollback before acceptance.
4. Transfer/archive job ownership only after a consistent journal snapshot,
   preserving source identity and exactly one scheduled writer. Existing main
   copy/export schedules still use their original code and were not replaced.
5. Finish complete WAV-only catalog publication, independent readback, empty
   archive database restoration, fixed inventory and quarantine reconciliation.
6. Accept the private reader and broker with actual zones/job ownership,
   archive-only old URLs, ZIP manifests/SHA/WAV decode, CSV/display and progress.
7. Rehearse and publish only the minimal accepted reader overlay, preserving all
   administration/static fallbacks and ingestion routes.
8. Start a fresh 24-hour interval for that accepted reader release. None started
   here; earlier observation timers do not replace it.
9. Refresh catalog/pilot evidence, obtain a final provider snapshot after
   publication and independently restore its complete catalog and pilot WAVs.
10. Require a fresh read-only preflight and separate explicit human approval for
    at most ten files/five MiB. Current execution grants no deletion approval.

The new host alone is not proof that deletion is ready. Existing null versions
remain unaccepted in production. Final snapshot, complete offsite restore,
accepted reader publication and fresh 24-hour acceptance are still required.

Private deployment inputs and immutable transfer evidence are kept outside Git.
No passwords, private keys, tokens or private Compose values appear here.

## Added scientific feature backup

The requested feature backup is installed on production as the separate
`greenmind-feature-backup.timer` / `.service`. It preserves source PostgreSQL
features and extraction workers. Runtime `/opt/greenmind/feature-backup/20261008-v2`;
initial package/config retained. No administrator accounts were created.
Access reused: existing `greenmind-raw-copy` non-admin identity, its configured
private SFTP key, and the existing column-limited `greenmind_wav_metadata_b4ae52d674aa`
role. No MinIO root/admin credentials were extracted or reset.

Coverage: Gateway features and feature versions; Direct segment/revision
manifests with per-channel features and scientific config. Visualization
aggregates remain part of the separate complete WAV-only catalog, still pending.
The feature-only format cannot satisfy full catalog/deletion-readiness checks.

Schedule: 00:00 Europe/Zurich, retries 01:00 / 02:00 / 03:00, at most four
attempts per day, skipped after a verified daily result. Worker 128 MiB, no swap,
10% CPU, idle I/O. Source reserve 640 MiB and maximum load 2.4 remain unchanged.
SQL export and transfer/restore have separate bounded two-minute phases. Shared
archive coordination avoids concurrent copy/export. No receiver was restarted.

Storage Box quota was read independently from the Mac using its existing SSH
key and pinned host keys, without production load: 1,099,508,482,048 total bytes,
988,477,652,992 available bytes (SFTP statvfs, 08 October). Live quota parsing
matches the implemented OpenSSH response; publication keeps at least 5%/2 GiB
plus twice the incoming bundle size.

Initial production attempt correctly failed closed on the historical numeric
`noise_50_nyquist_hz` band, which was absent from the WAV catalog allowlist.
Compatibility was corrected without modifying source values. The allowlist
fingerprint explicitly includes this historical band; future full catalog
acceptance must use the new fingerprint rather than reuse older proofs.
The correction was installed with exact prior service/config SHA guards.
The following attempts paused for `archive_job_busy` and then `memory`.
No complete feature backup has been verified yet. Timer active, copied feature
coverage not claimed. No successful offsite feature restore is claimed yet.

A proposed 1,217-byte existing feature sample transfer to the Mac was rejected
by automatic approval because the approved Storage Box destination did not
cover this additional egress. A focused human request is pending. It was not
retried via another route. The private server-side sample and original part
remain preserved.

Receiver continuity: original start times/restart counts unchanged; Gateway
8120, Direct 8003, Legacy 8000 and old reader 8140 all HTTP 200. Ordinary copy
timer and the separate feature timer active. Verified complete feature backups: 0.
Rollback only this added feature schedule by disabling
`greenmind-feature-backup.timer`; preserve all local/offsite evidence.

Final local validation: 248 passed, seven skipped, four integration deselected; Ruff and diff whitespace checks passed. Live feature completeness remains NOT VERIFIED.

Local source commit: `e8fb0b4`, branch `feature/archive-deletion-preparation-20261003`.
GitHub push was attempted, but origin pushurl is deliberately
`DISABLED_PENDING_REVIEW`. It was not changed or bypassed. The source commit
has not been published on GitHub; draft PR #4 remains at its previous head.

### Feature release v3 and bounded validation

Runtime `/opt/greenmind/feature-backup/20261008-v3` installed after exact v2
service/config hash checks. Original packages/configuration remain preserved.
The wrapper now retries only `archive_job_busy` at twenty-second intervals,
up to three times, catching the copier's existing yield window. Waiting opens
no database connection; memory pauses still exit immediately. Outer unit
350 seconds; active SQL and publication budgets remain two minutes each.
No source RAM reserve was weakened.

A separate one-off constrained validation unit was started. It paused for
`memory` on 2026-10-08 at 15:47 UTC before a complete backup. The daily timer
remains active with its next calendar event at 22:00 UTC / 00:00 Switzerland.
Storage Box quota query passed; complete feature publication/restoration still
NOT VERIFIED, verified complete feature backups still zero.

Post-v3 focused tests: 34 passed (including three bounded-contention scheduler
tests and Direct channel-feature recovery). Earlier full suite: 248 passed,
seven skipped, four integration deselected. No complete live acceptance is
substituted by those local tests. Production receiver health all HTTP 200.

## Follow-up implementation: source lease and private Staging correction

Current status remains **PARTIAL / NOT READY FOR DELETION**. This section
supersedes earlier resource figures and private-container status above.

### Implemented and tested

- Feature exports on an archive host require the actual source copier's shared
  lock. A forced SSH command grants only the fixed `catalog` operation, for at
  most 120 seconds. The client stops new work after 110 seconds. Disconnect,
  invalid input and partial-line input release the lock within its bounds.
- Publication can use the separate read-only Storage Box identity for recovery
  rather than reusing upload access. Retried readbacks preserve earlier failed
  evidence; reusable feature parts must match their manifest hashes first.
- Local archive suite: **254 passed, seven skipped, four integration deselected**.
  Ruff passed. Local results do not prove complete live catalog recovery.
- Immutable code package `20261008-v8`: 173 files, 246,783 bytes, SHA-256
  `f5d191ad90535caa738f868beaeed1718670134fbd9200e1ec581e9f2131a40c`.
  Every file was independently checked after extraction on both hosts.

### Live changes and access audit

| Change | Scope and evidence |
| --- | --- |
| Archive resource rebalance | Same parent hard limit, 2560 MiB / 150% CPU. Staging: 1280 MiB high, 1536 MiB max, 100% CPU. Archive: 768 MiB high, 1024 MiB max, 50% CPU. |
| Private Staging correction | Existing accepted administration image includes migration `0023`; private database already has `0023`. Corrected only private base backend image and set one Gunicorn worker. Healthy, zero restarts after correction. |
| Retained Staging evidence | Original Compose retained; corrected `compose-v6.json` SHA-256 `3d480cf7a415d466195e507699e82aeae5afb030a3a287937341990d2fa3a155`. Root-private budget and runtime proofs retained under `/var/lib/greenmind`. |
| Source identity | `greenmind-archive-lease`, uid 994, own group only; no administrator/sudo membership. |
| Source lease authorization | Dedicated archive key, source-IP restriction `2.31.22.33`, `restrict`, one forced command; no arbitrary shell or forwarding. |
| Source lease filesystem access | ACL permits only directory traversal and the four existing coordination files. Actual account cannot read the Storage Box upload key. |
| Source lease runtime | Forced pointer upgraded from immutable v4 to v8; prior authorization retained. No receiver configuration changed. |
| Actual lease probe | Authenticated archive-to-source probe returned `archive_job_busy`, correctly refusing overlap with the ordinary copier. This is transport/lock evidence, not a successful export. |
| Archive Python | Existing neutral runtime copied without production mounts; SQLalchemy, psycopg2 and httpx imports checked. No upload access installed. |

All sixteen private Staging containers were subsequently stopped; all reported
`oom=false`. Their data remain intact. Original public Staging remains active.
The private copy is now stale: another consistent cold copy and a bounded
cutover/rollback coordinator are required before public migration. Starting
the corrected private stack is not permission to publish it.

Production Gateway 8120, Direct 8003, Legacy 8000 and old reader 8140 returned
HTTP 200 after these changes. Receiver start timestamps and restart counts
exactly match the earlier table. No production receiver was restarted.
Original copy/export schedules and the source feature v3 timer remain in place;
the archive-host feature service has not been installed or started.

### Explicit blocking approval

A private 20,480-byte access package was prepared on Production, SHA-256
`7cc9c7354a0ef339cc622d3fef84d6366e5f089dc299431a116b6efc59a768ea`.
It contains the existing Storage Box upload key, separate read-only key, their
host pins, the limited metadata-reader database password, and source telemetry
token. It excludes MinIO administrator credentials and application account rows.

Automatic approval review rejected its proposed transfer from Production to
`2.31.22.33` because sensitive credential egress requires explicit approval
of this payload and destination. **The transfer did not execute.** A focused
human question is pending. No indirect route or credential extraction was used
to bypass that rejection. The archive lease key already remains private on
the archive host; it grants neither upload nor database access.

GitHub publication remains blocked by `DISABLED_PENDING_REVIEW`; it was not
changed or bypassed. These follow-up source changes are local and uncommitted.

### Required continuation after approval

1. Verify the exact transferred package hash and install private credentials
   only for the non-admin archive worker; use the new v8 code pointer. Public
   readers must never receive upload credentials.
2. Rehearse fresh consistent Staging migration and rollback under one bounded
   coordinator, then transfer exactly one scheduled archive writer. Preserve
   the existing source reserves and intake identities.
3. Complete feature backup and the separate full WAV-only catalog, independent
   readback, empty-database restore, fixed inventory and quarantine/null-version
   reconciliation. A successful feature bundle alone does not satisfy this.
4. Complete real reader/broker authorization, old URLs, archive-only fallback,
   ZIP/SHA/WAV, CSV/display and progress acceptance; rehearse/publish only the
   minimal hash-guarded reader overlay.
5. Observe that accepted release for a fresh 24 hours, publish current catalog
   and bounded pilot evidence, then obtain and independently restore a final
   provider snapshot. No such new interval or final snapshot exists here.
6. Obtain a fresh deletion-readiness result with deletion disabled. Require
   separate human authorization matching at most ten files/five MiB before
   any pilot. Ongoing deletion requires its own later decision.

Rollback for this follow-up preserves all evidence: suspend only newly added
archive schedules if later installed; restore the retained forced authorization
if needed. The private Staging stack is already stopped. Do not stop receivers,
delete spools, remove historical metadata, or alter existing source schedules
as an incidental rollback action.

## Continued preparation and read-only audit — 09 October

Status: **NOT READY FOR DELETION**. No deletion or retention was enabled.
Neither the blocked credential transfer nor the old reader candidate was
activated. Current public services and schedules remain in place.

### New code and actual tests

- `cutover.py`: bounded phase ordering, append-only fsynced evidence, watchdog
  requirement before source pause, target-stop confirmation before source
  restart, and preservation of a remotely accepted owner when acknowledgment
  is lost. Fault-injection tests cover timeout/response loss, missing target
  stop, wrong snapshots, incomplete rehearsal and expired budgets. **This is
  the orchestration engine, not a completed host-specific deployment adapter
  or live migration rehearsal.** Those integrations remain required.
- `archive_host_catalog.py`: separate bounded narrow-role Gateway/Direct stages
  under the actual source lease; catalog publication and full recovery through
  the separate read-only Storage Box identity into a new empty SQLite archive.
  Source reserve stays 640 MiB; archive reserve stays at least 800 MiB.
- `source_catalog_once.py`: optional bounded source preparation during actual
  headroom, existing non-admin copy identity only, consistent SQLite journal
  backup, allowlisted PostgreSQL export, separate publication/recovery phase,
  unchanged 640 MiB/2.4 guards. All earlier evidence remains intact.
- `catalog_stages.assemble` now accepts checkpoints during local assembly and
  full restore. This does not turn a partial catalog into a complete one.
- Full local archive suite: **277 passed, seven skipped, four integration
  deselected**. Ruff and whitespace checks passed. Exact deployed MinIO
  legacy-null integration and actual receiver/public-reader checks are not
  replaced by the synthetic tests.

Immutable package `20261008-v9` was verified on both hosts: 177 files,
253,930 bytes; SHA-256
`61ed28c856d26b184347a49d71748505f07efa9d03bf3399f4e634177f1b3352`.
Existing receiver packages, source feature v3 timer and archive job schedules
were not replaced. No new administrator identity was created in this follow-up.

### Live evidence and remaining gates

The one-off feature preparation dispatched at 20:01 UTC on 8 October returned
`paused / host_load`. Its retained `attempt-result.json` contains that result;
there is no `verified.json`. A successful systemd unit state accepting exit 75
does not mean a completed backup. The private attempt and configuration remain
on Production; no repeating validation schedule was added.

On 9 October the existing receivers and old reader again returned HTTP 200.
Receiver start timestamps/restart counts match the earlier table. The actual
source reserve fluctuated between allowed and disallowed headroom; the latest
bounded audit failed the 640 MiB export reserve. No reserve was lowered and no
service was stopped to manufacture capacity. The archive host has sufficient
headroom, but its required private upload/metadata access is still unavailable.

The existing diagnostic identity read both exact MinIO buckets as `Enabled`
with no lifecycle configuration. This is actual bucket metadata evidence;
legacy-null acceptance remains disabled and complete per-object verification
has not been performed.

The current Production proxy no longer matches the original prepared reader
manifest. That stale candidate must not be published. A fresh GET-only overlay
was prepared on 9 October against the exact current file, Nginx syntax-tested
using a separate include tree, and reversed byte-for-byte to the original.
Protected receiver identities and active proxy stayed unchanged. This is
**syntax/roundtrip preparation**, not actual proxy publication/rollback,
archive-only download acceptance or accepted new reader release. The new
overlay must be bound to that future fully accepted immutable reader release.

The source read-only audit also confirmed all inspected deletion, lifecycle,
retention and legacy-null activation flags remain false and the historical
quarantine is retained. Its private report is stored only on Production:
`/home/traver/greenmind-archive-rollout-20261008/readiness-20261009.json`.
Automatic approval review rejected downloading this report to the Mac because
this internal payload/destination lacked specific approval. That transfer was
not executed and was not reconstructed via another route.

The access-package transfer was independently confirmed absent on the archive
host. Automatic review again rejected it despite the general rollout approval;
an explicit payload/destination human question remains pending. No secret,
administrative role or public-reader credential was silently substituted.

Before claiming activation readiness, still complete: the actual coordinator
adapter and watchdog/sole-writer cutover; full catalog and fixed inventory;
independent complete recovery; real reader, zone/owner, original URL,
ZIP/SHA/WAV/CSV/progress tests; real guarded proxy rehearsal/publication; a fresh
24 hours following that accepted release; final provider snapshot after current
catalog/pilot publication and full restoration; fresh read-only preflight.
Only then can a separately authorized ten-file/five-MiB pilot be considered.
No fresh accepted-release observation timer has been started.

The separate one-off catalog attempt on 9 October at 07:18 UTC returned
`paused / archive_job_busy` before export. No catalog folder, complete manifest
or verified restoration was created. The existing scheduled writer was not
interrupted. Final health checks returned HTTP 200 for Gateway 8120, Direct
8003, Legacy 8000 and the existing reader 8140; receiver start timestamps and
restart counts were unchanged. Production and Staging proxy hashes still
matched the inspected current files, and all six inspected archive/deletion,
legacy-null, retention and prune flags remained false in both configurations.

### Explicit access approval applied, 9 October

The human explicitly approved both previously blocked transfers. The exact
20,480-byte `offload-access-v4.tar` reached archive host `2.31.22.33` with the
expected SHA-256 `7cc9c7354a0ef339cc622d3fef84d6366e5f089dc299431a116b6efc59a768ea`.
The source-only private report reached the Mac with SHA-256
`93524abcd8da0d5e2c36acfe98eba71de11cbeca3bbf25087f71e2680c39e779` and mode
0600. The temporary report-specific source read ACL was withdrawn after
delivery; its original private mode and bytes were preserved.

No administrator identity was created. `traver` used existing authorized sudo
on both hosts. Existing archive identities now have these isolated paths:

| Identity | Private access | Explicit exclusions |
| --- | --- | --- |
| `greenmind-archive-copy`, uid 999 | `/etc/greenmind/archive-offload-v10/access.env`, mode 0640 root/copy group; private upload, subaccount-read, source-lease and telemetry files | No administrative MinIO credentials, no SQL writes, no deletion activation |
| `greenmind-archive-read`, uid 995 | `/etc/greenmind/archive-reader-validation-v10/read.env`, mode 0640 root/read group; dedicated subaccount key/host pinning | Cannot read upload key or the copy/SQL configuration; no upload variables |

The copy identity received only traverse access to the private configuration
parent; an initial permission failure was corrected with that precise ACL,
without making sibling credentials public. The reader received its own
traverse ACL and separate private configuration directory. Actual OS tests
confirmed that reader access to the upload key and SQL configuration fails.
The source production credentials, public reader and receiving containers
were not replaced.

Actual SFTP authentication as `greenmind-archive-read` to `u676312-sub1`
succeeded with pinned host keys. The bounded unit executed only `pwd`, read
no WAV/catalog files, wrote nothing remotely, consumed 66 ms CPU and peaked
at 2.3 MiB RAM. This proves connectivity, not provider write denial, full
archive recovery, application authorization or snapshot acceptance.

One archive-host SQL-stage attempt paused `archive_job_busy`. A later bounded
attempt paused `source_load` before SQL/export. Source reserve remains 640 MiB,
archive reserve 800 MiB; the archive probe's conservative source load threshold
remains 2.0, and the source lease's established ceiling remains 2.4. Neither was
relaxed to obtain a passing result. The original schedules remain the sole
scheduled writers. No completed metadata stage or catalog recovery was claimed.

Full local archive tests: **285 passed, seven skipped, four integration
deselected**. New fault tests cover finite busy retries, immediate memory/load
pause, failed-child completion rejection, independent WAV checksum/decode,
corrupt-read evidence retention and rejection of upload access in the reader.
Local synthetic WAV tests do not substitute for actual authorized downloads.

A new transfer of `read-validation-20261009.json` (two source-derived
Gateway/Direct WAV receipts) was separately rejected by automatic review. This
is not the already approved access package or report: it contains sensor,
object, time and checksum metadata. A precise human question is pending; the
file was independently confirmed absent on the archive host. It was not sent
by another route, embedded in a command, or reconstructed there. Actual WAV
downloads using those receipts remain **NOT RUN**. No reader release, proxy
switch, fresh observation interval, final snapshot or deletion was activated.

Final receiver health checks again returned HTTP 200 for 8120/8003/8000/8140;
all protected start timestamps, restart counts and both active proxy hashes
remained unchanged. This does not claim a full application-data acceptance.

### Approved independent WAV readbacks completed, 9 October

The human separately approved the two WAV receipts and their archive-host
readback tests. The exact `read-validation-20261009.json` transfer then
succeeded. Source and archive-host SHA-256 agree:
`f9b4df4cc0ed82d724127a0c5f215128e15e8f6bdd6151ae1da35cdb1cfcff86`.
One initial automatic-review timeout was followed by its permitted single
retry; no alternative transfer route was used.

The already hash-verified v10 helper dispatched the bounded isolated unit
`greenmind-archive-read-validation-20261009t083411600076.service`. It downloaded
one Direct WAV (456,044 bytes) and one Gateway WAV (456,084 bytes) through
`u676312-sub1`. Both exact sizes and SHA-256 values matched their approved
source receipts; both WAVs passed full frame decoding. Source PostgreSQL and
MinIO were not queried by this download job. The read process had no upload
configuration and no permission to read the private upload key.

Private proof and retained WAVs:
`/var/lib/greenmind/archive-reader-validation-20261009/20261009T083411600076/`.
The unit completed with exit 0 under 128 MiB/no-swap/10% CPU/120-second limits.
The collected unit did not retain an actual memory-peak measurement; no RAM
peak is inferred from the earlier separate authentication-only test.

These are **two successful independent sample readbacks**, not a complete
catalog restore, live API/zone/job-owner acceptance, archive-only old-URL
acceptance, final snapshot recovery or deletion readiness. The proof keeps
those gates explicitly false. All earlier evidence and local originals remain
intact, existing schedules and proxy routes were unchanged, and no data or
snapshot was deleted. No new accepted release or 24-hour interval was started.

Post-test receiver health again returned HTTP 200 for 8120/8003/8000/8140;
protected receiver identities/start times/restart counts and the inspected
Production/Staging proxy hashes remained identical to the pre-test baseline.


## Human-approved continuation, 9 October, after 10:33 UTC

The human explicitly approved the described private Storage Box access package
and continuation. This applies to the existing 20,480-byte package, not a newly
invented 2,040-byte file. Its exact SHA-256 remains
`7cc9c7354a0ef339cc622d3fef84d6366e5f089dc299431a116b6efc59a768ea`.
The original transfer/install and independent WAV sample reads already passed.
No self-granted approval, new administrator or credential discovery is involved.
Deletion, legacy-null acceptance, retention and pruning remain disabled.

A fresh source audit returned HTTP 200 on 8120, 8003, 8000 and 8140. Protected
receiver identities/start times/restart counts and quarantine digest were
unchanged. Actual available source RAM ranged from 490.5 to about 647 MiB;
the source export reserve is still 640 MiB. The archive host had about 3207 MiB
available and no active duplicate Staging containers. Original source schedules
remain active; job ownership has not moved.

One actual bounded catalog attempt was dispatched:
`greenmind-catalog-metadata-validation-20261009t103646969960.service`.
It stopped at `source_lease_lost`, after 25.248 seconds, 7.845 CPU seconds and
54.7 MiB peak archive-worker memory. No complete Gateway or Direct stage was
produced. Lease loss alone did not identify whether the source paused for
memory, load or transport; the client previously obscured renewal pause codes.
Those known source pause codes are now preserved and allowlisted. Transport
loss remains distinct. Source health and thresholds are unchanged.

New isolated preparation:

- `source_ledger_once.py` uses the real source archive lease and SQLite online
  backup including committed WAL; it emits a fresh allowlisted journal stage.
  Original journal and every failed or earlier copy are retained. No SQL source
  is opened. Source dispatch requires 768 MiB before allocating its 128 MiB
  worker, preserving the existing 640 MiB reserve; 10% CPU, no swap, 140-second
  outer timeout, 110-second active phase budget; no repeating timer installed.
- `archive_catalog_finalize.py` accepts only all three complete, checksum-valid
  production stages, within 24 hours, plus the retained quarantine register.
  It assembles, independently reads every offsite part through sub1, restores a
  new empty SQLite database and reconciles the fixed inventory. Total bounds
  remain 256 MiB compressed and 512 MiB decoded. Local assembly, publication/
  recovery and reconciliation each retain a 110-second budget. The dispatcher
  uses 384 MiB, no swap, 50% CPU, the existing archive slice and a 380-second
  outer timeout. No existing scheduled job is replaced.
- Assembly streams independent private files instead of requiring hard links
  across systemd read-only/writable mounts. Source files are never overwritten
  or removed. Partial or corrupt restores never produce final `verified.json`.
- Metadata dispatch now binds Python to its own hash-verified immutable package;
  the old v9 backend and existing source forced lease/code remain unchanged.
  Public readers receive no upload or metadata database credentials.

These helpers are preparation, not proof of complete live catalog recovery.
Missing/stale stages block finalization before publication. Synthetic roundtrip
and fault tests prove code behavior, not actual production cohort completeness.
Full local suite: 295 passed, seven skipped, four integration deselected. A later
focused check includes the new non-string pause-code case. Four initially blocked
Unix-socket tests passed when rerun outside the socket-restricted sandbox.

Required order is still: fresh source checkpoints; full independent offsite
restore/reconciliation; bounded real sole-owner cutover; new private reader and
broker authorization/old URL/archive-only/ZIP/SHA/WAV/CSV/progress acceptance;
minimal accepted proxy publication/rollback; fresh 24-hour accepted-release
observation; current catalog and pilot publication; final provider snapshot;
full catalog and all pilot restoration from that snapshot; fresh read-only
readiness. Only a later separate, exact ten-file/five-MiB human pilot approval
can authorize removal. No prior timer or sample read satisfies these gates.


### Immutable installations, actual follow-up and private source-read access

Code-only v11 was installed on both hosts at
`/opt/greenmind/archive-preparation/20261009-v11`. All 182 payload hashes were
verified independently on each host; package SHA-256
`515ccf35c222b0e900ca855decc672d419cbd6e8bf3b3ad64028aabd8c88828a`,
259,453 bytes. Existing runtimes, source forced lease v8, schedules and receiver
code pointers were retained. No reader or proxy was switched.

The actual source-journal dispatcher paused BEFORE starting a worker:
`source_startup_headroom`, 590.1 MiB available versus 768 MiB required
(640 MiB original reserve plus a 128 MiB worker budget). No source journal
stage was created. The subsequent v11 metadata attempt
`greenmind-catalog-metadata-validation-20261009t105127379746.service`
waited once for `archive_job_busy`, then paused at `source_load`. It did not
retry the resource pause or open the Direct stage. Elapsed 24.708 seconds,
1.491 CPU seconds, 40.5 MiB peak. This is an incomplete export, not a passed
catalog or deletion gate. The allowlisted pause diagnostic worked as intended.

The provider's actual read-only query at 10:54:02 UTC returned only preliminary
snapshot 1414399 (`2026-10-07T12-45-05`, Greenmind-Sicherung-20261007).
No final snapshot was created or accepted. Its proof is retained privately.

The previously approved 20,480-byte package did not contain MinIO source-object
read credentials. A regular human-configured operator alias was used ONLY for
bounded existing-user/policy queries; no container/root secret was extracted.
The existing source object account is distinct from the operator administrator.
Its exact policy allows GetObject/GetObjectVersion for the two production WAV
buckets and explicitly denies object writes/deletion, version deletion, bucket
deletion and lifecycle changes. It has NO ListBucketVersions permission.

A separate support package was verified on the main host at
`/opt/greenmind/archive-validation/20261009-v12`; three payload hashes, SHA-256
`21fa647d8ec4c9f269ffa5bae0f9bbb834463dc589a3e3c9c9bf51fdb921d6bc`.
It prepared, but did not apply, a metadata/object/version-list READ-ONLY
preflight policy. No IAM identity or policy was created or modified.

The concrete source-read access tar was then prepared and encrypted-relayed
between the approved hosts, without writing credentials to this Mac:
10,240 bytes, SHA-256
`2be52b4d7cefe1b68961a577672c1b34b8163a9347dbcdd25bad18f232e50f58`.
It contains only six existing source S3 endpoint/access variables and a policy
proof. The operator/admin identity is excluded; no source writes or deletes
are granted. Both exact transfer attempts passed automatic approval. The first
failed with ordinary source-file UNIX permissions, not automatic review.
Temporary file-specific ACLs allowed the retry; after success they were removed
from both source directory and tar, restoring root modes 0700/0600. Originals
and private packages remain intact.

The destination hash/size and policy facts were checked before a separate
installation in `/etc/greenmind/archive-source-read-v12`. Directory root/group
copy 0750; env root/group copy 0640; policy proof root 0600. Actual
`greenmind-archive-copy` uid 999 can read the env and
`greenmind-archive-read` uid 995 cannot. Incoming tar is private 0600. The first
installer used an incorrect SOURCE-only username and stopped before directory
creation; it was corrected against actual archive passwd identities. No users
were created. The immutable preparation proof retains `transferred=false`
(the preparation-time fact); this receipt records the subsequent verified
transfer, rather than overwriting the original.

These source endpoints remain loopback placeholders from the source host.
The file is inert: no existing service env was replaced, no object reader or
copier was launched, and no public reader received upload/SQL/object secrets.
A reviewed private broker/tunnel and consistent journal/sole-writer cutover are
still required before using it on the archive host. Do not replace the running
copy account policy with deletion rights. Version-list preflight access and
later separately authorized bounded deletion access remain distinct gates.

Final source checks returned Gateway 8120, Direct 8003, Legacy 8000 and old
reader 8140 HTTP 200. Receiver start timestamps/restart counts and both proxy
hashes matched baseline. Source RAM was 631.9 MiB available, load 2.008;
no reserve was reduced. All six destructive/activation flags remained false
in both inspected source preparation configurations; archive flags were also
checked false. Actual archive copy/read key permission isolation passed.
Quarantine remained the same one record and digest. Journal 172,278 verified
entries is a ledger count, NOT a fresh independent full remote-cohort proof.
No new copy-total or complete backlog claim is made.

Validation: full suite 295 passed, seven skipped, four integration deselected;
subsequent focused continuation checks 24 passed, source-access policy tests
six passed. Ruff and final diff whitespace checks passed. These tests do not
replace live reader, complete catalog, provider restore or legacy-null tests.

Deletion readiness remains FALSE. Remaining execution gates:
1. Actual consistent Staging/coordinator cutover and private transport, without
   duplicate writers; obtain sufficient sustained source headroom first.
2. Complete fresh ledger/Gateway/Direct stages, full WAV-only catalog offsite
   publication, independent complete restore, fixed cohort and quarantine
   reconciliation; separately prove legacy-null handling without re-uploading.
3. Reviewed read-version-list access; actual new reader/broker zone and job-owner
   isolation, original URLs/archive-only fallback, ZIP/SHA/WAV, CSV/display and
   download progress acceptance.
4. Accepted minimal hash-guarded proxy rehearsal/publication, actual rollback
   and receiver continuity; then a NEW 24-hour accepted-release observation.
5. Fresh complete catalog and bounded pilot evidence publication; final provider
   snapshot AFTER publication and full catalog/all-pilot restoration from it.
6. Fresh read-only readiness, followed by separate explicit exact-list approval
   for at most ten files/five MiB. No removal is authorized by this continuation.

No data, originals, prior evidence, snapshots or historical records were deleted.
No receiver was restarted, source timer stopped, job owner transferred, proxy
published, GitHub push performed, retention or legacy-null acceptance enabled.


## 2026-10-10 continuation: restricted diagnostic identity and bounded exports

Status: NOT deletion-ready. No source/RAW WAV, historical row, snapshot or earlier
proof was deleted. No retention, pruning, lifecycle, legacy-null acceptance or
pilot deletion was activated. No accepted reader release or fresh 24-hour
accepted-release observation was started. GitHub was not pushed.

### Access and exact human approval

Only the regular operator-configured MinIO alias was used:
`/home/traver/.config/greenmind/minio-operator-a3b88514f95551f1`, alias
`greenmind-operator`. No root/container credentials were extracted, unknown
passwords reset, public upload access installed or administrator created.

One new expiring diagnostic identity `2X6BN0ICCP1N6NF7KFER` was actually created.
Expires `2026-10-17T06:28:15Z`. Friendly-name and MinIO structured-denial parsing
bugs were fixed; the existing identity was resumed, not duplicated. Exact attached
policy and expiration were read back. Permissions are GetObject/GetObjectVersion,
ListBucketVersions/GetBucketVersioning/GetLifecycleConfiguration for ONLY
`greenmind-raw` and `greenmind-direct-production-hotspot`. Explicit deny covers
object/version deletion, object upload, bucket deletion, versioning and lifecycle
writes. Global listing and administrative access are denied.

Automatic review initially rejected the concrete transfer of this new key.
Temporary source ACLs were removed. The human then explicitly approved:
“Ja, diesen beschränkten Schlüssel zum Archivserver übertragen”. Only that exact
restricted key was subsequently encrypted-relayed from 188.245.247.156 to
2.31.22.33. No credential bytes were written to this Mac or printed in logs.
Source ACLs were removed again after success.

Installed archive directory `/etc/greenmind/archive-diagnostic-read-20261010`:
root/copy-group 0750, `read.env` root/copy-group 0640, proof root 0600.
Actual copy uid 999 can read; public reader uid 995 cannot. Private incoming
600 file is retained. Environment SHA-256:
`844778da6eb243b84ddace6d50f7cd5c5deec9eb465ef7354a8693df5f61bd93`.
No operator alias, SFTP upload identity, new schedule or public-reader env was
transferred or replaced.

### Actual version-read acceptance

`/var/lib/greenmind/archive/version-read-validation-20261010/20261010T072746928931/verified.json`
was created by the bounded private copy-account worker (192 MiB hard cap,
no swap, CPU 25%, 90 seconds maximum; no SQL/upload credentials).
Actual runtime ~7 seconds, CPU 1.279 seconds, peak RAM 45 MiB.

Both production buckets reported versioning Enabled and
NoSuchLifecycleConfiguration. Actual exact-prefix version listing, version-specific
HEAD and a bounded 44-byte RIFF/WAVE header download succeeded for the two
previously approved sample recordings. Version IDs match their existing receipts:
Gateway `65ad5a5e-0ab8-48bd-89dc-e8e59c391047`; Direct
`dc1d4df9-e120-47a9-902d-5bbaec97311f`. Global ListBuckets returned AccessDenied.
This proves these reads, not whole-catalog recovery, reader authorization,
full ZIP/SHA/WAV/CSV agreement or legacy-null acceptance. The prior independent
sub1 full-WAV/SHA/decode proof remains a separate two-sample result.

Both sample receipts first verified at `2026-10-09T07:52:05.517227Z`.
Under the unchanged max(ended, received, first_verified)+7 days rule, these two
cannot become age-eligible before `2026-10-16T07:52:05.517227Z`. This is not a
claim about all other candidate files. A 24-hour observation does not replace
that age guard or exact-list pilot authorization.

### Actual private Staging diagnosis

Five existing private clone containers (PostgreSQL, MinIO and three APIs) were
briefly started with a separately armed 240-second stop safeguard. Six private
scheduled worker clones remained stopped. Actual ports 8124/8125/8126 returned
200, containers were running, with no new OOM/restart. Resident clone slice was
~619 MiB; combined GreenMind slice ~654 MiB; archive host had ~2784 MiB available.
Old peak values were not relabeled as fresh test peaks. The image worker setting
already supports one worker; no alleged hard-coded-two-worker bug was invented.
All five were explicitly stopped afterwards, and all 16 clones were verified
stopped. Evidence:
`/var/lib/greenmind/evidence/private-staging-startup-20261010.json`.

The restored clone is the retained October-8 cold copy. It is NOT a fresh
consistent migration or accepted sole-writer cutover. The unsafe old rollback
path was not executed: source may resume only after independent target-stop
confirmation. A real host-specific coordinator/fencing adapter is still pending.

### Source journal fix and resource boundaries

New `source-journal-checkpoint.py` uses the real shared archive lease, source
account uid 996, a 32 MiB hard cap/no swap, 10% CPU, idle I/O and 120-second outer
budget. Production reserve remains 640 MiB and startup needs 672 MiB; original
source load ceiling remains 2.4. Existing source schedules and code pointers
were not replaced. The independent archive conversion uses 256 MiB/no swap,
50% CPU, existing archive slice, 800 MiB reserve and a 110-second active budget.

Actual first journal attempts paused for memory or an occupied lease. One
attempt produced an OperationalError before meaningful output. A real read-only
SQLite diagnostic confirmed WAL mode; a subsequent check found the WAL/SHM
present, so the earlier exact errno was not reconstructed as a proven fact.
The hardened profile now permits WAL shared-memory bookkeeping in the source
parent, while binding the original main database and existing WAL read-only.
Connection remains mode=ro/query_only; immutable=1 is never used because it can
miss committed WAL rows. The original database is never compacted or overwritten.

Actual v18 worker copied a 307,085,312-byte checkpoint before pausing under
source_load, peak 32 MiB, zero swap, CPU 4.223 seconds. No journal-proof.json was
written. This artifact is unaccepted and retained; a full-sized file alone is
not a successful checkpoint or restoration proof.

Full-page integrity work has now been moved to the isolated archive host.
Source online backup completion, count and SHA identify the cut; its proof
explicitly says source_integrity_checked=false/archive_integrity_required=true.
Archive conversion must perform integrity_check before exporting a stage; even
matching hashes cannot accept corrupt SQLite pages. Progress-handler interruptions
retain the real SafetyPause instead of opaque SQLite errors. A source proof is
not a complete WAV-only catalog and never grants deletion readiness.

v19 source dispatcher attempts also paused BEFORE starting at 647.9, 671.7 and
654.0 MiB. These pauses were not overridden. Archive metadata attempt
20261010T075550717059 paused after two occupied-lease retries, then source_load;
53.032 seconds wall / 4.016 seconds CPU / 50.6 MiB RAM, no completed Gateway/Direct
stages. Other earlier attempts remain incomplete as recorded privately.

v20 one-off archive metadata dispatch now uses the ORIGINAL source ceiling 2.4
rather than its extra-conservative 2.0 setting. The source lease's independent
2.4 maximum, RAM/health/disk reserves, SQL timeouts and archive load/RAM limits
remain unchanged. No repeating schedule was changed. Actual completion remains
subject to real guards; a dispatch is not a pass.

Immutable package evidence (all 189 payload hashes verified):
- Source and archive v19: SHA-256
  `ae794fec81b5ac3eaa3003a5740cec8836903a43bbe42fdf350fd0587be3fd82`.
- Archive v20: SHA-256
  `6acf5a70862fee1e9ec84d9f191394a9d87360a73d52dc64b3c5161c256690d0`.
Earlier immutable packages and failed private attempts remain intact.

### Provider state and remaining acceptance

A bounded read-only Hetzner API query at 07:45 UTC returned only snapshot 1414399,
created `2026-10-07T12:45:06Z`, description Greenmind-Sicherung-20261007.
Local evidence `../archive-server-rollout-20261010/provider-readonly.json`.
No snapshot was created/restored by this query. It remains preliminary; final
snapshot acceptance requires complete fresh catalog/pilot publication first.

Local full archive test run: 330 passed, seven skipped, four integration
checks deselected; four Unix-socket setup errors arose from the local sandbox.
The actual eight broker tests were then run with permitted local Unix sockets:
eight passed. Four overlap with the 330 passing tests, giving 334 unique passing
tests. Subsequent touched-dispatcher checks passed. Ruff passed for changed files.
These are local tests, not live reader/production authorization or recovery proof.

Still required before any bounded pilot approval:
1. Fresh consistent host-specific sole-owner migration with independent rollback
   fencing, sufficient sustained source headroom, and exactly one scheduled copier.
2. Complete accepted ledger/Gateway/Direct stages, full WAV-only manifest/offsite
   publication/independent readback/empty archive DB restoration; fixed inventory,
   quarantine and real legacy-null reconciliation without re-uploading originals.
3. Real private broker/candidate rights, zones/job-owner isolation, archive-only
   original URLs, ZIP manifests/full SHA/full WAV decode, CSV/display and progress.
4. Only the accepted minimal hash-guarded reader overlay: actual rollback and
   receiver continuity, then a new accepted-release 24-hour observation.
5. Fresh catalog/pilot proofs, final provider snapshot AFTER publication, independent
   complete catalog/all-pilot restoration, fresh fail-closed readiness.
6. Separate exact-list/snapshot/hash-bound human pilot approval <=10 files/5 MiB.

No reader release, old public URL acceptance, whole-cohort remote reconciliation,
final snapshot or deletion readiness is claimed by this continuation.


### Last bounded dispatcher correction

The v20 metadata attempt stopped with ArchiveBlocked before creating a Gateway
stage. Static diagnosis found conflicting configuration: the worker had been
clamped to the original source-lease ceiling 2.4, while CombinedProbe constructor
still rejected configured ceilings greater than 2.0. No SQL output was created.
The new private v21 probe permits configuration up to 2.4 and rejects 2.401;
its default remains 2.0 and other existing schedules were not changed.
Existing source lease/feature backup hard ceiling 2.4, RAM/disk/receiver guards,
local archive guards and all hard worker budgets remain in force.

Actual v21 installed on archive only, all 189 hashes verified, package SHA-256
`14b93eef1622de15299ec40cd844f2ff2ab8226d6089a0d329331b9dd8d48e38`.
Fifteen remote-health tests passed, including retained 2.0 behavior, fresh
source identity, memory/load/health/disk failure and 2.4/2.401 boundary checks.
Twenty-eight combined health/catalog/dispatcher checks passed before the final
minor test regrouping. Ruff passed. Two new health tests bring the unique local
passing-test total to 336; these do not establish live catalog completion.


### Accepted source checkpoint (still not a complete catalog)

Source unit `greenmind-journal-checkpoint-20261010t080553454458` completed.
Cut: 2026-10-10T08:05:55.790878Z to 08:06:09.994077Z; 175,019 journal rows,
307,113,984 bytes; SHA-256
`dc4d9705b5eb6e79a9b891fa94c502b728924c3c29ef9559aae5f033cc9c76f3`.
Actual CPU 2.139 seconds. Full integrity intentionally remains an independent
archive-host prerequisite; source proof explicitly records that requirement.
Originals and earlier failed checkpoints remain unchanged.

Private relay of this WAV-only copy journal was approved by automatic review,
limited to 4 MiB/s, with no local database spool on this Mac. A file-specific
read ACL for the two artifacts and traverse-only ACL for their two parents was
armed with an independent five-minute restoration timer before access was added.
The preexisting exact ACLs were retained privately for restoration. No upload
or MinIO operator credentials accompany the journal.

Source continuity check at 08:05:38 UTC: actual receiver start/restart records
and production/staging proxy hashes match baseline; Gateway8120, Direct8003,
Legacy8000 and reader8140 HTTP200. Existing four source timers remain active;
all six destructive/activation flags remain false. RAM734.4MiB/load1.776.
Source evidence `/home/traver/greenmind-archive-rollout-20261008/source-final-20261010.json`.

Archive check 08:06:47UTC: all16 private clones stopped, six flags false, source
link active, copy uid999 can read diagnostic env/public reader uid995 cannot.
RAM3202.1MiB/load0.0205. Evidence
`/var/lib/greenmind/evidence/archive-final-20261010.json`.
The v21 metadata attempt paused on actual source_load without complete stages.
It was not relabeled as catalog success and was not retried in process.


### Journal relay receipt

Both checkpoint and its source proof were relayed successfully. Archive hash and
size match source proof exactly (307,113,984 bytes / dc4d9705...9c76f3). Incoming
private files were retained; an additional private uid999/mode600 copy was made
under `catalog-preparation-20261009/checkpoint-20261010T080553454458`.
The exact source ACLs were independently compared against the retained getfacl
backup and restored byte-for-byte. Only the temporary ACL-restoration timer was
then stopped; original source schedules were unaffected. No file was deleted.

Independent local-only archive conversion dispatched as
`greenmind-journal-conversion-20261010t081205326686`, no source connection and no
credentials loaded, under the existing 256MiB/no-swap/50%-CPU archive budget.
Actual complete stage is not claimed until its successful output is read back.


### Independent archive journal stage PASS

`greenmind-journal-conversion-20261010t081205326686` actually completed:
175,019 rows, local SQLite integrity_check passed before complete export; source
SHA/provenance/count agreement passed, stage carries archive_integrity_checked=true.
Runtime107.971seconds / CPU54.005seconds / RAM77.1MiB peak, within unchanged
110-second active/120-second unit budgets and 256MiB hard cap. No source connection
or credentials were loaded for this phase. This proves the complete journal
stage, NOT complete Gateway/Direct metadata, full offsite catalog restoration,
whole-cohort remote RAW reconciliation or deletion readiness.

Performance boundary: this real ~175k-row stage used nearly its full active
budget at the existing 50% archive CPU cap. Larger future checkpoints may require
reviewed resumable local processing rather than silently extending budgets or
retrying partial output as complete. Source checkpoint and compressed stage
remain private and retained. No automatic cleanup is installed.


Final journal stage readback reports12 compressed parts,46,795,878bytes and
175,019rows, archive_integrity_checked=true. This is a full local checkpoint
stage, not a remote catalog publication or Storage Box full restore.
At08:16:25UTC the actual four source health routes still returned200 and receiver
start/restart records matched baseline. SourceRAM674.5MiB/load2.4097; original
load guard2.4 remains binding. Archive capacity does not substitute for source
telemetry, nor permit starting a second active writable deployment.


Latest metadata attempt `20261010T081537832807` exhausted its four bounded
occupied-lease attempts (69.860 seconds wall,3.163CPU,40.5MiB peak), and remained
incomplete without opening accepted metadata stages. Original schedules were
not stopped to force a slot. A later bounded lslocks/source-service snapshot
found no jobs.lock owner at that instant and the export service activating;
this does NOT disprove earlier occupied-lock responses or prove a sole-owner
cutover. No stale lock or duplicate writer claim is made without evidence.

Machine-readable truthful gate status: `../archive-server-rollout-20261010/status.json`.
Overall deletion readiness remains FALSE. Successful local journal processing
is retained for subsequent fresh source-metadata stages and full offsite restore.
Source receivers, original scheduling, existing reader8140 and proxy routes
remain unchanged. No fresh24h acceptance or final provider snapshot has started.


### Actual continuation on 2026-10-10 (13:53 UTC)

The explicitly approved Staging control access is installed. Source retains
its new root-only mode600 private key; Archive holds one root-owned public-key
authorization, bound to Source188.245.247.156, `restrict`, and the exact
`control.py --fence` command. The locked `greenmind-staging-fence` account may
only use that fixed passwordless sudo command. Both sudoers files parse cleanly.
The ordinary operator `traver` used existing SSH/sudo access; no new general
SSH administrator, upload access in a reader, or MinIO root-secret extraction
was introduced. Control script SHA960a765c...ddc32c matches on both hosts.

Actual Staging rollback rehearsal cohort4dfd9c61d1db758eae40d9ac passed; accepted
cohort4c5a4f375344c2e8979ac483 preserves the cold-copy SHA26876a99...01d4.
Source18 Staging containers are stopped, Archive16 healthy containers serve
Staging. Source protected receivers and Production proxy remain unchanged.

Private v23 reader checks passed real cookie authentication, wrong-zone404,
original Gateway/Direct dashboard downloads, 1h/7d/30d/1y series and exact CSV
agreement. Both actual approved WAV export jobs passed ZIP manifest/SHA/WAV,
owner isolation and progress. The controlled private S3-NoSuchKey rehearsal
read both original dashboard URLs through the read-only Storage Box broker,
verified full SHA/WAV bytes and restored the private configuration. No original
object was removed. Device-Bearer compatibility is not claimed accepted.

The private broker reached its fixed30-minute limit. The unpublished private
API was stopped to release approximately94MiB; public reader8140 is untouched.
The experimental compressed-only SQL tunnel was stopped; the original Source
link and migrated Staging remain active. Both original Source copy schedules
are retained; Archive copy schedules remain inactive. No writer offload is
claimed complete.

Direct metadata has a complete367904-row source snapshot and independently
validated local13,536,044-byte/8-part stage. The175019-row journal checkpoint
has an integrity-verified12-part stage. Gateway captures remained partial at
source-load or statement guards; none were combined or relabeled complete.
The local-source alternative waits without SQL, then uses the same128MiB/10%
CPU/640MiB reserve/2.4 load/eight-second statement/110-second active limits.
Only the soft inter-page yield was shortened from200ms to50ms.

New per-table offline validation and per-part independent catalog recovery
are tested. Publication requires every exact part before the manifest; complete
recovery requires every independent read-only part and exact SQLite integrity
and counts. Upload credentials cannot certify independent recovery. Archive
packagev30 SHA64dd02ee...79b77 has197 verified files. Latest targeted suite115
PASS; additional existing catalog/lease/authorization suite64 PASS. This is
code/test preparation, not full offsite catalog publication or restoration.

At13:48UTC all8120/8003/8000/8140 health routes returned200; Production proxy
SHAf4e7b516...e95436 remains exact. At13:45UTC receiver start/restart records
matched baseline. Bounded recent logs showed111 Gateway ingest201 responses
and276 Direct upload201 responses among400 requests, plus successful heartbeats.
Existing continuity protection responses503 are not represented as successful
control delivery. Source3 CPUs/3819MiB RAM had load4.19 and991MiB available;
Archive had approximately1986MiB available and load0.23. Source pressure is a
real guard, not an archive-memory shortage.

No reader overlay was published; no accepted-release24-hour interval started.
No complete catalog, fixed-cohort, legacy-null, final snapshot or deletion
readiness claim is made. All originals, archived files and proofs remain.

Final bounded wait result at13:56:07UTC: soft_start_headroom, no SQL connection
and no capture output. Unit ended13:56:08; its success-status treatment means
safe pause, NOT catalog success. Fresh Source audit13:55:49 reports1130.7MiB
available/load3.104; Archive audit13:55:54 reports1985.9MiB/load0.167. Source
8120/8003/8000/8140 all200, receiver start/restart values unchanged and original
copy/drain timers active. The additional64 existing catalog/lease/authorization
tests passed. Accepted-release24-hour start remains FALSE. No new background
copy schedule, reader publication, pruning or deletion was enabled.


### Actual continuation on 2026-10-10 (17:08 UTC)

Gateway capture now uses native PostgreSQL COPY over bounded 256-block heap
ranges in one read-only REPEATABLE READ transaction. Logical UUID identities
are unchanged. The allowlisted table read obtains ACCESS SHARE before the
relation size, preventing rewrites without excluding ordinary ingestion.
Partitions/inheritance and nonstandard block sizes fail closed. Statement8s,
active source110s, source640MiB/load2.4 and archive800MiB/load2 are unchanged.

The existing narrow metadata role greenmind_wav_metadata_b4ae52d674aa received
SELECT(ctid) only on its eleven exact already-allowed Gateway tables. No other
content columns, writes, membership or administrative privileges were added.
The existing PostgreSQL operator performed this named-column grant at
16:54:56.183077UTC after waiting for source640MiB/load2.0. Two preceding attempts
paused before SQL. Role flags remain false for superuser/create-role/create-db/
replication/bypassrls, connection limit2. Private source proof:
/home/traver/greenmind-archive-rollout-20261008/physical-scan-permission-20261010.json.
Grant SQL SHA1e13f85b10da08f6f7d7ffc7258514cb48db74cc273c70a94c731b7bd9828472.
Actual readonly EXPLAIN reports Tid Range Scan for the bounded feature-version
query. No receiver-container execution or root-secret extraction occurred.

Native experiment20261010T165719 paused at source_load after20.560s,
4.231CPU seconds and42MiB peak. Its partial bytes remain intact; it is NOT a
complete source snapshot. Previous deferred-text capture reached118.1MiB peak
with bounded file-cache eviction, but its110s deadline also prevented completion.

Archive packagev35 SHAbc385a5c481c8396b4100a5984dc662e58c0948dea93cf38f8d326a44666561e
contains198 individually verified files. Its bounded wait unit
greenmind-physical-wait-20261010t170450 waits without SQL for three source-load
samples≤1.6 before one unchanged-budget capture. SQL-port15433 uses a temporary
AES SSH transport with64MiB/10%CPU, original keys/forwarding rights and30-minute
expiry. Original link/health forwarding remains intact. No new administrator
identity or broader forwarding access was introduced.

The five-minute copy-only window independently restored both original source
copy/drain timers at16:57:25. Latest actual status confirms both active and
restore success. This was NOT ownership transfer. No Archive copy schedule is
installed or enabled. Receivers8120/8003/8000 and old reader8140 all returned200
at17:02UTC. Source pressure remains a real guard.

Latest targeted tests:95PASS covering capture, offline validation, partwise
publication, independent restoration and source capture. Public reader/proxy
remain unchanged; no accepted-release24-hour interval, final snapshot, retention
activation or deletion is claimed.

### Actual continuation on 2026-10-10 (18:08 UTC)

- Real Direct Bearer compatibility passed privately: existing legitimate429C
  identity returned46 own segments200; foreignABE8 run404; invalid token401;
  plaintext400. Token stayed on Mac; zero additional WAV downloads. Private
  reader8141 and broker were stopped afterward; public proxy unchanged.
- Exact production MinIO executable SHA256 matches the local isolated test
  executable:7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f.
  Two isolated NULL-version/racing-new-version tests passed against this binary.
  Production image digest unavailable from registry; image-config equality is
  not claimed. No original objects were overwritten or deleted.
- Actual readonly HEAD/version-target preflight passed for the two approved
  original WAV receipts at17:38:28UTC. Both are NONNULL versions; this does not
  accept the entire historicalNULL cohort or enable deletion.
- Gateway capturev37 paused at source_load after64.559s and42.9MiB. All partial
  evidence retained; no complete Gateway catalog claimed. Actual source heap
  metadata totals899.9MB. Currentv39 uses1024-block/8MiB physical ranges and
  50ms cooperative yields, reducing accumulated wait; statement8s/active110s,
  source640MiB/load2.4 and archive800MiB/load2 remain unchanged.
- 132 focused tests passed for capture/partwise restore and source health.
- Source private telemetry/forced lease health runtime health-v9 accepted at
  18:05:44UTC. Current root-pinned config remains[8120]; only this explicit
  candidate8152 may later be added. Logical8120 becomes healthy only if both
  receivers are healthy. Existing restricted key/IP/rights unchanged; no new
  administrator identity. Original source receivers/proxy unchanged. The first
  2-second start poll rolled back safely; bounded30-second retry passed.
- Source copier and drain timers remain active. Archive copy schedules absent.
  Reader publication, complete catalog recovery and fresh24h acceptance remain
  unclaimed; deletion and retention are disabled.

### Actual continuation on 2026-10-10 (19:02 UTC)

- Private Source receiver v6 passed120seconds at18:29:21UTC, image
  9f20141514dce0c625d906971b1215195ef07f184785f7e3340949674457eec0,
  192MiB/no swap. Original receivers remained running.
- Initial proxy attempts restored the exact original on source-load pressure
  and ordinary continuing HTTP connections. Existing streams were never forced
  closed. The reviewed v2 tracked connections accepted before/around the gate;
  new ordinary sensor POST connections did not block natural draining.
- Actual minimal receiver switch passed18:43:37UTC. Current proxy SHA256 is
  b5dbe4d009cce995c31bb7571413667dfe43130f051ec903d817b5c67d434d6c.
  Source telemetry and forced lease guard both8120/8152. Original copier ENV
  additionally checks8152. Root Staging control pins the accepted new hash.
  Original ENV/config/scripts and both failed attempts remain private evidence.
  The independent gate recovery and candidate expiry did not stop a public
  receiver. No administrator/key identity was added.
- Both original copy/drain timers independently restored18:47:45UTC. No writer
  ownership transfer yet. Gateway8120/8152, Direct8003, Legacy8000 and reader8140
  returned200. Actual existing upload logs independently showed404 successful
  ingest201 in3minutes, median23.5ms/P9582.7ms, noHTTP5xx. These are real requests,
  not injected measurements; they do not establish whole-system24h acceptance.
- Complete consistent Gateway capture passed18:46:22UTC: all11tables,
  174562wav_file,174561wav_feature,175621feature versions,277433visual_reading,
  174561visual_wav,338117visual_wave.77.934seconds including soft waiting;
  202.2MiB peak. SQL was closed and the temporary AES tunnel stopped afterward.
- Offline wav_file validation completed. The complete wav_feature phase paused
  at the unchanged110-second deadline,293.9MiB peak. Its partial outputs remain.
  New offline chunks preserve the exact capture hash, row and byte coverage;
  each phase≤20000rows/32MiB,110seconds,384MiB/no swap,50%CPU and archive800MiB
  reserve/load2. They receive no SQL/upload credentials. The completed snapshot
  is never combined with earlier partial source captures.18focused tests pass.
- Optional archive-host authentication is prepared locally, disabled: original
  Source auth remains authoritative over the existing private tunnel, only its
  validated UUID crosses the private broker. No production JWT signing secret
  is exported. Current SQL user/zone checks remain required.31tests pass.
- Bounded copy-only archive-host entrypoint is prepared locally, disabled until
  root-pinned ownership and fresh journal transfer:≤75seconds under the existing
  120-second Source lease, no new SSH capability.20copy/lease tests pass.
- Full offsite catalog publication/restoration, fixed reconciliation, sole-writer
  offload, final reader release and fresh24h observation are not yet accepted.
  No deletion, retention, legacy-null exception or cleanup has been enabled.


### Actual continuation on 2026-10-10 (21:45 UTC)

- Complete WAV-only catalog published: 162 compressed parts, 711350645 bytes,
  2019270265 decoded bytes. Published reference SHA256
  `85984109a77aa0dd802aac32bcbb0522786d1c2a9a27f45ba2a55d22be3ca587`.
  Gateway, Direct and ledger snapshots have separate cutoffs, not one atomic
  cross-database snapshot. Publication is not an independent recovery proof.
- Actual catalog readback exposed a path-validation defect: the dedicated catalog
  reader incorrectly called the WAV-only path validator. v50 separates the
  canonical hash-addressed catalog path without broadening the public WAV reader.
  27 focused tests passed. The independent subaccount readback/empty database
  restore is now progressing part by part under the original 384 MiB ceiling,
  800 MiB archive reserve and 640 MiB Source reserve. No completion yet claimed.
- Source copier was fenced and its entire 176508-row journal consistently copied.
  Archive copy v48 actually verified 54 WAVs / 24046776 bytes, with zero failures,
  then paused at its unchanged 75-second budget. Peak memory 93.2 MiB; exit75 is
  an accepted resource pause. Both Source and Archive ownership proofs now pin
  cohort `eed98245752657722c4aa01f`. Only Archive has an active copy timer (every
  two minutes and Swiss midnight). Source fence survives restart. No deletion.
- Source export worker/timer fenced; consistent private checkpoint has 3 jobs,
  10 files and manifest SHA256
  `d60745dde3dcae3f368707cb2e5f17462720c7e28d665c4408dc83e5f4a7feda`.
  Transfer of this private checkpoint through Mac to Archive was rejected by
  automatic approval review. Explicit payload-and-destination permission remains
  pending. No checkpoint bytes were downloaded; Archive export worker stays off.
  Existing ready ZIPs remain on Source. A new, separately tested restore helper
  preserves every job column and spool hash; four corruption/isolation tests pass.
- Private v48 cookie/zone/admin authorization, Gateway 1h/7d/30d/1y series and
  CSV agreement passed. Two exact previously approved WAVs passed actual HTTPS
  missing-original fallback, full SHA256/size/WAV decoding and zone denial.
  The temporary private fixture was restored, stopped and retained as evidence.
  These checks do not replace job ownership/progress/ZIP acceptance for v48.
- Frontend v3 passed actual private120s acceptance with at least875704320 bytes
  Source available, maximum load1.14. Existing8140 actually returned296/3596
  one-second Direct points (5m/1h), with exact CSV mean/min/max agreement.
  Root-controlled public activate/rollback/activate passed21:41:48UTC. Both
  public bundle hashes/markers and11 old+11 new assets verified. Four actual
  receivers retained images, start times and restart counts; six original
  protected containers remained unchanged. Final proxy SHA256
  `5848a31418a1964ddd071de810755cd12661601f2a24b466f3c99de030b3a7ea`, paired
  Staging-control SHA256
  `057ba45dc995a0e487339da6edd9f2278a45fb0a584802beebb756172d7e3eb8`.
  Reader stays8140; no archive-reader publication or fresh24h acceptance.
- Root evidence: Source `/opt/greenmind/peter-frontend-v3-20261010/public-switch-control/accepted.json`,
  Source `/opt/greenmind/copy-handoff/source-owner-accepted-v48/`, Archive
  `/opt/greenmind/archive-validation/copy-owner-accepted-v48/` and
  `/opt/greenmind/archive-validation/catalog-parts-20261010-v5-read/`.
  No administrator identity or credential was added in this continuation.
  Original configs, failed attempts, partial downloads and prior proofs retained.


### Actual continuation on 2026-10-10 (22:00 UTC)

- New v48 bearer compatibility actually passed21:46:32UTC: own46 segments,
  foreign404, invalid401, plaintext400. Existing credential remained on Mac;
  no token transferred, no additional WAV downloaded.
- Actual mount-namespace negative tests found a private broker could read the
  export ENV newly created after its startup: an optional inaccessible path
  had been skipped. Root refresh makes that mask mandatory and restarted only
  the unpublished broker. API and broker now actually deny all four copy,
  upload, export ENV and export lease-key paths. Original upload access was
  already denied; public8140 and all receivers unchanged. Also corrected the
  prepared inactive export worker's misspelled coordination mount path.
  Root proof `/opt/greenmind/archive-validation/credential-mask-refresh-v48/accepted.json`.
- Exactly two new private export jobs selected only the separately approved
  Gateway and Direct WAVs (912128 total source bytes). Source kept existing
  signing credentials; Archive received only the normal validated user context.
  Manual128MiB/no swap export slices completed both jobs; shared lease busy
  pauses release resources and retry finitely. No export schedule was enabled.
  Actual HTTP download checks passed21:58:46UTC: complete one-file ZIP manifests,
  SHA256/size/full WAV decode, real owner downloads and foreign404, queued to
  ready progress with original512MiB and stronger800MiB guards. The first SQL
  attempt failed closed; a bounded diagnosed retry passed without privilege or
  connection-limit changes. Source original three jobs remain untransferred.
- Minimal reader overlay prepared against accepted frontend SHA5848a314…,
  syntax passed and byte-exact draft rollback passed. Candidate SHA256
  `59898f51da6f0adc96b5d3d0bec1fd7e321244171275496103594a272d8b04e8`,
  paired control candidate SHA256
  `7029ce93f350d8097a2d82981d2074e7b5c44d11037a52adacf392af0af000bc`.
  No publication, no actual reader proxy rehearsal and no fresh24h yet.
  Production receiver/admin/frontend/static blocks retained. Root preparation:
  `/opt/greenmind/archive-validation/reader-proxy-v48-frontend-v3/`.
- 28 focused checkpoint/readback/reconciliation/export/compatibility tests pass.
  No original RAW, archive, metadata, snapshot or earlier proof was deleted.


### 2026-10-10 22:34 UTC: complete readback; final integrity not accepted

- All 162 offsite parts were independently read through the dedicated readonly
  subaccount, SHA-256/size verified and restored into the new private SQLite
  database. Exact counts for all 18 Gateway/Direct tables and 176,508 ledger
  receipts were independently checked. No complete recovery proof exists yet.
- The original final `PRAGMA integrity_check` hit the unchanged 120-second
  phase limit. Bounded follow-up diagnostics identified file-cache pressure:
  about 23 MiB anonymous memory versus 379 MiB cached file pages in the initial
  worker. No OOM occurred; no limit or source reserve was reduced.
- Advice only on the isolated readonly recovery file reduced observed worker
  peak from 384 MiB to 183.7 MiB. It does not change source files or globally
  drop cache. The diagnostic still paused at its 100-second deadline.
- A quick structural check is not accepted as full SQLite integrity. A local,
  unpublished verifier combines it with exact, resumable row/key bijections
  for every primary index and exact manifest coverage. Its focused tests pass;
  it has not been admitted against the full production catalog or published.
  SQLite documents the checks omitted by quick_check at
  https://www.sqlite.org/pragma.html#pragma_quick_check .
- Export-checkpoint restore/merge retains both original stores, all job owners,
  ready parts and progress; conflicts and combined size/job limits fail closed.
  Seven focused checkpoint tests and 24 catalog tests passed locally.
- Transfer of the three existing private export jobs (ten files, approximately
  3.3 MiB) through the Mac remains explicitly awaiting human authorization
  after automatic review rejected the exact payload/destination. No job bytes
  have been relayed. Reader publication remains blocked on lossless job handoff.
- At 22:19 UTC actual Source health ports 8120, 8152, 8003, 8000, 8140 and 28141
  returned 200. Production proxy remained SHA-256
  `5848a31418a1964ddd071de810755cd12661601f2a24b466f3c99de030b3a7ea`.
  The accepted reader's fresh 24-hour interval has not started. No original WAV,
  SQL row, archived part, snapshot or retained proof was deleted.


### 2026-10-10 23:03 UTC: full native catalog recovery accepted

- The independent 162-part recovery now passes the complete native SQLite
  `PRAGMA integrity_check`, exact 18-table counts, original manifest identity,
  every readonly-subaccount part proof and 176,508 journal receipts. The
  accepted proof is `complete-parts-20261010T1945-v50/verified.json` under
  `/var/lib/greenmind/archive-reader-validation-20261009/`, created at
  2026-10-10T23:01:47.014373Z, SHA256
  `3c8bda75dc77312f22721653146085713d573fd23faadd0a3ee1e2a8ac38df07`.
- Many small file-page reads, rather than HTTP-check overhead, explained the
  slow original integrity walk. A readonly diagnostic with SQLite memory
  mapping and 64MiB internal cache completed the full check in 60.861 seconds.
  This is virtual mapping, not an increased resident-memory allowance.
- Immutable private catalog runtime v51 applies that I/O tuning to the
  original full integrity predicate. It also installs a native SQL progress
  callback and independently polled health monitor: checks every two seconds,
  maximum five-second age, latched failure, original 110-second deadline.
  The regular full finish then succeeded in 67.543 wall seconds, 18.577 CPU
  seconds, with peak384MiB and unchanged 384MiB/no-swap/50%-CPU/120-second
  outer budget. Source640MiB and archive800MiB reserves remain unchanged.
  One preceding attempt correctly paused on source_receiver_health.
- No quick_check substitution, per-table sampling, experimental verifier,
  source SQL modification or acceptance-predicate relaxation was used.
  Five focused safety/corruption tests and all22 catalog finish/resume/transfer
  tests passed. Only three private catalog runtime files differ from v50;
  public reader8140, candidate v48, copier and reception services unchanged.
- The existing fixed-cohort coordinator detected this real verified proof
  and began bounded reconciliation. It must still finish before claiming
  a reconciled inventory; quarantine and legacy-null rejection remain intact.
- At23:00:22UTC Source8120/8152/8003/8000/8140/28141 all returned200.
  No job-checkpoint bytes have been transferred while explicit permission
  remains pending. No new reader proxy was published and no fresh24h test
  started. No original file, row, snapshot or earlier proof was deleted.


### 2026-10-10 23:07 UTC: fixed eligible cohort accepted; publication held

- Every reconciliation phase actually completed. The outer coordinator then
  encountered a status-parser KeyError because the complete finish result
  correctly contains schema/complete rather than a progress status. Corrected
  the parser to use optional status; no source/recovery records were changed.
  A separate bounded readonly finalizer independently checked all three phase
  completions, native checkpoint integrity, exact original database signature,
  proof/manifest hashes, final counters and the single required quarantine.
  Root accepted evidence: `/opt/greenmind/archive-validation/reconciliation-v50/complete.json`.
- Accepted reconciliation SHA256
  `21bf7c51ec31a2fd67a68bae66aebc4a7f912f368fe9f811f2161149b0c2afd1`.
  Fixed cutoff is2026-10-10T11:33:05.864804Z; this is the earlier source
  snapshot window, not a fresh post-publication inventory. Eligible pending
  files, receipt mismatches and unknown eligible files are all zero.
- Gateway:174562 catalog WAVs,173416 matched,1145 recent/protected pending
  (522312700 bytes), one excluded historical invalid record. Direct:2963
  published/sealed WAVs, all2963 matched, zero pending. Matching receipts do
  not constitute a fresh physical read of every archived RAW WAV.
- Actual legacy-null receipts:166586 Gateway and1852 Direct. Legacy-null
  deletion remains disabled. Non-null receipts:6830 Gateway and1111 Direct.
  Seven-day-reserve-elapsed matched counts:154345 Gateway and1235 Direct;
  these counts do not assert final deletion eligibility or pilot approval.
- At23:06:58UTC every Source health port8120/8152/8003/8000/8140/28141
  returned200. All four protected receiver images, start times and restart
  counters equal the earlier baseline. Production proxy remains5848a314…
  and Staging control057ba45d…. Archive copy timer is active; export timer
  remains inactive. All private catalog/finalizer units finished.
- Existing three Source download jobs/ten files remain intact and untransferred.
  Exact-payload transfer permission remains pending after automatic rejection;
  no retry or relay bypass was attempted. Public reader8140 remains in place.
  New reader publication, lossless job ownership transfer and fresh24h
  observation have not been claimed or started. No original, backup, archive,
  snapshot, SQL row, existing job or earlier evidence was deleted.


## 2026-10-11 authorized job handoff and accepted public reader

- Human approval explicitly covered the three Source download jobs through the
  private Mac verification folder to Archive2.31.22.33; all originals retained.
  Ten files /3304048 bytes matched manifestSHA
  `d60745dde3dcae3f368707cb2e5f17462720c7e28d665c4408dc83e5f4a7feda`.
  Checkpoint directory listing was not permitted by its existing execute-only
  ACL, so exactly the manifest-named files were read; no ACL was widened.
- Archive preserved its original two jobs and merged all three Source jobs,
  all columns, owners, creation/expiry times, completed ZIPs and private spools.
  Five-row digest `82211fc3bcb71054fb35f7288a6a32d4065d518c022e96e4e536830439d1b184`.
  Source remained unchanged at three rows, digest
  `1bf903d9d1f3658b732f6782075b3fc1a6d6419b0bc2f7faae4e3d8ca066b26a`.
  One already-expired job correctly remains404; no expiry was extended.
- Source export owner accepted09:29:45UTC; Archive sole export writer accepted
 09:31:23UTC. `greenmind-archive-export-20261010.timer` and
  `greenmind-archive-copy.timer` are active only on Archive. Source writer is
  fenced; original schedules/stores/proofs remain. No upload credential is
  exposed to the public API/broker; eight actual namespace-denial checks passed.
- Reader public acceptance actually completed09:44:56UTC (11:44:56 Swiss) under
  Root controller `/opt/greenmind/archive-validation/reader-proxy-v48-frontend-v3/acceptance-v54/`.
  Actual activate/rollback/activate protected all four receivers, old reader
  and v3 frontend. ProxySHA `59898f51da6f0adc96b5d3d0bec1fd7e321244171275496103594a272d8b04e8`;
  paired Staging controlSHA `7029ce93f350d8097a2d82981d2074e7b5c44d11037a52adacf392af0af000bc`.
  Public cookie/real-zone/owner isolation,1h/7d/30d/1y, CSV/display, original
  Gateway/Direct URLs, migrated ZIP manifests/SHA/full WAV decode all passed.
  Direct5m/1h at one-second resolution retains mean/min/max and CSV agreement.
  Failed pre-publication health/selector attempts and their actual successful
  rollbacks were retained. Indexed exact approved recordings replaced
  unnecessary unbounded test selection; SourceSQL8-second limit was preserved.
- Two new public export jobs selected only the two previously approved Golden
  WAVs (912128 original bytes), and the actual sole scheduled Archive writer
  completed both. Public owner progress, foreign404, anonymous401, ZIP manifest,
  SHA/size and full WAV decode passed09:51:30UTC. First ready job55.6MiB peak;
  worker hard128MiB/no swap and Source640MiB/Archive800MiB remain unchanged.
- v4r2 image imported exactly once after its import guard passed. The later
  independent928MiB start check correctly paused before creating a container.
  The guarded retry reuses the imported image, waits with extra scheduling
  margin, and retains the exact928MiB start,640MiB runtime,256MiB container,
  128MiB Node heap and32MiB private controller budgets. No existing container
  was stopped to obtain RAM. Public v4 and fresh24h are not claimed yet.
- Existing regular SSH `traver`, explicitly authorized sudo and existing
  column-limited read/auth configuration were used. No new administrative
  identity or password was created, no root/container secret was extracted,
  and no source credential was newly copied to Archive. Prepared observation
  receives only existing read-only SQL URLs and health/resource settings.
- Nothing was deleted, pruned or activated for retention/deletion. Nine local
  observation/checkpoint tests passed. A fresh24h interval must begin only
  after the accepted final reader/frontend pair; earlier observations are
  not substituted. Snapshot/pilot/deletion authorization remain separate.


## Actual paired acceptance observation — 11 October 2026

The publicly accepted reader is `archive-reader-20261010-v48`. The exact archive frontend image is `sha256:3bebe7cd5662357a10e25c4f8eb8000303aeafc93fbda6741f93b0ae6282717c`, container `gm-peter-frontend-v4-archive-r2-20261011`. Archive-private acceptance is `/opt/greenmind/archive-validation/frontend-v57/private-frontend.json` (SHA-256 `59cc92a10eff9750663f0b401647c8dd53fdbe4c84a7345aef39aa174476c617`). Minimum measured Archive headroom was 1655 MiB. The container is 256 MiB, no swap, 0.5 CPU, in the unchanged shared 1 GiB / 50% CPU archive slice, with no public port or source credentials. A bounded loopback socket proxy links Archive 3143 to the dedicated internal frontend address.

The explicitly approved Source SSH change reused the existing `greenmind-archive-link` public key. Only `permitlisten="127.0.0.1:28143"` was added, retaining the source-address restriction to 2.31.22.33 and no command rights. No administrator identity or new key was created, and SSH was not restarted. Source authorized_keys SHA-256 is `9123476e7c7764e7ec7c0df1e805187de990214474562f4b671c1af6812a6636`. Private approval/execution evidence is `/opt/greenmind/archive-validation/frontend-relay-scope-v56/accepted.json`. The new archive frontend-only relay reuses the existing private Source link key through a service credential; it does not change or restart `greenmind-source-link.service`. Existing public reader upload credentials remain unavailable to the frontend.

Source frontend switch v57 encountered a bounded health timeout and actually rolled back. Evidence and all failed candidates were retained. The subsequent v58 performed activate → rollback → activate with all eight protected Source service fingerprints unchanged and old asset fallbacks preserved. Its accepted proof is `/opt/greenmind/archive-validation/frontend-proxy-v58/accepted.json`, SHA-256 `b1cbfdf81edf3195fb7d29ce90d6de4a90992f765943575610b9a06d7e258f24`, accepted at 2026-10-11 11:10:39.784724 UTC. The public Nginx file SHA-256 is `29f29402f77d7f8eadb93825c924ba3c03af9e089ef009d1903def6e1a93348a`; paired staging control SHA-256 is `56ed4e1656cea60727c5ddf699175a026bb004c41e3954ca44223ddcad021cb4`. Only three frontend destinations and the corresponding staging-control hash changed; reception routes, reader routes, administration and static fallbacks were preserved. Minimum measured Source headroom was 830.7 MiB. The finite switch held only the existing archive-job lease, never an ingestion lock. Independent public reader and exact two-bundle GET checks passed at 11:14:28 UTC.

Archive persistence was accepted at 11:14:44 UTC without restarting the frontend: only its restart policy became `unless-stopped`, and the new socket and relay were enabled. Root proof `/opt/greenmind/archive-validation/frontend-final-v59/accepted.json` has SHA-256 `10b9e11f2c05bda8c7706a73aa4d864c76b40ef8cbff917d0f47aaebb4ce5fc0`. Original Source v3, stopped native candidates, failed private candidates, images and transfer files remain intact.

A genuinely new finite read-only interval started on Source at **2026-10-11 11:39:35.899222 UTC**, and Archive at **11:41:00.408587 UTC**. Both timers are `greenmind-archive-observation-accepted-20261011-v59.timer`; each host has its own private Root start proof under `/opt/greenmind/archive-validation/accepted-observation-v59/started.json`. Local copies are in `implementation/validation/archive-server-rollout-20261010/`. Source first two samples passed; Archive first sample passed. This is STARTED, not a completed 24-hour PASS.

Source sampling uses the existing bounded sequential read-only latest-row queries and existing 640 MiB / load 2.4 guards, a 96 MiB / no-swap / 10% CPU / 60-second unit, and a four-minute schedule at the copier yield window. The Source shared archive lease has a 30-second wait and opens no SQL while waiting; it affects no receiver lock. The one-off starter was bounded to 64 MiB / no swap / 5% CPU / 240 seconds. Archive sampling uses 64 MiB / no swap / 10% CPU / 60 seconds within the unchanged shared slice, every five minutes, preserving its 800 MiB / load 2 guards. Both require at least 280 samples, no gap over 600 seconds, unchanged protected markers, healthy routes, no errors and no headroom pauses; Source additionally needs actual Gateway and Direct source progress. Both results must pass. Stopping only these new timers suspends observation; evidence must be retained.

One-off read-only follow-up is scheduled for **12 October 2026, 13:55 Swiss time**. It does not authorize mutation or deletion. Source observations: `/mnt/HC_Volume_106755700/greenmind-delete-preparation/observation-accepted-20261011-v59`; Archive observations: `/var/lib/greenmind/archive-accepted-observation-v59`.

Known operational limitation: the bounded actual 11:30:19–11:40:19 UTC upload window had 1268 successful Gateway ingests, 17 successful Gateway WAV uploads, and 2423 successful Direct uploads, alongside **seven Direct HTTP 503 responses**. Source receiver start/restart markers remained unchanged and six health routes returned 200. Sanitized logs show intermittent Direct database operational errors and PostgreSQL connection-slot pressure. Existing `max_connections=50`, role caps, source guards and receiver settings were not changed. Source retry logic alone is not proof that every failed chunk was eventually persisted. A healthy observation does not silently turn this into a lossless-delivery claim.

No WAV, catalog, original export job, snapshot, historical record or earlier proof was deleted. Deletion, retention and pruning remain disabled. Fresh final snapshot/restoration, reviewed legacy-null treatment and separate bounded human pilot authorization remain separate deletion gates; a started or successful 24-hour reader observation never bypasses them. Nine focused existing observation/export-checkpoint tests passed, and both new installers and embedded observation programs compiled.


### Additional read-only Direct-503 evidence, 11:48 UTC

A new bounded 32 MiB / no-swap / 5% CPU / 40-second host-only diagnostic read twenty minutes of timestamped Direct and PostgreSQL logs, capped at 2 MiB and five seconds per stream; neither cap was reached. It emitted only error categories and timestamps, never raw log lines, device identities, SQL values or credentials. Proof: `implementation/validation/archive-server-rollout-20261010/direct-503-correlation-v60.json`; Source original `/opt/greenmind/archive-validation/live-upload-v55/direct-503-correlation-20261011T114818542349.json`.

There were 19 Direct `OperationalError` events and 16 PostgreSQL `remaining connection slots` capacity errors; 16 Direct events correlate within two seconds. All seven earlier counted Direct POST-503 timestamps match a Direct database error in the same second; six also correlate with a capacity error. This supports connection pressure as the leading cause, but timing alone is not exact failed-request identity proof. The current exception handler intentionally does not log payloads or credentials and does not retain the failed chunk identity in this evidence. Thus exact retry receipt and full downstream persistence of each failed request cannot be demonstrated from these logs. This remains an OPEN acceptance gate, not a passed lossless-delivery condition. PostgreSQL limits, receiver settings and accepted release were not changed. Both fresh observations remain running; Archive's first scheduled follow-up also passed.


### Peter dashboard incident read-only investigation — 12:29–12:32 UTC

A bounded diagnostic used the existing private Source test signing configuration and column-limited read-only role to select Peter's real active, verified account. Tokens remained on Source and were never printed or exported. Initial diagnosis encountered an OperationalError; an attempted coordinated archive window also timed out before database access. The next bounded observation succeeded without any changed role, connection limit, service, proxy or receiver configuration.

Actual Peter public GET results: four zones; seven Direct devices; all seven default-24h series and recording-list routes returned HTTP 200. Peter_buero device suffixes: 94:B8 719 chart points/100 recording entries; 94:CC 707/100; 94:D4 720/100; DC:10 21/6 (data began at 11:48 UTC today); 4E:04 2/1, last seen 12:04:57 UTC and offline. Ruedi AB:E8 had 720/100. Test 42:9C had 0/0 and was last seen 22 September. All returned chart points included extrema. Recording lists are capped at 100; that count is not the total WAV inventory. Device-status GET took 6.97 seconds.

Separate explicit one-second 5m/1h queries also returned data and min/max. The public new reader and old 8140 reader agreed on 5m point counts, response byte sizes and first/last timestamps for the first four devices tested. This is not a SHA comparison of bodies or a complete browser acceptance. An arbitrarily selected old office Gateway sensor returned an empty 1h list; this does not imply all office sensors lack data.

These checks show current authorized data exists. They do not prove the precise cause of Peter's reported blank browser. The coordinated frontend task reproduced independent failure modes where a temporary device-status error clears mounted charts, or a recordings error/pending response prevents an otherwise valid chart from being displayed. Its proposed fix remains under that task's review; no frontend or reader rollback was performed here.

Private local evidence: `peter-live-v63.json` SHA-256 `47d3a47c1c1013bcdf956245195e3ce420e4c30fa8c846084ca2c651f50910bf`; `peter-live-v64.json` SHA-256 `8d8257bd61774138ccff148af55a567b31035f315f579f2c7347d0d8a17c5696`, under `implementation/validation/archive-server-rollout-20261010/`. Original proofs remain on Source. Tests were capped at 96 MiB, no swap, 10% CPU and 120/180 seconds. No deletion occurred; the accepted release and observation timers were not changed.


Further user identification was relayed by the coordinated frontend task: screenshot suffix is 94:B8, correcting the earlier typed 94:B9. Production API proof v64 directly maps 94:B8 to device `3fba4959-ab9c-47a0-ad11-add0fd443edf`, Peter_buero, with last_seen 12:31:54 UTC. A new bounds/server-clock comparison at 12:38:51 UTC was blocked before HTTP requests by an actual PostgreSQL `remaining connection slots` error. Thus it does not establish a new time-bound equivalence result. The screenshot's relayed old last_seen differs from the successful production API result; browser origin/data source and client context remain to be checked. No assumed STA/AP-MAC equivalence or reassignment occurred.

At the bounded read of Source observation, 16 samples through 12:37:51 UTC had zero recorded sample errors, headroom pauses, health failures or protected changes. The interval is unfinished. These periodic probes do not override the independently demonstrated intermittent database connection failures or prove lossless ingestion.


### WWW, exact 94:B8 and time bounds — actual 12:44 UTC check

Both domains share the same Nginx TLS server block, API destinations and frontend. Unauthenticated login HTML SHA-256 matched exactly (`cce61babac32638453a2e68e606c9205935ba0a3906a3564f160f2dcfa6a352e`); both expose reader release v48, and anonymous device lists return 401. WWW resolves to Source IPv4; apex also has the existing Source IPv6 address. Nothing was changed.

A single bounded diagnostic waited for the existing archive-job shared lease without opening SQL, then used Peter's existing authorization on `https://www.green-mind.ch`. At 12:44:22 UTC, exact device 94:B8 was online with last_seen 12:44:24 UTC. The indexed Source visualization query found a one-second point at 12:44:00. Explicit current-UTC 5m bounds returned 271 points, server-clock 5m returned 269; both ended at 12:44:00. Explicit 1h returned 3266 points; server-clock 1h returned 3295 through 12:44:32 as processing advanced during the check. These are sequential moving windows, not identical frozen snapshots; all returned points contain extrema. Recording list for 5m returned one entry, which is normal and does not establish stale data. This does not prove lossless sampling or explain the exact browser screenshot.

The screenshot's relayed old last_seen is still inconsistent with these current authenticated results. Browser session, actual network requests and client clock remain unverified. No reader rollback or service/configuration change was performed. Private proof `peter-www-94b8-v67.json` SHA-256 `689b7dce8d0bb6c4e63f3643bb374e625e56f532dff5469557642197b4b068b5` is retained locally and on Source. The shared lease was released when the finite read-only diagnostic finished; reception was never locked or stopped.
