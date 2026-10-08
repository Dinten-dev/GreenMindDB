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
