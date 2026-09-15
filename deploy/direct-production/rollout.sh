#!/bin/bash
# Separate opt-in project; never starts, stops or migrates Legacy services.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state_dir=${1:?Usage: rollout.sh ABSOLUTE_STATE_DIRECTORY [--activate]}
[[ "$state_dir" == /* && "$state_dir" != "$source_dir" ]] || { echo 'Use a separate absolute state directory.' >&2; exit 1; }
activate=${2:-}
[[ -z "$activate" || "$activate" == --activate ]] || exit 1
if [[ "$activate" != --activate && ! -f "$state_dir/enabled" ]]; then
    echo 'Production Direct is not enabled; Legacy deployment is unchanged.'
    exit 0
fi
[[ -f "$state_dir/private/direct.env" ]] || { echo 'Prepare dedicated Direct credentials first.' >&2; exit 1; }
umask 077
exec 9>"$state_dir/private/rollout.lock"
flock -n 9 || { echo 'Another Direct rollout is running.' >&2; exit 1; }
cd "$state_dir"
compose=(docker compose -p gm-direct-production -f compose.yml)
previous_image=''
if [[ -f compose.yml ]]; then
    previous_container=$("${compose[@]}" ps -q direct-api)
    if [[ -n "$previous_container" ]]; then
        previous_image=$(docker inspect --format '{{.Image}}' "$previous_container")
        docker image tag "$previous_image" greenmind-direct-production:rollback
        cp compose.yml compose.previous.yml
    fi
fi
committed=false
started=false
restore() {
    if [[ "$committed" != true ]]; then
        if [[ -n "$previous_image" ]]; then
            cp compose.previous.yml compose.yml
            export DIRECT_BACKEND_IMAGE=greenmind-direct-production:rollback
            if [[ "$started" == true ]]; then "${compose[@]}" up -d --no-build; fi
            echo 'Previous Direct image restored; inspect Direct health before retrying.' >&2
        elif [[ "$started" == true ]]; then
            "${compose[@]}" stop
            echo 'Initial Direct activation failed; Direct stopped, Legacy unchanged.' >&2
        fi
    fi
}
trap restore EXIT
cp "$source_dir/compose.yml" compose.yml
# The normal Production build supplies the reviewed backend image.
export DIRECT_BACKEND_IMAGE=greenmind-backend:latest
"${compose[@]}" config --quiet
# A failed schema preflight leaves running Direct containers alone.
"${compose[@]}" run --rm --no-deps direct-api python -m app.direct.provision init-schema
started=true
"${compose[@]}" up -d --no-build
# Require active ingestion, a live assembler and available dashboard pairing.
ready=false
for attempt in {1..30}; do
    if "${compose[@]}" exec -T direct-api python -c '
import json, urllib.request
def read(url):
    return json.load(urllib.request.urlopen(url, timeout=5))
health = read("http://127.0.0.1:8002/health")
assert health["direct_ingest"] == "healthy" and health["assembler"] == "healthy"
assert read("https://green-mind.ch/api/v1/direct-ingest/setup")["hotspot_pairing"] is True
' >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 2
done
[[ "$ready" == true ]] || { echo 'Direct health, assembler or public pairing failed.' >&2; exit 1; }
if [[ "$activate" == --activate ]]; then touch enabled; fi
committed=true
echo 'Production Direct healthy; pairing available. Verify real sensor/WAV reception separately.'
