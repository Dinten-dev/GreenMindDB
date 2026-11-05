# Direct Live: request cost and Staging isolation

The verified HTTPX trust context is loaded once before receiver readiness.
It keeps certificate and hostname verification enabled. Request-scoped clients
still perform each dashboard identity/capability/zone check, carry only that
request's credential and discard their cookie jar afterwards. No authorization
or viewer data is cached. The private Unix-socket transport uses the same
preloaded context, avoiding unnecessary repeated CA-bundle parsing for HTTP.

`deploy/release/staging_live_proxy.py` prepares a longer device-route prefix for
the dedicated Staging site. It requires an already configured, distinct
loopback management upstream and copies the existing raw-route proxy headers.
Raw chunks, registration, legacy routes, other sites and resource limits are
unchanged. It rejects Production files, ambiguous layouts and conflicting edits.

Before activation, save the exact active configuration and other-site hashes;
validate the candidate with the real Nginx parser and an isolated include tree.
Use a graceful reload, verify authenticated device/viewer requests and raw ACKs,
and retain the original file for conditional rollback. No new listener, database,
cache service or worker is required by this change.

Acceptance uses the separate test device and its existing hotspot: real 400-Hz
raw capture, two viewers, a visible browser, 30/60/180-second windows and volatile
shared markers. Check frame continuity, freshness, raw/WAV byte identity and CPU
throttling without filling in missing preview points. Test results belong beside
the exact source/binary identities; a synthetic pass alone is insufficient.
