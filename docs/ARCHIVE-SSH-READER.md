# Archive reads with SSH-only Storage Box access

When a dedicated read-only Storage Box subaccount cannot be provisioned, the
public API and export worker must not inherit the upload SSH key. Deploy a
separate host-only `app.raw_archive.read_broker` service instead. A read-only
subaccount remains preferable when administrative account access is available.

The broker holds the existing pinned SSH credentials in its private service
environment and exposes a Unix socket, not a network listener. The socket is
restricted to the API/worker UID. It accepts only bounded availability checks
and reads of Gateway/Direct identities already in the verified journal. It
derives remote paths from receipts, downloads through SFTP and checks complete
byte size and SHA-256 before streaming. There are no upload, deletion, shell,
arbitrary-path, user-management or database-write methods. The client checks
size and SHA-256 again before exposing a body to a downloader.

Mount the socket directory read-only into the API and export worker. Configure
`RAW_ARCHIVE_READ_BROKER_SOCKET=/broker/read.sock`,
`RAW_ARCHIVE_READ_SCRATCH_DIR=/exports` and `RAW_ARCHIVE_READS_ENABLED=true`.
Keep all deletion, retention and pruning flags false. Public containers must
contain no `RAW_ARCHIVE_SFTP_*` settings or SSH keys. Their database role grants
SELECT on named authentication, zone, catalog and visualization tables only.

The broker uses one simultaneous SFTP read, four bounded socket connections,
2 Mbit/s bandwidth, a 64 MiB file ceiling, a 2 GiB free scratch-space guard,
512 MiB host MemAvailable and 2.4 load-average guards. It checks Gateway 8120,
Direct 8003 and Legacy 8000 health before and after retrieval. The host unit
must enforce memory/CPU/pid limits, read-only journal/config/code, private
scratch, no added privileges and a private runtime socket directory.

Existing source objects are preferred. Archive fallback runs only on an
explicit source 404; authentication errors never become archive fallbacks.
Acceptance must also explicitly restore verified Gateway and Direct receipts
through the broker without deleting the source, then test authorized ZIP jobs,
hash manifests, WAV decoding, hidden zones and job-owner isolation.

The shared Storage Box credential retains upload capability on the private
host broker. Isolation reduces exposure; it does not convert that credential
into a provider-enforced read-only account. A read-only subaccount can replace
it later. This mechanism alone does not authorize any deletion or establish
that all historical files have been synchronized.

## Coarse Gateway views under receiver load

`VISUAL_WAV_FEATURE_SERIES_ENABLED=true` opts the read sidecar into a coarse
Gateway signal path for display steps of at least ten minutes. It uses only
verified mV feature rows whose source SHA-256, sample rate and calibration
match the corresponding WAV, with complete/inferred timing and full coverage.
The route applies the existing zone authorization first. Fine views and mixed
or environmental sensors continue to use the original readings path.

Summaries weight mean and mean-square by actual sample count. Min/max remain
observed extrema; RMS and standard deviation derive from those weighted sums.
Only complete recordings wholly inside the selected window contribute. No
statistics are split into invented sub-recording windows, and no median of
medians is reported. Display points and CSV use the same function. CSV adds
actual source interval boundaries and observed timestamp-overlap seconds.

Historic Gateway filenames can imply one-second overlaps between consecutive
recordings. These remain explicitly reported; coverage is unknown for affected
points rather than falsely claiming continuous or duplicate-free wall time.
The chart labels full-WAV summaries and exposes overlap in its tooltip. This
path does not alter stored readings, features, source WAVs or ingestion.
