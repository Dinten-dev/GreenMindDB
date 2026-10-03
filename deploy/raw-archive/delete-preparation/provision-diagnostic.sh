#!/usr/bin/env bash
# Use ONLY an admin alias explicitly configured by the authorized operator.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: provision-diagnostic.sh EXPLICIT_ADMIN_ALIAS PRIVATE_POLICY_FILE' >&2
  exit 2
fi
: "${GREENMIND_DIAGNOSTIC_ACCESS:?Supply a NEW dedicated diagnostic username}"
: "${GREENMIND_DIAGNOSTIC_SECRET:?Supply its NEW private secret}"
[[ "$GREENMIND_DIAGNOSTIC_ACCESS" =~ ^greenmind-diagnostic-[a-zA-Z0-9_-]+$ ]]
[[ "$1" =~ ^[a-zA-Z0-9_-]+$ ]]
[[ -f "$2" && ! -L "$2" ]]
if rg -q 'FILL_' "$2"; then
  echo 'Fill the exact bucket in the reviewed policy first.' >&2
  exit 2
fi
# Do not enable shell tracing: user-add needs credentials as tool arguments.
mc admin policy create "$1" greenmind-bucket-diagnostic "$2"
mc admin user add "$1" "$GREENMIND_DIAGNOSTIC_ACCESS" "$GREENMIND_DIAGNOSTIC_SECRET"
mc admin policy attach "$1" greenmind-bucket-diagnostic --user "$GREENMIND_DIAGNOSTIC_ACCESS"
# No GetObject, PutObject, DeleteObject, ListBucket or administrative permissions.
