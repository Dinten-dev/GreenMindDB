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
python3 - "$2" <<'PY'
import json,re,sys
data=json.load(open(sys.argv[1]))
assert set(data)=={'Version','Statement'} and data['Version']=='2012-10-17'
assert len(data['Statement'])==1
rule=data['Statement'][0]
assert set(rule)=={'Effect','Action','Resource'} and rule['Effect']=='Allow'
assert sorted(rule['Action'])==['s3:GetBucketVersioning','s3:GetLifecycleConfiguration']
resources=rule['Resource']
assert len(resources)==2 and 'arn:aws:s3:::greenmind-raw' in resources
direct=next(value for value in resources if value!='arn:aws:s3:::greenmind-raw')
assert re.fullmatch(r'arn:aws:s3:::greenmind-direct-production[a-z0-9.-]*',direct)
PY
# Do not enable shell tracing: user-add needs credentials as tool arguments.
mc admin policy create "$1" greenmind-bucket-diagnostic "$2"
mc admin user add "$1" "$GREENMIND_DIAGNOSTIC_ACCESS" "$GREENMIND_DIAGNOSTIC_SECRET"
mc admin policy attach "$1" greenmind-bucket-diagnostic --user "$GREENMIND_DIAGNOSTIC_ACCESS"
# No GetObject, PutObject, DeleteObject, ListBucket or administrative permissions.
