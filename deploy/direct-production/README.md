# Production Direct: prepared, explicitly activated

This configuration lives on `develop` for review. It does not authorize a main
merge, a Production deployment, sensor flashing or a Raspberry Pi upgrade.
Normal deployments never invoke this helper. Its independent maintenance command
requires the `enabled` marker or explicit first activation. Staging never runs
the Production helper.

## Isolation

| Resource | Production Direct | Existing Staging Direct |
|---|---|---|
| Compose project | gm-direct-production | gm-direct-staging |
| Loopback API port | 8003 | 8002 |
| Database and login | greenmind_direct_production | greenmind_direct_staging |
| Object bucket | greenmind-direct-production-hotspot | greenmind-direct-staging-hotspot |
| Private TLS storage listener | 172.28.20.1:9444 | 172.28.21.1:9443 |
| Dashboard / firmware target | green-mind.ch | test.green-mind.ch |
| Firmware credential namespace | gmdirectprod | gmdirect |

The two Direct processes reuse the existing Production PostgreSQL and MinIO
services through `greenminddb_default`. They receive only a dedicated Direct
login and restricted object-store account. The preparer creates no Legacy tables,
does not rotate Legacy credentials, and never stops services. Its explicit schema
command refuses databases containing non-Direct tables. Bucket access is restricted
to `direct/*`, with an explicitly reviewed storage quota. Retention remains disabled/dry-run. Choose the quota before preparation: one 380-Hz PCM16 sensor produces about 65.7 MB/day (5.9 GB over 90 days), plus metadata and late revisions. The previous 1-GiB pilot default is deliberately removed.

Production Direct requires `DIRECT_BACKEND_IMAGE=sha256:...` from the reviewed release bundle. An ordinary main deployment no longer refreshes this independent receiver. Pairing
validates ownership against the existing dashboard API; Legacy registration,
JSON, WAV, analytics and WebSocket routes remain unchanged. The optional generic
`docker-compose.direct.yml` also now passes both dashboard configuration values.
Use this dedicated configuration for the shared host, not the generic overlay's
Staging defaults.

## First activation — only after separate Production approval

1. Review the exact DB/Ardu/Gateway commit IDs, tests and package. Build the
   backend off-host using `deploy/release/build-bundle.sh` or the manual package
   workflow. Verify and load its immutable image. Do not run a normal Main/full-stack
   deploy to obtain the image: the existing Gateway receiver must remain running.
   Set `DIRECT_BACKEND_IMAGE=sha256:THE_REVIEWED_LOADED_CONFIG_ID` for the activation
   command. Keep the previous images and nginx configuration available.
2. On the host as the Docker operator, prepare the dedicated state directory:

   ```sh
   install -d -m 700 /home/traver/greenmind-direct-production
   install -m 700 /absolute/reviewed-package/deploy/direct-production/prepare.py /home/traver/greenmind-direct-production/prepare.py
   python3 /home/traver/greenmind-direct-production/prepare.py --prepare-production --bucket-quota-gib REVIEWED_GIB
   ```

   Secrets are generated only there in `private/` (0600). Back up that directory
   securely; retain it on retries. Never copy Staging credentials or delete the
   secret file to regenerate an already provisioned login. The generated values
   are not printed and are excluded from Git and deployment rsync.
3. Review and install `nginx/green-mind.ch.conf` as
   `/etc/nginx/sites-available/greenmind-prod`, preserving the current file as a
   timestamped backup. Run `sudo nginx -t` before reloading; restore the backup
   if validation fails. The existing site's certificate paths are reused. The
   Direct API listens only on loopback8003, storage TLS only on the Docker gateway
   address9444. Do not open those ports publicly. This remains an explicit operator
   step; apply only the reviewed Direct additions to the installed configuration,
   preserving unrelated changes and any existing visualization routes.
4. Activate explicitly using the reviewed checkout's helper:

   ```sh
   DIRECT_BACKEND_IMAGE=sha256:REVIEWED_CONFIG_ID bash /absolute/reviewed-package/deploy/direct-production/rollout.sh /home/traver/greenmind-direct-production --activate
   ```

   It initializes only Direct tables, starts only `gm-direct-production`, checks
   database/ingestion health, assembler heartbeat and public dashboard pairing,
   then writes `enabled`. Supply the immutable `DIRECT_BACKEND_IMAGE` explicitly. Failed initial activation stops only Direct. Subsequent Direct updates remain an explicit, separate maintenance action.
5. Select the **explicit** `direct_biolingo_production` firmware target from
   GreenMindArdu, flash only the approved device, connect to its hotspot and use a
   fresh Direct code from **green-mind.ch**. Staging codes and saved tokens cannot
   migrate across environments. The default firmware build still targets Staging.
6. Verify an old Gateway sensor and a Direct sensor together: advancing sequences,
   no new loss counters, completed WAVs with correct counts/checksums and continued
   Gateway reception during a Direct interruption. No physical-device check is
   replaced by the automated build or rollout health checks.

## Rollback and disabling Direct

For a separately approved Direct maintenance update, the rollout pins the previous running Direct API image as
`greenmind-direct-production:rollback` before updating. If startup/health fails,
it restores that image and the previous Compose file. Schema initialization is
additive; no database or bucket is deleted. This rollback covers **Direct only**,
not the unchanged ordinary frontend/backend deployment process.

To disable Direct explicitly, remove the `enabled` marker, then run
`docker compose -p gm-direct-production -f compose.yml stop` from its state
directory. Keep `private/`, its database and its bucket for retry/recovery. The
legacy `greenminddb` project remains running. To manually select the saved image,
set `DIRECT_BACKEND_IMAGE=greenmind-direct-production:rollback` for Compose up.
Do not use volume deletion, bucket deletion or Legacy migration downgrades.

## Test scope

Automated tests execute the actual preparation and rollout scripts using isolated
Docker/SSH substitutes: separate resources, repeatable secrets, no activation by
default, schema-before-start, failed activation, and restoration of the old Direct
image. Existing backend tests exercise old/new reception and error isolation.
Real Production activation, credentials and physical Gateway/DUAL hardware checks
remain operator validation after approval. Storage reserve is outside this change;
release images are now built off-host.
