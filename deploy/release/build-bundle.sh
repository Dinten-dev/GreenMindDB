#!/usr/bin/env bash
# Build on the operator workstation or CI runner, never on the receiving host.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo"
revision=$(git rev-parse HEAD)
archive_exports_enabled=${GREENMIND_RELEASE_ARCHIVE_EXPORTS_ENABLED:-false}
[[ "$archive_exports_enabled" == false || "$archive_exports_enabled" == true ]] || { echo 'Invalid archive export flag.' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=normal) ]] || { echo 'Commit reviewed changes before building.' >&2; exit 1; }
out=${1:?Usage: build-bundle.sh ABSOLUTE_OUTPUT_DIRECTORY}
[[ "$out" == /* && ! -e "$out" ]] || { echo 'Use a fresh absolute output directory.' >&2; exit 1; }
mkdir -m 700 -p "$out"
build_source=$(mktemp -d)
trap 'rm -rf -- "$build_source"' EXIT
# Ignored local databases, credentials and build caches must never enter an image.
git archive "$revision" | tar -x -C "$build_source"
for component in backend frontend; do
    build_args=(--platform linux/amd64 --label "org.opencontainers.image.revision=$revision")
    if [[ "$component" == frontend ]]; then
        build_args+=(--build-arg "NEXT_PUBLIC_ARCHIVE_EXPORTS_ENABLED=$archive_exports_enabled")
    fi
    docker build "${build_args[@]}" -t "greenmind-release-${component}:${revision}" "$build_source/$component"
done
docker image save "greenmind-release-backend:${revision}" "greenmind-release-frontend:${revision}" | gzip > "$out/images.tar.gz"
git archive "$revision" deploy/release deploy/direct-production nginx/green-mind.ch.conf nginx/test.green-mind.ch.conf | tar -x -C "$out"
python3 - "$out" "$revision" <<'PY'
import hashlib,json,sys,tarfile
from pathlib import Path
out=Path(sys.argv[1])
with tarfile.open(out/'images.tar.gz') as archive:
    images=json.load(archive.extractfile('manifest.json'))
config_ids={item['RepoTags'][0].split(':')[0]: 'sha256:'+item['Config'].split('/')[-1].removesuffix('.json') for item in images}
hash_value=hashlib.sha256()
with (out/'images.tar.gz').open('rb') as stream:
    for block in iter(lambda:stream.read(1024*1024),b''): hash_value.update(block)
(out/'bundle.json').write_text(json.dumps({'revision':sys.argv[2],'images_sha256':hash_value.hexdigest(),'images':config_ids},indent=2)+'\n')
print('Release bundle built; no server contacted.')
PY
