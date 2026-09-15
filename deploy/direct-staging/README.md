# Staging Direct pilot

Explicit opt-in deployment, independent from the normal `gm-staging` compose
project and outside its rsync destination: `/home/traver/greenmind-direct-staging`.
No production commands are included. Do not enable this as part of the standard
Production deployment.

The shared host has little free RAM. Reuse the existing Staging PostgreSQL cluster
with a **separate database and restricted login**, rather than adding PostgreSQL.
Two bounded Direct processes (128 MiB each) retain independent ingestion/assembly.
This is a one-sensor AD8232 pilot, not a capacity release for 100 sensors.
Direct spool: 64 MiB total / 16 MiB per device; segment maximum 2 MiB; dedicated
bucket quota 1 GiB. Retention deletion remains disabled. Inspect headroom after
startup and stop the Direct services if they destabilize the host.

Stage the files here, the reviewed `nginx/test.green-mind.ch.conf` as
`nginx-staging.conf`, and `backend/app/direct` as `direct/` in that directory.
Write the SHA256 of the current live Staging Nginx config to `nginx-before.sha256`.
`prepare.py` creates only named Staging resources and writes local 0600 secrets;
never commit/copy its private directory into Git or ordinary deployment bundles.

Operator sequence after review:
1. `python3 prepare.py`
2. `docker build --network none -t greenmind-direct-staging:hotspot-v2 .`
3. `docker compose -f compose.yml config --quiet`
4. `docker compose -f compose.yml run --rm --no-deps direct-api python -m app.direct.provision init-schema`
5. `docker compose -f compose.yml up -d --no-build`
6. Check the local Direct health and server memory.
7. User executes `sudo bash /home/traver/greenmind-direct-staging/activate-proxy.sh`.
8. Verify public device authentication, bucket read-back and dashboard pairing.

Nginx's new public Direct location uses an independent loopback receiver. The
existing `/api/` routes remain unchanged. An internal TLS listener on the Staging
Docker bridge (172.28.21.1:9443) forwards to Staging MinIO, preserving S3 signatures.
Both sensor HTTPS and S3 certificate verification remain enabled. No new public
MinIO listener or production bucket is created.

Rollback: stop only this compose project (`docker compose -f compose.yml stop`),
restore the timestamped `greenmind-staging.before-direct-*` proxy backup, run
`sudo nginx -t`, and reload Nginx. Keep the Direct database, bucket and secret files
for diagnosis; do not remove volumes or data. Revert the frontend release on
`develop` separately if required. The sensor's full original 16-MB flash backup is
kept locally in the private validation directory.
