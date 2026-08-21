# Optional development workspace tools

The canonical local workflow is `cp .env.example .env && make dev`. The helpers in this
directory exist only for the optional bind-mounted `docker-compose.dev.yml` overlay and for a
historical workspace at `~/gm_dev`.

## Bind-mounted development overlay

The overlay keeps the backend on its image-defined unprivileged `appuser`, which also owns the
persistent firmware volume. It runs the bind-mounted frontend as the current host user so Next.js
does not create host artifacts owned by a container-only UID. Source the helper so its variables
remain in the current shell:

```bash
source dev-tools/docker-env.sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

`docker-env.sh` exports `CURRENT_UID` and `CURRENT_GID`; those exact names are consumed by the
frontend override in `docker-compose.dev.yml`. The overlay bind-mounts source code, runs Uvicorn
with reload, and runs the Next.js development server. It is not a production deployment.

## Permission repair

`fix-permissions.sh` recursively changes ownership using `sudo`. It targets `~/gm_dev` when that
directory exists; otherwise it targets the current working directory.

```bash
pwd
./dev-tools/fix-permissions.sh
```

Run it only after inspecting the script and confirming both the current directory and printed
target. It is unnecessary unless a container created root-owned files in the intended workspace.

## Destructive workspace reset

`reset-dev.sh` deletes and recreates **only** `~/gm_dev` after an interactive confirmation. It
does not preserve uncommitted work, ignored files, local databases, or generated credentials.

```bash
./dev-tools/reset-dev.sh
```

Treat this as a destructive recovery tool, not a normal cleanup command. Back up required data,
verify the displayed absolute target, and prefer a fresh clone when provenance is uncertain.
