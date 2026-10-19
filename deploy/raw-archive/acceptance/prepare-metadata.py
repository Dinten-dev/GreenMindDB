"""Provision one column-limited metadata reader; no source data or admin secrets copied."""

import json
import os
import pwd
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.raw_archive.wav_catalog import COLUMNS

RUNTIME = Path(__file__).resolve().parents[3]
ROOT = Path("/home/traver/greenmind-archive-acceptance-20261007")


def sql(database, statement):
    assert database.replace("_", "").isalnum()
    result = subprocess.run(["docker", "exec", "-i", "greenminddb-postgres-1", "sh", "-c", 'exec psql -X -q -At -U "$POSTGRES_USER" -d ' + database + ' -v ON_ERROR_STOP=1'], input=statement, text=True, capture_output=True, timeout=12)
    if result.returncode:
        raise RuntimeError("Metadata role operation failed; private SQL output withheld")
    return result.stdout.strip()


def main():
    assert os.geteuid() == 0
    available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")) / 1024
    if available < 512 or os.getloadavg()[0] > 2.4:
        print(json.dumps({"status": "paused", "available_mib": round(available, 1), "roles_created": [], "deleted_files": 0}))
        return 75
    revision = json.loads((RUNTIME / "bundle.json").read_text())["revision"]
    role = "greenmind_wav_metadata_" + revision[:12]
    source = dict(line.split("=", 1) for line in Path("/home/traver/greenmind-archive-production/33fe395/read.env").read_text().splitlines() if "=" in line)
    databases = {kind: urlsplit(source[key]).path.lstrip("/") for kind, key in (("gateway", "DATABASE_URL"), ("direct", "DIRECT_DATABASE_URL"))}
    for database in databases.values():
        assert database in {"plantdb", "greenmind_direct_production"}
    if sql(databases["gateway"], "SELECT 1 FROM pg_roles WHERE rolname='" + role + "';"):
        raise RuntimeError("Existing metadata identity must never be reset")
    operator = pwd.getpwnam("traver")
    folder = ROOT / role
    folder.mkdir(mode=0o700)
    os.chown(folder, operator.pw_uid, operator.pw_gid)
    secret = secrets.token_hex(32)
    values = {"RAW_ARCHIVE_" + kind.upper() + "_DATABASE_URL": "postgresql://" + role + ":" + secret + "@127.0.0.1:19032/" + database for kind, database in databases.items()}
    values["RAW_ARCHIVE_DIRECT_S3_BUCKET"] = "greenmind-direct-production-hotspot"
    credential = folder / "metadata.env"
    with credential.open("x") as body:
        body.write("".join(name + "=" + value + "\n" for name, value in values.items()))
    os.chown(credential, operator.pw_uid, operator.pw_gid)
    credential.chmod(0o600)
    report = {"role": role, "configuration": str(credential), "tables": {kind: list(tables) for kind, tables in COLUMNS.items()}, "deleted_files": 0, "status": "prepared"}
    try:
        for number, (kind, database) in enumerate(databases.items()):
            statements = ["BEGIN; SET LOCAL lock_timeout='150ms'; SET LOCAL statement_timeout='8s';"]
            if number == 0:
                statements += [f"CREATE ROLE {role} LOGIN PASSWORD '{secret}' CONNECTION LIMIT 2 NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;", f"ALTER ROLE {role} SET default_transaction_read_only=on;", f"ALTER ROLE {role} SET statement_timeout='8s';", f"ALTER ROLE {role} SET lock_timeout='150ms';"]
            statements += [f"GRANT CONNECT ON DATABASE {database} TO {role};", f"GRANT USAGE ON SCHEMA public TO {role};"]
            for table, columns in COLUMNS[kind].items():
                statements.append('GRANT SELECT (' + ','.join('"' + name + '"' for name in columns) + ') ON "' + table + '" TO ' + role + ';')
            statements.append("COMMIT;")
            sql(database, "\n".join(statements))
        report["status"] = "provisioned"
        proof = sql(databases["gateway"], f"SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,rolconnlimit FROM pg_roles WHERE rolname='{role}';")
        assert proof == "f|f|f|f|f|2"
        report["administrative_rights"] = False
        return 0
    finally:
        proof_file = folder / "provision.json"
        proof_file.write_text(json.dumps(report, indent=2) + "\n")
        proof_file.chmod(0o600)
        os.chown(proof_file, operator.pw_uid, operator.pw_gid)
        print(json.dumps(report))


if __name__ == "__main__":
    raise SystemExit(main())
