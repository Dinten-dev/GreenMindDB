# Optional Direct ingest — local implementation for Staging review

Relative to the reviewed main baseline `3c82579`, this feature is additive.
The existing `app.main`, Gateway endpoints, models,
WAV reader, feature worker, retention worker and authentication are unchanged.
The normal deployment does not include the Direct Compose overlay.
The older `develop` differs from this baseline in 52 pre-existing files; see the
release review before deciding which baseline will be delivered to Staging.
The owner has now selected main plus Direct for develop/Staging. The accompanying
review also strengthens byte verification of legacy derived archives and fixes
frontend security dependencies. Staging retention is explicitly disabled during
the sensor pilot, including if older server environment values requested it.

Release order required by the owner: review **every change**, then authorize
`develop`/Staging; connect the first real test sensors; verify both paths; only
then discuss a separately authorized production release. No push is authorized
merely by completing these tests.

## Architecture and operating modes

```text
GATEWAY: unchanged sensor → unchanged Pi → /ingest + /wav/upload
         → existing database, MinIO, features, retention

DIRECT:  explicitly provisioned new sensor → HTTPS /direct-ingest/chunks
         → separate PostgreSQL (payload and metadata committed together)
         → separate assembler → dedicated MinIO bucket → per-channel features

DUAL:    supported test sensor → Gateway (canonical existing analysis)
                              → Direct (comparison scope only)
```

Missing firmware mode means GATEWAY. Existing firmware is not modified and
does not need a configuration update. DIRECT and DUAL must be explicitly set
in the new test firmware and independently provisioned on the server.

Direct devices have their own identity, key, organization and zone. Provisioning
verifies these against a read-only connection to the existing ownership data.
No virtual Gateway is created. In DIRECT, no Pi or Gateway association is needed.
DUAL requires an existing sensor in the same zone and is restricted to the
existing mono/380-Hz/PCM16 comparison profile. DUAL cannot be enabled by request
metadata. Direct comparison results never enter `sensor_reading`, `wav_file`
or the existing dashboard aggregates, so retries or DUAL cannot double-count
legacy measurements. Direct results are available through its authenticated
segment API; a combined dashboard is not implemented by this change.

Four-channel/500-Hz/24-bit data is supported by the Direct protocol and assembler.
The existing Pi only accepts 380 Hz and its WAV format is mono PCM16. Consequently
four-channel PCM24 DUAL is deliberately rejected rather than down-converted.

## API v1

Endpoint: `POST /api/v1/direct-ingest/chunks` on the separately routed Staging host.

| Header | Value |
|---|---|
| Authorization | `Bearer gmd_<device UUID without dashes>_<random credential>` |
| Content-Type | `application/octet-stream` |
| X-GreenMind-Metadata | JSON object below; at most 2,048 bytes |
| Content-Encoding | omitted or `identity`; compression is not supported |

TLS is mandatory. Firmware verifies the provisioned CA certificate and hostname,
does not follow redirects and checks every acknowledgement. Gateway credentials
are not reused as Direct credentials. Never put credentials in URLs or logs.

| Metadata | Contract |
|---|---|
| `protocol_version` | `1` |
| `device_id`, `session_id` | UUID strings; session changes at restart/configuration change |
| `sequence` | unsigned logical chunk counter, at most signed 64-bit maximum |
| `session_start_us` | fixed UTC epoch microseconds for session frame zero |
| `first_frame` | sample-frame index since session start; includes skipped frames |
| `frame_count` | frames in this chunk, at most ten seconds |
| `sample_rate` | integer 1–2,000 Hz, immutable within a session |
| `channels` | integer 1–8, immutable within a session |
| `sample_bits` | 16 or 24; signed two's-complement little endian |
| `channel_labels` | one distinct ASCII alphanumeric label per channel |
| `calibration_version` | explicit version; ADC counts are not silently labelled mV |
| `firmware_version` | explicit version, immutable within the session |
| `payload_sha256` | lower-case SHA-256 of the exact binary payload |

Interleaving is frame-major: frame 0 channel 0, frame 0 channel 1, …, then frame 1.
The exact body length is `frame_count × channels × sample_bits/8`.
Default maximum body: 64,000 bytes. Default maximum reconstructed segment: 32 MB.
At most 1,200 chunks may contribute to one segment, bounding assembler metadata
and run fragmentation. Excessively fragmented input is rejected with 413.
The new sensor example sends one-second blocks; 4 × 500 × 3 is 6,000 bytes/s.

