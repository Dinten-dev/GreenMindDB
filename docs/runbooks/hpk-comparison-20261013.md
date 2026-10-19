# HPK-derived Direct comparison

The authenticated `/api/v1/visualization/hpk` entry lists the user's existing Direct devices. Each comparison shows unchanged 380 Hz source samples and an explicitly derived 100 Hz trace with the same display scale. Existing charts, CSV data and default WAV downloads keep their original semantics.

`processing=hpk` is optional on Direct `waveform` and `wav` endpoints. The derivative requires mono PCM16, 380 Hz, `unsigned-mv-linear-int16-v1` calibration. The WAV contains signed int16 `round(mV * 10)` at 100 Hz; its filename and response headers identify the derivative. Unknown calibrations fail closed. Processing never writes source data or SQL projections.

The implemented chain comes from pgloor/biolingo-hpk commit `c3ad7fcdfb86f3321ce263abdabde671da98a7bc`: LP alpha 0.25, eight-sample moving average and four-sample mean. HPK computes but does not store its high-pass output, so no high-pass is applied here. Direct's 380 Hz input is additionally resampled 20/19 with a centred 401-tap Kaiser FIR (beta 5), normalized separately for DC in each phase. Source edges are extended by their boundary value. Each contiguous run initializes filters from its first value. State resets at real gaps and segment boundaries; this differs from continuously running HPK firmware. The timestamp marks the first sample in the averaged four-sample group, with nominal filter delay about 20 ms.

Packet boundaries are merged before filtering; missing samples are never synthesized. Full run history precedes a preview slice. At most ten minutes are processed, with one processing slot per reader process and immediate retryable rejection of concurrent filtering. Rate limits, authentication, tenant authorization and zone checks remain active. The HTML view uses same-origin authenticated requests, nonced scripts and private/no-store responses.

The reader's one-slot database pool is released after authorization and immutable metadata selection, before `load_source` or object storage reads. This also repairs the existing original-waveform pool deadlock.

## Verification

22 targeted tests pass against PostgreSQL with `pool_size=1`, `max_overflow=0`. Tests cover mains harmonics, low-frequency preservation, units, clipping, malformed and oversized inputs, gap handling, packet splitting, preview history, concurrency, unchanged originals and access isolation. The exact archive reader package passed a separate service check with unchanged networking, storage, 192 MiB memory and 25% CPU limits. Full real WAV reads, derivatives, anonymous denial and cross-tenant denial passed. The public GreenMind endpoints passed authenticated comparison, sensor selection, raw preview and WAV hash checks.

On two existing Direct recordings, AC RMS falls from about 55.7–55.8 to 40.4–40.7 mV, about 27%. AC RMS includes useful signal and noise. No HPK original recording is available, and the current test electrodes are open. This is not proof that the hardware noise source is resolved.

## Deployment and rollback

Only `visualization/direct_api.py`, `visualization/hpk_filter.py` and `visualization/hpk_view.py` changed relative to the actual reference-aware archive reader. Authentication, PostgreSQL/PgBouncer settings, storage access and reference reconstruction modules are retained.

Active source root: `/opt/greenmind/reader-hpk-thischat-20261013/backend`. `zz-thischat-hpk.conf` adds its environment file to `greenmind-archive-reader-api-20261010.service`. The earlier compatible reader at `/opt/greenmind/reader-reference-thischat-20261012/backend` remains intact. Removing only this new drop-in and restarting the reader restores that compatible version. Never restore a pre-reference reader or the old payload-releasing worker. The archive-required hold worker remains the compatible worker rollback.

Ingest, firmware, raw retention and deletion policy were not changed by this deployment. The 24-hour reception/integrity acceptance remains ongoing; earliest completion is 14 October 2026, 09:40 Europe/Zurich.
