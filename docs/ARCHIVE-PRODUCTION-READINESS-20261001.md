# Production acceptance — 1 October 2026

**LIVE since 16:11:01 UTC (18:11 Swiss time). No original WAV removed. All deletion/retention/pruning flags remain false.**

## Immutable release and routing

Runtime source: `a66ae7c8b1e539fb5998ac3efc98c4945caf174d`.
GitHub: https://github.com/Dinten-dev/GreenMindDB/pull/3.
All five checks passed for that exact revision: backend, frontend, Direct concurrency/Legacy migrations, visualization archives/Direct history, GitGuardian.
Images were built away from Production for linux/amd64.
Backend image: `sha256:a8aecf440c8b165f9a5837c46affc586b95d2cbf3f31ef2191d5f36e6f5b7a22`.
Frontend image: `sha256:e6f8d7b5b58bdc1d21bec4f3ec9b3e4ac5323caa8520220181e6e12db4be3607`.
Local/remote bundle SHA-256: `7847e8f386175453a39c26ab95f32fc8cc9b5a91599d7b4eb2e84844ae56b69f`.
Runtime directory keeps its preparation name `/home/traver/greenmind-archive-production/33fe395`; its manifest records the actual a66ae7c revision.

Nginx before: `73111c089d59645575d6259d17aa35a97c094d2f7221680dac1021aa0406de0e`.
Nginx active: `e18cfd2a6b357b1310946e20250cb77bcf3402cbb653642b96910ed5a241250d`.
Only three visualization proxies (8121 → 8140), two frontend proxies (3133 → 3140) and an old-asset fallback were changed.
Admin 8133, Gateway continuity guards/8120, Direct ingest 8003/devices 8122, Legacy and private S3 TLS 9444 remain preserved. All older static fallbacks remain available. Staging configuration hash is unchanged.
Candidate and rollback syntax were rehearsed before reload. Two authenticated public acceptance passes succeeded after reload. The activation script restores the original configuration automatically if acceptance fails.

## SSH-only archive isolation

The public API/export worker contain no upload SSH key or RAW_ARCHIVE_SFTP settings. A host-only private Unix-socket broker holds the pinned SSH credential and permits only bounded reads of verified receipt identities. It verifies the full remote file size and SHA-256 before streaming; the client verifies them again.

The Storage Box credential still has provider upload capability inside that private broker. This is application/service isolation, not a provider-enforced read-only subaccount. No web account was needed for this release. A provider read-only credential remains preferable when available.

The new database role grants SELECT on named authentication, zone, catalog and visualization tables only. Verified in both databases: no administrative privileges or memberships, no schema/database CREATE, read-only transactions, eight connections, eight-second statements, 150 ms lock timeout, 8 MiB work_mem, JIT off. Only this new role's JIT setting changed.
Get-only S3 credentials use `S3_READ_ONLY=true`, so readers do not probe/create buckets. Reader/worker alone resolve green-mind.ch to the existing private bridge 172.28.20.1 for S3 TLS 9444. Receiver networking and global database settings were not altered.

## Acceptance: PASS

- Real Gateway and Direct series: 1 hour, 7 days, 30 days, 365 days; nonempty and bounded to <=1205 points per series.
- Seven-day CSV values/counts agree with the displayed series.
- Real hidden-zone access denied; jobs inaccessible to another owner; anonymous requests denied.
- Actual queued Gateway and Direct jobs completed under the unchanged 512 MiB host-memory guard. ZIP manifests, all included byte sizes and SHA-256 values, and full WAV decoding passed.
- Two public passes repeated the series, CSV, authorization and download checks. Individual series requests in those passes took 0.044–0.835 seconds; these samples are not p95/SLO evidence.
- Eight independent archive readbacks (oldest WAV per sensor, bounded sample), including Gateway and Direct, passed full size/hash and WAV decode. Additional forced Gateway/Direct broker restores passed without removing source objects.
- Five public frontend assets returned nonempty HTTP 200 responses. A rendered browser usability review was not performed in this SSH-only acceptance.
- All actual receiving containers kept their pre-task start time/restart count. Existing Direct restart count 2 predates this work.
- After switch, Gateway 8120, Direct 8003 and Legacy 8000 each healthy. Direct spool 2,027,680 bytes; assembler healthy.
- At 16:11:44 UTC, Gery Gewächshaus, Peter_buero and Ruedi-Meier_Gewaechshaus had fresh heartbeats. Previous 15 minutes: 16, 6 and 18 new WAVs respectively. Direct: 299 chunks in five minutes, latest 16:11:44. These bounded observations do not prove zero loss at every instant.

## Resource protection and reversible retirement

Five unused read-only API containers, proven absent from the complete Nginx configuration, were stopped to free RAM/connections. No container, volume or historical file was deleted. All receiving and actively routed services remained unchanged:

- gm-release-production-d387f260d6bb-9862fb-api-1 (8004)
- gm-visual-staging-visual-api-1 (8005)
- gm-release-production-61cce4e17f45-080409-api-1 (8006)
- gm-release-staging-d387f260d6bb-812553-api-1 (8007)
- gm-release-staging-61cce4e17f45-dadc4c-api-1 (8009)

They can be restarted if needed; older routed readers remain available for rollback.
New API: 192 MiB/CPU20%; frontend: 128 MiB/CPU20%; worker: 128 MiB/CPU10%; broker: 64 MiB/CPU10%, no swap. Broker observed peak 26,611,712 bytes, zero restarts. A subsequent sample showed API 95.27 MiB and frontend 70.3 MiB.
Host MemAvailable fluctuated around 464–646 MiB. Export/archive guards preserved the 512 MiB reserve and load limit 2.4; resource pauses occurred, then actual jobs completed. This constrained host can still delay downloads under load. No guard was weakened.

`greenmind-archive-export.timer` is active; its first service exit was successful. Existing midnight Swiss-time WAV copy and drain timers remain active and unchanged. Deletion remains disabled in both copier and reader.

## Archive reconciliation at approximately 16:08 UTC

| Kind | Verified files | Verified bytes | Pending files | Pending bytes |
|---|---:|---:|---:|---:|
| Gateway | 119,280 | 53,517,509,804 | 29,886 | 13,404,549,902 |
| Direct | 964 | 436,533,594 | 2 | 912,088 |
| Total | 120,244 | 53,954,043,398 | 29,888 | 13,405,461,990 |

Totals: **53.95 GB verified; 13.41 GB pending** (decimal GB).
Gateway source catalog had 149,167 objects; Direct 966. One additional Gateway entry has invalid/incomplete checksum metadata and is excluded from the eligible pending count. It remains local. Zero receipt identity/size/hash mismatches were found.
The journal was reconciled against bounded read-only source catalog scans, then eight full readbacks were performed. This is not a fresh remote byte-for-byte audit of every archived object. Copying and ingestion continue; totals are a dated snapshot, not a promise that everything is synchronized.

## Remaining before any deletion

Deletion is outside this release and still requires explicit human instruction. Reconcile the eligible backlog to zero, investigate the one invalid Gateway metadata entry, preserve an independently recoverable index/snapshot, and test any remaining legacy/raw consumers plus restoration when local source is absent. The proposed independent backup and seven-day local grace guard are not implemented by this release. Do not enable RAW_ARCHIVE_READS_ACCEPTED, deletion, retention or pruning based on these sample restores alone.
