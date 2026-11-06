# Develop and Staging Live integration

Updated 6 November 2026. `develop` is the shared integration branch for this
platform and [GreenMindArdu](https://github.com/Dinten-dev/GreenMindArdu/tree/develop).
The firmware [build and operation guide](https://github.com/Dinten-dev/GreenMindArdu/blob/develop/docs/develop-live.md)
is the companion to this document.

## Current compatibility

| Path | Current source contract |
| --- | --- |
| DIRECT raw capture | Nominal 400 Hz, mono PCM16, explicit raw400 signal profile |
| Gateway and DUAL | Existing 380-Hz contracts, identities and historical recordings retained |
| Live preview | On-demand 12.5 Hz, bounded catch-up packets; no interpolated values |
| Viewer windows | 30 seconds, 1 minute or 3 minutes; ordinary measurement default is 24 hours |
| Rails | Actual ADC rail fraction over a ten-second window, with age/completeness shown |
| Markers | Shared with authorized viewers; volatile only, expire after three minutes |
| Languages | German, English, French and Italian, including authenticated views |

The paired default Staging firmware identifies itself as
`direct-v2.15-rc4-raw400-live-p1-staging`. Protocol v1 remains the default and v2
is explicitly opt-in. Published Direct downloads remain the separate v2.11-rc1
avg20ms/380-Hz package; `develop` does not relabel them or assign device updates.

## Source integration and running Staging

The integrated server recovery is based on
[`89b5599`](https://github.com/Dinten-dev/GreenMindDB/commit/89b559911e12dd4bb5ec83da2f3ff6c948a8607e),
with paired firmware recovery through
[`9d6d7e9`](https://github.com/Dinten-dev/GreenMindArdu/commit/9d6d7e96d0ce4cb187c6c14fc330aaac8c55019f).
Subsequent documentation commits can advance `develop` without changing those
running binaries. Compare runtime code and exact image identities when planning
an update; a branch name alone is not deployment evidence.

The affected Staging raw and management HTTP services received the server
recovery. The application, visualization, frontend, private Live owner and
assembler retained their previous compatible images. This deliberate mixed
image state avoids restarting receiving services merely to synchronize labels.
No Production rollout follows from bringing the source branches together.

## Service boundaries

Raw chunk ingestion keeps its separate receiver, database and immutable
acknowledgement contract. Direct management/control and viewer routes use the
management service. One private `app.direct.live_worker` owns the volatile Live
state over a shared Unix socket; it is not a public listener or a database cache.
`DIRECT_LIVE_ENABLED` defaults to false. Enabling Live requires an explicit
environment configuration and matching `DIRECT_LIVE_SOCKET` mounts.

Each public request still checks active identity, organization/zone permissions
and device status. Verified TLS-context reuse does not share client cookie jars
or cache authorization. The dedicated Staging proxy routes device-management
requests separately from raw uploads; see [the recovery procedure](DIRECT-LIVE-RECOVERY.md).

## Local setup and operation

Follow the [root local-development setup](../README.md#local-development) and
[backend guide](../backend/README.md) for local databases, credentials and
migrations. Live is an additional explicitly configured component; running the
ordinary development stack does not enable or deploy it automatically.

For a separate, authorized sensor use the firmware's N16R8 Staging build only
after identifying its physical hardware and flash layout. Retain its Staging
credentials; do not interchange Production or Gateway pairing codes. Configure
a reachable 2.4-GHz WLAN/hotspot, then open the correct sensor in Measurements
and explicitly select Live. Viewers can use a different internet-connected
network from the sensor.

Network loss can pause the preview and exhaust its finite catch-up window.
Age, incomplete windows and rail staleness remain visible; raw storage has its
own separate buffer and exact acknowledgement rules. For USB diagnostics use
the firmware's [passive observer](https://github.com/Dinten-dev/GreenMindArdu/blob/develop/docs/passive-usb-observation.md),
which does not reset the sensor or reopen a disconnected port automatically.

## Validation and release boundaries

Automated software checks passed for the integrated runtime corrections.
Live was exercised on separate Staging hardware; the user confirmed operation
and requested tests to stop. Confirmed network unavailability affected parts
of that run. It is not a completed endurance, measurement-quality or physical
leaf-touch latency acceptance. The [Step 13 matrix](ACCEPTANCE-20261103.md)
remains the record of outstanding qualification rather than today's setup guide.

The develop/documentation synchronization does not restart hardware testing,
reopen temporary test access, flash sensors, replace release assets, assign OTA,
activate retention/deletion or shut down another Live service. Deployment and
publication remain separate operations under the [release procedure](../deploy/release/README.md).