The UTC anchor is fixed for the entire session. Sample indices determine order;
arrival times do not. A session lasts at most 90 days. Start a new session after
changing rate, channels, format, labels, calibration, firmware or server mode.
Clock drift is not corrected by resampling. Real-device clock quality must be
checked on Staging. The test firmware anchors the monotonic clock after UTC
becomes available; Gateway acquisition continues while Direct waits for UTC.

Successful JSON responses contain `status`, `device_id`, `session_id`, `sequence`
and `payload_sha256`. `201/persisted` is sent only after committing bytes,
metadata, segment assignments and quota accounting. `200/duplicate` means the
same full identity and metadata were already committed. Raw bytes can later be
released while the idempotency tombstone remains.

| Status | Meaning / sender action |
|---|---|
| 200 / 201 | Validate all ACK identity fields and checksum, then dequeue |
| 400 | TLS required |
| 401 / 403 | Wrong, disabled or mismatched device identity; correct provisioning |
| 408 | Upload timeout; retry identical chunk |
| 409 | Same identity changed, overlapping frames or session conflict; retain and investigate |
| 410 | New data older than the configured late horizon / sealed segment; retain and report |
| 413 | Payload or metadata too large |
| 415 | Wrong content type or unsupported compression |
| 422 | Invalid metadata, checksum, size, clock or unsupported DUAL profile |
| 429 | Per-device spool quota reached; retry after backoff |
| 503 | Disabled Direct, database unavailable or Direct spool full; retry after backoff |

Retries must keep both payload and metadata unchanged. Lost ACKs are harmless.
Out-of-order chunks are accepted if sequence order and frame ranges are coherent.
New data more than seven days old is rejected by default; already-known identical
chunks still receive duplicate ACKs while their tombstones exist. No new Direct
age restriction is applied to the old Gateway endpoints.

## WAVs, gaps, features and retention

WAV segmentation uses deterministic UTC ten-minute buckets, independently per
device, session and channel configuration. A chunk crossing a boundary is split
by frame index, without altering its samples. The first bucket may begin after
the UTC boundary; `initial_partial` records this explicitly.

An incomplete segment is published after the default 120-second idle period.
Each contiguous run receives its own WAV; the manifest records missing frame
ranges and exact starting indices/times. Nothing is interpolated, zero-filled,
clipped to a lower precision or concatenated across gaps. Late data creates a
new immutable manifest revision; the API returns only the latest revision.
Complete segments are sealed immediately; incomplete segments are sealed after
the late-arrival horizon. Final short segments remain explicitly partial.

PCM24 is decoded with sign extension to integer counts, then converted to
float64 only for feature calculation. All 24 input bits remain in the WAV.
Features are calculated per channel and contiguous run using the existing
`calculate_signal_features` implementation and its version/parameter hash.
ADC counts use signed clipping limits. The PCM16 comparison profile uses the
unchanged legacy scale and limits. There is no fictitious ADS131M04 calibration.

Objects are written into a dedicated `greenmind-direct-*` bucket, read back and
checked by SHA-256 before a revision is published. Source chunk checksums are
also rechecked during assembly. PostgreSQL row locks isolate work across worker
processes. A failed worker transaction leaves the acknowledged source chunks
available for retry. Source payloads are released only when every segment
referencing the chunk is sealed and published. Metadata tombstones remain.

Direct retention defaults to disabled and dry-run. If explicitly enabled, raw
WAVs expire after at least 90 days, and feature manifests after at least 730 days.
Deletion is restricted to sealed segments with verified revisions; object deletion
precedes the raw-deleted marker. Retrying after interruption is safe. Chunk
tombstones are removed only after raw retention and after payload release. Existing
Gateway retention is neither changed nor invoked by the Direct worker.

**Operational limit:** a crash after object upload but before the manifest commit
can leave an unreferenced object. Deterministic keys allow a retry to reuse it,
but this change does not include a general orphan-reconciliation sweeper. Bucket
quota and orphan monitoring must therefore be part of the Staging review; do not
enable a blind lifecycle rule that can delete still-referenced objects.

## Reading results and health

`GET /api/v1/direct-ingest/segments` requires the same device token and returns
at most 100 latest manifests belonging to that device. Each includes source mode,
analysis scope, missing ranges, features and raw availability.

