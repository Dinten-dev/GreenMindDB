# Production Direct: prepared, explicitly activated

This configuration lives on `develop` for review. It does not authorize a main
merge, a Production deployment, sensor flashing or a Raspberry Pi upgrade.
A normal deployment checks `enabled` outside the checkout; absent that marker,
it does not start the Direct project. Staging never runs the Production helper.

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
to `direct/*`, with a 1-GiB storage quota. Retention remains disabled/dry-run.

Production Direct uses the normal `greenmind-backend:latest` image, refreshed after
an approved main deployment, instead of a frozen Staging pilot image. Pairing
validates ownership against the existing dashboard API; Legacy registration,
JSON, WAV, analytics and WebSocket routes remain unchanged. The optional generic
`docker-compose.direct.yml` also now passes both dashboard configuration values.
Use this dedicated configuration for the shared host, not the generic overlay's
Staging defaults.

## First activation — only after separate Production approval

1. Review the exact DB/Ardu/Gateway commit IDs, tests and main diff. Keep the
   previous application images and nginx configuration available. Deploy the
   approved DB commit through the ordinary Production process; this creates the
   reviewed `greenmind-backend:latest`. No Direct marker exists yet.
2. On the host as the Docker operator, prepare the dedicated state directory:

   ```sh
   install -d -m 700 /home/traver/greenmind-direct-production
   install -m 700 /home/traver/greenmind-prod/deploy/direct-production/prepare.py /home/traver/greenmind-direct-production/prepare.py
   python3 /home/traver/greenmind-direct-production/prepare.py --prepare-production
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
   address9444. Do not open those ports publicly. The regular deployment attempts
   this nginx update only if the SSH identity has passwordless sudo; otherwise
   this remains an explicit operator step.
4. Activate explicitly using the reviewed checkout's helper:

   ```sh
   bash /home/traver/greenmind-prod/deploy/direct-production/rollout.sh /home/traver/greenmind-direct-production --activate
   ```

   It initializes only Direct tables, starts only `gm-direct-production`, checks
   database/ingestion health, assembler heartbeat and public dashboard pairing,
   then writes `enabled`. Failed initial activation stops only Direct. Subsequent
   regular Production deployments refresh Direct only while this marker exists.
5. Select the **explicit** `direct_biolingo_production` firmware target from
   GreenMindArdu, flash only the approved device, connect to its hotspot and use a
   fresh Direct code from **green-mind.ch**. Staging codes and saved tokens cannot
   migrate across environments. The default firmware build still targets Staging.
6. Verify an old Gateway sensor and a Direct sensor together: advancing sequences,
   no new loss counters, completed WAVs with correct counts/checksums and continued
   Gateway reception during a Direct interruption. No physical-device check is
   replaced by the automated build or rollout health checks.

## Rollback and disabling Direct

The rollout pins the previous running Direct API image as
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
remain operator validation after approval. Server memory reserve is intentionally
outside this change; the existing Production build strategy is unchanged.
