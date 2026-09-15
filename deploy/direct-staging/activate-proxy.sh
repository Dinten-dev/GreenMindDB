#!/bin/bash
# Install the reviewed Staging candidate only; retain an exact rollback copy.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then echo 'Bitte mit sudo ausführen.' >&2; exit 1; fi
base=$(cd -- "$(dirname -- "$0")" && pwd)
target=/etc/nginx/sites-available/greenmind-staging
[[ -f "$base/nginx-staging.conf" && -f "$base/nginx-before.sha256" ]]
expected=$(cat "$base/nginx-before.sha256")
actual=$(sha256sum "$target" | cut -d' ' -f1)
if [[ "$actual" != "$expected" ]]; then
    if cmp -s "$target" "$base/nginx-staging.conf"; then
        nginx -t
        systemctl reload nginx
        echo 'Staging-Proxy ist bereits installiert.'
        exit 0
    fi
    echo 'Staging-Konfiguration wurde zwischenzeitlich verändert. Bitte erneut prüfen.' >&2
    exit 1
fi
curl --fail --silent --max-time 5 http://127.0.0.1:8002/health >/dev/null
backup="${target}.before-direct-$(date -u +%Y%m%dT%H%M%SZ)"
cp -p -- "$target" "$backup"
committed=false
restore() {
    if [[ "$committed" != true ]]; then
        cp -p -- "$backup" "$target"
        nginx -t && systemctl reload nginx
        echo 'Bisherige Staging-Konfiguration wiederhergestellt.' >&2
    fi
}
trap restore EXIT
install -m 644 -- "$base/nginx-staging.conf" "$target"
nginx -t
systemctl reload nginx
committed=true
echo "Staging-Proxy aktiviert. Sicherung: $backup"