`GET /api/v1/direct-ingest/segments/{segment_id}/runs/{run_index}` retrieves a
verified current WAV for that device. It returns 410 after raw expiry. A token
cannot list or download another device's records.

The private Direct `/health` reports Direct availability, spool usage and
assembler heartbeat separately. The legacy `/health` is unchanged. A healthy
Direct API with an unavailable assembler can keep accepting bounded durable
backlog; it does not imply that WAV reconstruction is healthy.

## Explicit Staging setup after review and authorization

1. Confirm the exact `develop` diff, migrations, rollback and target services.
2. Confirm Staging database, bucket and credentials are distinct from production.
3. Deploy the reviewed backend with the normal Staging workflow. Direct is absent
   from that deployment and its routes remain unavailable until explicitly added.
4. Provision a dedicated Direct database and bucket-scoped object credentials.
   Staging requires a `greenmind-direct-staging*` bucket; production requires
   `greenmind-direct-production*`. Settings reject the wrong environment prefix.
   Apply a bucket quota and reserve database disk capacity; memory/CPU limits
   alone do not prevent exhaustion of the shared physical disk.
5. Add reviewed Direct settings to the server's protected environment, not Git.
   Use `docker-compose.direct.yml` only as an explicit overlay with profile `direct`.
6. Keep `DIRECT_INGEST_ENABLED=false`; run
   `python -m app.direct.provision init-schema` in a one-off Direct container.
   This creates only `direct_*` tables in the dedicated database. It refuses a
   database containing non-Direct tables. It does not run legacy Alembic.
7. Provision each new device with `python -m app.direct.provision add-device`,
   supplying `--organization-id`, `--zone-id`, `--mode` and `--token-file`.
   DUAL additionally requires `--legacy-sensor-id`. Supply the ownership-check
   DSN through `DIRECT_LEGACY_READ_DATABASE_URL` using a read-only account.
   Mount a private output directory for the token file; the token is never printed.
8. Review and install the new Nginx location in the Staging HTTPS server only.
   Set `DIRECT_TRUSTED_PROXY_IPS` to the actual bridge peer. Never trust `*`.
9. Enable Direct and start its API/worker. Check readiness and real TLS rejection.
10. The owner connects the first test sensors; verify old uploads simultaneously,
    data values, ordering, timing, network interruption, restarts and quotas.
11. Production remains untouched until a separate review and explicit approval.

Required Direct settings are documented inline in `docker-compose.direct.yml`.
The overlay reuses the newly built backend image but introduces only Direct
services. Its environment intentionally supplies no production database or
Gateway credentials. The numerical feature adapter imports legacy definitions
but does not open a legacy database connection.

The initial schema is explicit creation for an empty dedicated database. Future
schema evolution needs a versioned Direct migration; do not treat `create_all`
as an automatic upgrade mechanism.

Rollback stops Direct API/worker and removes only its Nginx location. Keep the
Direct database, acknowledged chunks and bucket. Existing Gateway services stay
running. Do not downgrade/drop the new data as an operational rollback.

## Resource and hardware limits

The Compose overlay bounds CPU, memory, process count and logs; the Direct DB
uses separate storage and small connection pools. These do not guarantee
availability under full host failure or shared-disk exhaustion. Staging needs
real storage quotas and reserve capacity before longer sensor tests.

At the target profile, 100 devices produce 51.84 GB raw payload/day before
temporary copies and metadata. Complete-segment cleanup limits raw database
backlog, but this is not a measured 100-device capacity certification.

The new ESP32 example is an opt-in **Biolingo AD8232 test build**, not an ADS131M04
driver or a production firmware replacement. PCM24 transport/assembly is tested;
real ADS131M04 acquisition, PCB pins, calibration, flash security and hardware
restart behaviour still require the real board. It uses RAM queues with four
waiting blocks plus one in-flight block per path (about five seconds normally).
Longer outages explicitly count dropped new frames. RAM does not survive reset;
seven-day SD persistence is not implemented and is not claimed.

## Validation references

Database locking uses the documented [SQLAlchemy row-lock interface](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update)
and [PostgreSQL transaction locks](https://www.postgresql.org/docs/current/explicit-locking.html).
WAV construction uses Python's [PCM WAV support](https://docs.python.org/3/library/wave.html).
Exact executed checks and the full release diff are in `direct-release-review.md`.
