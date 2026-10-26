#!/usr/bin/env bash
# Use ONLY an admin alias explicitly configured by the authorized operator.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: provision-diagnostic.sh EXPLICIT_ADMIN_ALIAS PRIVATE_POLICY_FILE' >&2
  exit 2
fi
: "${GREENMIND_DIAGNOSTIC_ACCESS:?Supply a NEW dedicated diagnostic username}"
: "${GREENMIND_DIAGNOSTIC_SECRET:?Supply its NEW private secret}"
: "${GREENMIND_MINIO_CLIENT:?Supply the absolute path to the verified MinIO client, not Midnight Commander}"
: "${GREENMIND_DIAGNOSTIC_DIRECT_BUCKET:?Supply the exact reviewed production Direct bucket}"
if [[ "$GREENMIND_MINIO_CLIENT" != /* || ! -x "$GREENMIND_MINIO_CLIENT" ]]; then
  echo 'An absolute executable MinIO client path is required' >&2
  exit 2
fi
client_version=$("$GREENMIND_MINIO_CLIENT" --version)
if [[ "$client_version" != mc\ version\ RELEASE.* ]]; then
  echo 'The configured executable is not the MinIO client' >&2
  exit 2
fi
if [[ ! "$GREENMIND_DIAGNOSTIC_ACCESS" =~ ^greenmind-diagnostic-[a-zA-Z0-9_-]+$ || ! "$1" =~ ^[a-zA-Z0-9_-]+$ || ! -f "$2" || -L "$2" ]]; then
  echo 'Invalid diagnostic identity, operator alias or policy file' >&2
  exit 2
fi
python3 - "$2" <<'PY'
import json,os,re,sys
data=json.load(open(sys.argv[1]))
assert set(data)=={'Version','Statement'} and data['Version']=='2012-10-17'
assert len(data['Statement'])==1
rule=data['Statement'][0]
assert set(rule)=={'Effect','Action','Resource'} and rule['Effect']=='Allow'
assert sorted(rule['Action'])==['s3:GetBucketVersioning','s3:GetLifecycleConfiguration']
resources=rule['Resource']
assert len(resources)==2 and 'arn:aws:s3:::greenmind-raw' in resources
direct=next(value for value in resources if value!='arn:aws:s3:::greenmind-raw')
expected=os.environ['GREENMIND_DIAGNOSTIC_DIRECT_BUCKET']
assert re.fullmatch(r'greenmind-direct-production[a-z0-9.-]*',expected)
assert direct=='arn:aws:s3:::'+expected
PY
# Existing users must never have their unknown passwords replaced. A dedicated
# policy name also avoids overwriting a policy used by another identity.
diagnostic_policy="${GREENMIND_DIAGNOSTIC_ACCESS}-metadata"
python3 - "$1" "$diagnostic_policy" <<'PY'
import json,os,subprocess,sys
def codes(value):
    if isinstance(value,dict):
        result={value['Code']} if isinstance(value.get('Code'),str) else set()
        for child in value.values(): result.update(codes(child))
        return result
    if isinstance(value,list):
        return set().union(*(codes(child) for child in value))
    return set()
for kind,name,missing in (
    ('user',os.environ['GREENMIND_DIAGNOSTIC_ACCESS'],'XMinioAdminNoSuchUser'),
    ('policy',sys.argv[2],'XMinioAdminNoSuchPolicy'),
):
    try:
        result=subprocess.run([os.environ['GREENMIND_MINIO_CLIENT'],'--json','admin',kind,'info',sys.argv[1],name],capture_output=True,text=True,timeout=10)
        # A successful lookup means the identity/policy already exists. A denied
        # lookup is not evidence of absence. Withhold potentially sensitive text.
        value=json.loads(result.stdout)
        absent=result.returncode!=0 and value.get('status')=='error' and codes(value)=={missing}
    except (ValueError,subprocess.TimeoutExpired): absent=False
    if not absent:
        raise SystemExit('Diagnostic '+kind+' is not proven absent; no administrative changes made')
PY
# Do not enable shell tracing: user-add needs credentials as tool arguments.
"$GREENMIND_MINIO_CLIENT" admin policy create "$1" "$diagnostic_policy" "$2"
"$GREENMIND_MINIO_CLIENT" admin user add "$1" "$GREENMIND_DIAGNOSTIC_ACCESS" "$GREENMIND_DIAGNOSTIC_SECRET"
"$GREENMIND_MINIO_CLIENT" admin policy attach "$1" "$diagnostic_policy" --user "$GREENMIND_DIAGNOSTIC_ACCESS"
# No GetObject, PutObject, DeleteObject, ListBucket or administrative permissions.
