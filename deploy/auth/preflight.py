"""Read-only Staging inventory. Run manually with sudo; never grants agent sudo."""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def run(args, **kwargs):
    return subprocess.check_output(args, timeout=30, stderr=subprocess.DEVNULL, **kwargs)


def main():
    if os.uname().nodename != "ubuntu-4gb-fsn1-1" or os.geteuid() != 0:
        raise SystemExit("Run with sudo on the archive/Staging host only")
    names = run(["docker", "ps", "--format", "{{.Names}}"]).decode().splitlines()
    containers = json.loads(run(["docker", "inspect", *names]))
    candidates = []
    for item in containers:
        bindings = item["HostConfig"].get("PortBindings") or {}
        ports = [p["HostPort"] for group in bindings.values() if group for p in group]
        if any(p in {"8124", "8125", "8126", "3124", "3009", "8002", "5433"} for p in ports):
            candidates.append(item)
    result = {"checked_at": datetime.now(UTC).isoformat(), "read_only": True, "runtimes": []}
    allowlist = set()
    source_files = ["app/auth.py", "app/routers/auth.py", "app/routers/ws.py", "app/gateway_auth.py", "app/zone_access.py", "app/config.py"]
    probe = "import hashlib,json,pathlib; files=" + repr(source_files) + "; print(json.dumps({p:hashlib.sha256(pathlib.Path('/app',p).read_bytes()).hexdigest() for p in files if pathlib.Path('/app',p).is_file()}))"
    for item in candidates:
        name = item["Name"].lstrip("/")
        env = dict(v.split("=", 1) for v in item["Config"]["Env"] if "=" in v)
        visible = {k: env.get(k) for k in ["ENVIRONMENT", "SERVICE_ROLE", "FRONTEND_URL", "COOKIE_DOMAIN", "COOKIE_SECURE", "CORS_ORIGINS", "MANAGEMENT_ADMIN_EMAILS", "ARCHIVE_REMOTE_AUTH_ENABLED", "RAW_ARCHIVE_AUTH_SOURCE_URL"]}
        if "8124" in [p["HostPort"] for group in (item["HostConfig"].get("PortBindings") or {}).values() if group for p in group]:
            allowlist.update(v.strip().casefold() for v in env.get("MANAGEMENT_ADMIN_EMAILS", "").split(",") if v.strip())
        record = {"name": name, "image": item["Image"], "labels": item["Config"].get("Labels"), "settings": visible, "command": item["Config"].get("Cmd"), "mounts": [{"source": v["Source"], "target": v["Destination"], "read_write": v["RW"]} for v in item.get("Mounts", [])], "jwt_key_fingerprint": hashlib.sha256(env.get("JWT_SECRET_KEY", "").encode()).hexdigest()}
        try:
            record["source_sha256"] = json.loads(run(["docker", "exec", name, "python", "-c", probe]))
        except Exception:
            record["source_sha256"] = "not_python_runtime"
        result["runtimes"].append(record)
    sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SELECT json_build_object('revision',(SELECT version_num FROM alembic_version),
 'admins',(SELECT coalesce(json_agg(json_build_object('id',id,'email',email,'organization_id',organization_id,'active',is_active,'verified',is_verified)),'[]'::json) FROM users WHERE role='admin'),
 'active_legacy_gateways',(SELECT count(*) FROM gateway WHERE is_active AND (api_key_hash LIKE '$2a$%' OR api_key_hash LIKE '$2b$%' OR api_key_hash LIKE '$2y$%' OR api_key_hash LIKE '$bcrypt-sha256$%')),
 'counts',json_build_object('users',(SELECT count(*) FROM users),'gateways',(SELECT count(*) FROM gateway),'submissions',(SELECT count(*) FROM form_submission)));
ROLLBACK;"""
    raw = run(["docker", "exec", "-i", "gm-staging-postgres-1", "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "plantuser", "-d", "plantdb_staging"], input=sql.encode())
    database = json.loads(raw)
    for admin in database["admins"]:
        admin["allowlisted_platform_operator"] = admin["email"].casefold() in allowlist
    database["tenant_admin_candidates"] = [v["id"] for v in database["admins"] if not v["allowlisted_platform_operator"]]
    result["database"] = database
    result["rollout_blocked"] = bool(database["tenant_admin_candidates"])
    filename = Path(__file__).resolve().parent / ("preflight-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as output:
        json.dump(result, output, indent=2)
    print("Read-only report:", filename)
    print("Tenant ADMIN candidates:", len(database["tenant_admin_candidates"]))
    print("Active legacy gateways:", database["active_legacy_gateways"])


if __name__ == "__main__":
    main()
