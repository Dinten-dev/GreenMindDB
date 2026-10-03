# Dashboard history and original-WAV downloads

Activated on Production on 1 October 2026, 16:11 UTC, through the immutable runtime revision `a66ae7c8b1e539fb5998ac3efc98c4945caf174d`. See [production acceptance](ARCHIVE-PRODUCTION-READINESS-20261001.md) for hashes, checks, resources and limits. Example defaults remain disabled; copying source files does not activate the feature.

## Use

Select a sensor and a period in Measurements. The chart and CSV use the same bounded resolution and period. Gateway and Direct are authorized through existing organization/zone permissions. Older Gateway signal summaries can use verified complete WAV features; timestamps, extrema and overlap limitations remain explicit. Fine views keep the existing readings path.

Use the original-WAV export panel to queue a download for the selected interval. The isolated worker prepares ZIP parts and a manifest with source identity, SHA-256, byte size and decoding metadata. Download becomes available only after the entire job verifies. Jobs are owned by the requesting user and generated exports expire after 24 hours. Existing single-WAV downloads remain available.

A resource pause keeps the job queued; it does not expose an incomplete ZIP. The production worker uses a 512 MiB host-memory reserve and load guard, so a busy server can delay preparation. Source objects are preferred; only a genuine source 404 permits the verified SSH archive fallback.

## Safety

No original WAVs have been removed for this release. All deletion, retention and pruning flags remain false; archive deletion acceptance remains false. The private host broker permits verified reads without mounting upload credentials into public containers. Its credential is not a provider-enforced read-only account; see [SSH reader isolation](ARCHIVE-SSH-READER.md).

Readback samples and successful downloads do not establish complete archival coverage or deletion readiness. The current reconciliation backlog, invalid metadata entry and independent backup/legacy-consumer gates are recorded in the production acceptance document. Browser-rendered usability and sustained p95 performance were not measured during this SSH-only acceptance.
