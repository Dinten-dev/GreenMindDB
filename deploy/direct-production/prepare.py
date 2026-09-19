"""Prepare only the named Production database/bucket. Run as the Docker operator.

Secrets are generated on the host, kept in private/ (0600), and never printed.
Only the dedicated Direct database/account/bucket are provisioned.
Legacy tables, credentials, services and proxy remain unchanged.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

if sys.argv[1:2] != ["--prepare-production"]:
    raise SystemExit(
        "Explicit preparation required: python3 prepare.py --prepare-production"
    )

parser = argparse.ArgumentParser()
parser.add_argument("--prepare-production", action="store_true")
parser.add_argument("--bucket-quota-gib", type=int, required=True)
args = parser.parse_args()
if not 1 <= args.bucket_quota_gib <= 100000:
    raise SystemExit("Review a positive, bounded Production bucket quota")

BASE = Path(__file__).resolve().parent
os.umask(0o077)
private = BASE / "private"
private.mkdir(mode=0o700, exist_ok=True)
secret_file = private / "credentials.json"
if secret_file.exists():
    credentials = json.loads(secret_file.read_text())
else:
    credentials = {
        "database_password": secrets.token_hex(32),
        "s3_access_key": "gmdprod" + secrets.token_hex(8),
        "s3_secret_key": secrets.token_hex(32),
    }
    with secret_file.open("x") as output:
        json.dump(credentials, output)
        output.flush()
        os.fsync(output.fileno())


def execute(command, data=None):
    result = subprocess.run(
        command, input=data, text=True, capture_output=True, check=False
    )
    if result.returncode:
        diagnostic = result.stderr
        for value in credentials.values():
            diagnostic = diagnostic.replace(value, "[REDACTED]")
        (private / "prepare-error.log").write_text(diagnostic)
        raise SystemExit(
            "Production preparation failed; see private/prepare-error.log (credentials redacted)."
        )
    return result.stdout.strip()


def sql(statement, database="postgres"):
    return execute(
        [
            "docker",
            "exec",
            "-i",
            "greenminddb-postgres-1",
            "sh",
            "-c",
            'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -At',
            "sh",
            database,
        ],
        statement,
    )


role, database = "greenmind_direct_production", "greenmind_direct_production"
if sql("SELECT 1 FROM pg_roles WHERE rolname='greenmind_direct_production'") != "1":
    sql(
        f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{credentials['database_password']}';"
    )
if sql("SELECT 1 FROM pg_database WHERE datname='greenmind_direct_production'") != "1":
    sql(f"CREATE DATABASE {database} OWNER {role};")
sql(
    f"REVOKE ALL ON DATABASE {database} FROM PUBLIC; GRANT CONNECT ON DATABASE {database} TO {role};"
)
sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", database)

bucket = "greenmind-direct-production-hotspot"
policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:GetBucketLocation"],
            "Resource": [f"arn:aws:s3:::{bucket}"],
        },
        {
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{bucket}"],
            "Condition": {"StringLike": {"s3:prefix": ["direct/*"]}},
        },
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/direct/*"],
        },
    ],
}
execute(
    [
        "docker",
        "exec",
        "-i",
        "greenminddb-minio-1",
        "sh",
        "-c",
        "umask 077; cat > /tmp/gm-direct-production-policy.json",
    ],
    json.dumps(policy),
)
# Dedicated temporary mc config; root credentials are expanded only in MinIO.
setup = f"""set -eu
export MC_CONFIG_DIR=/tmp/gm-direct-production-mc-config
umask 077
trap 'rm -rf /tmp/gm-direct-production-mc-config; rm -f /tmp/gm-direct-production-policy.json' EXIT
mc alias set setup http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing setup/{bucket} >/dev/null
mc quota set setup/{bucket} --size {args.bucket_quota_gib}GiB >/dev/null
mc admin user add setup {credentials["s3_access_key"]} {credentials["s3_secret_key"]} >/dev/null
mc admin policy create setup gm-direct-production /tmp/gm-direct-production-policy.json >/dev/null
mc admin policy attach setup gm-direct-production --user {credentials["s3_access_key"]} >/dev/null
"""
execute(["docker", "exec", "-i", "greenminddb-minio-1", "sh"], setup)
environment = {
    "DIRECT_ENVIRONMENT": "production",
    "DIRECT_INGEST_ENABLED": "true",
    "DIRECT_DATABASE_URL": f"postgresql+psycopg2://{role}:{credentials['database_password']}@postgres:5432/{database}",
    "DIRECT_STORAGE": "s3",
    "DIRECT_S3_ENDPOINT_URL": "https://green-mind.ch:9444",
    "DIRECT_S3_BUCKET": bucket,
    "DIRECT_S3_ACCESS_KEY": credentials["s3_access_key"],
    "DIRECT_S3_SECRET_KEY": credentials["s3_secret_key"],
    "DIRECT_MAX_SPOOL_BYTES": "67108864",
    "DIRECT_MAX_DEVICE_SPOOL_BYTES": "16777216",
    "DIRECT_MAX_SEGMENT_BYTES": "2097152",
    "DIRECT_IDLE_SECONDS": "30",
    "DIRECT_RETENTION_ENABLED": "false",
    "DIRECT_RETENTION_DRY_RUN": "true",
    "DIRECT_DASHBOARD_API_URL": "http://backend:8000/api/v1",
    "DIRECT_DASHBOARD_ORIGIN": "https://green-mind.ch",
    "ENVIRONMENT": "development",
    "DATABASE_URL": "postgresql+psycopg2://unused:unused@127.0.0.1:1/unused",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
(private / "direct.env").write_text(
    "".join(f"{k}={v}\n" for k, v in environment.items())
)
print(
    f"Dedicated Production resources prepared with reviewed {args.bucket_quota_gib}-GiB bucket quota."
)
