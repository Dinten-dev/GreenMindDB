"""Prepare only the named Staging database/bucket. Run as the Docker operator.

Secrets are generated on the host, kept in private/ (0600), and never printed.
No production service, database, bucket, volume or proxy is changed here.
"""

import json
import os
import secrets
import subprocess
from pathlib import Path

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
        "s3_access_key": "gmdstage" + secrets.token_hex(8),
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
            "Staging preparation failed; see private/prepare-error.log (credentials redacted)."
        )
    return result.stdout.strip()


def sql(statement, database="postgres"):
    return execute(
        [
            "docker",
            "exec",
            "-i",
            "gm-staging-postgres-1",
            "sh",
            "-c",
            'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -At',
            "sh",
            database,
        ],
        statement,
    )


role, database = "greenmind_direct_staging", "greenmind_direct_staging"
if sql("SELECT 1 FROM pg_roles WHERE rolname='greenmind_direct_staging'") != "1":
    sql(
        f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{credentials['database_password']}';"
    )
if sql("SELECT 1 FROM pg_database WHERE datname='greenmind_direct_staging'") != "1":
    sql(f"CREATE DATABASE {database} OWNER {role};")
sql(
    f"REVOKE ALL ON DATABASE {database} FROM PUBLIC; GRANT CONNECT ON DATABASE {database} TO {role};"
)
sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", database)

bucket = "greenmind-direct-staging-hotspot"
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
        "gm-staging-minio-1",
        "sh",
        "-c",
        "umask 077; cat > /tmp/gm-direct-policy.json",
    ],
    json.dumps(policy),
)
# Dedicated temporary mc config; root credentials are expanded only in MinIO.
setup = f"""set -eu
export MC_CONFIG_DIR=/tmp/gm-direct-mc-config
umask 077
trap 'rm -rf /tmp/gm-direct-mc-config; rm -f /tmp/gm-direct-policy.json' EXIT
mc alias set setup http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing setup/{bucket} >/dev/null
mc quota set setup/{bucket} --size 1GiB >/dev/null
mc admin user add setup {credentials["s3_access_key"]} {credentials["s3_secret_key"]} >/dev/null
mc admin policy create setup gm-direct-staging /tmp/gm-direct-policy.json >/dev/null
mc admin policy attach setup gm-direct-staging --user {credentials["s3_access_key"]} >/dev/null
"""
execute(["docker", "exec", "-i", "gm-staging-minio-1", "sh"], setup)
environment = {
    "DIRECT_ENVIRONMENT": "staging",
    "DIRECT_INGEST_ENABLED": "true",
    "DIRECT_DATABASE_URL": f"postgresql+psycopg2://{role}:{credentials['database_password']}@postgres:5432/{database}",
    "DIRECT_STORAGE": "s3",
    "DIRECT_S3_ENDPOINT_URL": "https://test.green-mind.ch:9443",
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
    "DIRECT_DASHBOARD_ORIGIN": "https://test.green-mind.ch",
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
    "Dedicated Staging database, bucket, restricted credentials and 1-GiB bucket quota prepared."
)
