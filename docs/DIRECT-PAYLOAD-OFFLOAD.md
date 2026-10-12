# Direct partial-segment spool recovery

The previous worker releases SQL chunk bytes only after every referenced segment
is sealed. Verified partial segments can remain unsealed for seven days; their
accumulated bytes can exhaust the global spool quota and reject new uploads.

The additive `direct_chunk_payload_ref` table permits exact reconstruction of
original chunks from immutable WAV revisions. Chunk identities and duplicate
responses remain durable. Late arrivals can rebuild a newer revision using both
inline chunks and older WAV references. Old WAV versions remain required.

## Default and permanent storage policy

- `DIRECT_PAYLOAD_OFFLOAD_ENABLED=false` until the reader migration is accepted.
- Storage Box WAV originals remain permanently stored.
- Offload requires a complete independent Storage Box readback and original-chunk
  SHA-256 agreement; source storage verification alone is insufficient.
- `RAW_ARCHIVE_DELETE_ENABLED=false`, `RETENTION_ENABLED=false`, and
  `DIRECT_RETENTION_ENABLED=false`; `DIRECT_RETENTION_DRY_RUN=true`.
- SQL queue-byte exchange is independent of later local WAV eviction.
- No sensor protocol, firmware provisioning, or OTA action changes.

## Activation order

1. Back up the current Direct database and pin all running image hashes.
2. Run `python -m app.direct.provision upgrade-payload-references` against the
   existing Direct database. It adds one table under a 100 ms PostgreSQL lock
   timeout and changes no existing rows. Use `--read-role NAME` only for readers
   already permitted to select Direct chunks; additional grants fail closed.
3. Include chunk identities, pieces, references and budget in private recovery
   snapshots. Restore a snapshot into an empty isolated database and reconstruct
   complete original chunks from independently read archived WAVs.
4. Update every assembler and visualization reader before enabling offload.
   Identify processes by their command, not their container name: the current
   source has one `app.direct.worker` assembler and a separate
   `app.visualization.direct run` projection worker. Both need compatible code;
   the projection worker must remain available. Keep the active ingestion APIs
   serving requests during the bounded, verified assembler handoff.
5. Verify that the copy-only archive catalog includes immutable unsealed
   revisions, including older revisions superseded by late arrivals. This must
   not relax any local WAV deletion gate.
6. Configure `DIRECT_ARCHIVE_BROKER_SOCKET` with an absolute, private Unix socket
   connected to the verified read-only archive broker. Public HTTP endpoints,
   unverified destinations, and receiver credentials are not substitutes.
7. Verify real source and archive reads, then enable offload for the worker only.
   Each pass exchanges at most 200 chunks and 1 MiB, with device -> budget ->
   chunk locks and no network reads under those locks. WAV caches are bounded;
   large WAVs expose only the requested PCM range without a second full copy.
8. Start a fresh complete 24-hour acceptance: actual per-sensor persisted progress,
   upload status, spool/backlog, charts, downloads, limits, and disabled deletion.

## Health and rollback

`/health` remains a process-liveness endpoint. Capacity exhaustion is reported as
`capacity_limited`, including quota and remaining bytes, without causing restart
loops. `/readiness` reports actual latest persisted progress and returns 503 when
the worker is unhealthy or there is insufficient room for a maximum-size chunk.
Device-specific quotas and durable retry behavior remain unchanged.

After references exist, disable further offload but keep compatible readers and
workers. Never roll back to an assembler that only reads inline payloads. A full
old-version rollback requires verified rehydration and a quota check first. Do
not reset spool counters, fabricate acknowledgements, remove reference rows, or
delete pinned WAV revisions to restore availability.

Offline tests cover full-spool recovery, partial and cross-boundary WAVs, late
arrivals during readback, duplicate/conflicting retries, archive corruption,
failed commits, concurrent accounting, archived-only reads, and bounded memory.
Production activation and a completed observation are separate acceptance gates.
