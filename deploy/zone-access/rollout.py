"""Additive zone access deployment. Blue/green API; never stops existing services.

prepare/start are unprivileged. activate requires the operator's sudo access.
Only explicitly reviewed environment/revision/image IDs are accepted.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import subprocess
import time
import urllib.request

ENVIRONMENTS = {
    "production": (
        "greenminddb",
        "greenmind-prod",
        "green-mind.ch",
        8000,
        8003,
        8120,
        3120,
    ),
    "staging": (
        "gm-staging",
        "greenmind-staging",
        "test.green-mind.ch",
        8001,
        8002,
        8124,
        3124,
    ),
}
GUARD = runpy.run_path(str(Path(__file__).parents[1] / "release/gateway_guard.py"))


def run(args, data=None, timeout=180):
    result = subprocess.run(
        args, input=data, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed: {args[0]} ({result.returncode}); output withheld to protect credentials"
        )
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def env(name):
    return dict(v.split("=", 1) for v in inspect(name)["Config"]["Env"] if "=" in v)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def protected():
    return {
        n: inspect(n)["State"]["StartedAt"]
        for n in run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
        if not n.startswith("gm-zones-")
    }


def unchanged(before):
    for name, started in before.items():
        state = inspect(name)["State"]
        assert state["Running"] and state["StartedAt"] == started, (
            f"Existing service changed: {name}"
        )


def render(original, environment):
    _, _, _, old_api, _, api, front = ENVIRONMENTS[environment]
    GUARD["require_guard"](original, environment)
    # The guard's protection semantics are unchanged. Only its receiving upstream moves.
    candidate = original.replace(
        f"http://127.0.0.1:{old_api}", f"http://127.0.0.1:{api}"
    )
    visual_start = candidate.index("    # Authenticated visualization only;")
    visual_end = candidate.index("    # API → Backend directly", visual_start)
    old_visual = candidate[visual_start:visual_end]
    old_ports = re.findall(r"proxy_pass http://127\.0\.0\.1:(\d+)", old_visual)
    assert old_ports and len(set(old_ports[:3])) == 1
    old_front = re.search(
        r"location / \{\s*proxy_pass http://127\.0\.0\.1:(\d+);", candidate
    ).group(1)
    new_visual = re.sub(
        r"http://127\.0\.0\.1:" + old_ports[0] + r"\b",
        f"http://127.0.0.1:{api + 1}",
        old_visual,
    )
    # Keep the currently served frontend as the previous-assets fallback.
    new_visual = re.sub(
        r"(location \^~ /_next/static/ \{\s*proxy_pass http://127\.0\.0\.1:)\d+",
        lambda m: m.group(1) + str(front),
        new_visual,
    )
    new_visual = re.sub(
        r"(location @visual_previous_assets \{\s*proxy_pass http://127\.0\.0\.1:)\d+",
        lambda m: m.group(1) + old_front,
        new_visual,
    )
    candidate = candidate[:visual_start] + new_visual + candidate[visual_end:]
    candidate = re.sub(
        r"(location / \{\s*proxy_pass http://127\.0\.0\.1:)" + old_front + r"\b",
        lambda m: m.group(1) + str(front),
        candidate,
        count=1,
    )
    # Only browser metadata uses the new Direct app. Chunks, pairing and assembly stay on the existing receiver.
    marker = "    # Independent Direct receiver;"
    assert candidate.count(marker) == 1
    block = f"""    location = /api/v1/direct-ingest/devices {{
        proxy_pass http://127.0.0.1:{api + 2};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 20s;
        add_header Cache-Control "private, no-store" always;
    }}
"""
    candidate = candidate.replace(marker, block + "\n" + marker, 1)
    assert GUARD["block"](environment).replace(f":{old_api}", f":{api}") in candidate
    return candidate


def prepare(args):
    root = args.directory.resolve()
    assert not root.exists(), "Use a fresh release directory"
    prefix, site, domain, _, _, api, front = ENVIRONMENTS[args.environment]
    assert re.fullmatch("[0-9a-f]{40}", args.revision)
    for image in (args.backend_image, args.frontend_image):
        assert re.fullmatch("sha256:[0-9a-f]{64}", image)
        info = inspect(image)
        assert info["Architecture"] == "amd64"
        assert (
            info["Config"]["Labels"]["org.opencontainers.image.revision"]
            == args.revision
        )
    for port in (api, api + 1, api + 2, front):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    root.mkdir(mode=0o700, parents=True)
    before = protected()
    current = Path("/etc/nginx/sites-available") / site
    original = current.read_text()
    (root / "before.conf").write_text(original)
    (root / "candidate.conf").write_text(render(original, args.environment))
    application = env(prefix + "-backend-1")
    application.update(
        SERVICE_ROLE="application",
        EMBEDDED_BACKGROUND_WORKERS_ENABLED="false",
        RELEASE_REVISION=args.revision,
    )
    direct = env("gm-direct-" + args.environment + "-direct-api-1")
    assert direct["DIRECT_ENVIRONMENT"] == args.environment
    # Resolve authorization to the candidate API even before public activation.
    direct["DIRECT_DASHBOARD_API_URL"] = "http://application:8000/api/v1"
    visual = {
        k: v
        for k, v in application.items()
        if k
        in (
            "DATABASE_URL",
            "JWT_SECRET_KEY",
            "COOKIE_DOMAIN",
            "COOKIE_SECURE",
            "CORS_ORIGINS",
            "ENVIRONMENT",
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
            "S3_ENDPOINT",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "S3_REGION",
        )
    }
    visual.update({k: v for k, v in direct.items() if k.startswith("DIRECT_")})
    visual.update(
        SERVICE_ROLE="visualization",
        VISUAL_DIRECT_ENABLED="true",
        DIRECT_INGEST_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
        VISUAL_ARCHIVE_DIR="/archive",
        RELEASE_REVISION=args.revision,
        RELEASE_ID="gm-zones-" + args.environment + "-" + args.revision[:12],
        OPENBLAS_NUM_THREADS="1",
    )
    for name, values in (
        ("application", application),
        ("direct", direct),
        ("visual", visual),
    ):
        assert all("\n" not in str(v) and "\r" not in str(v) for v in values.values())
        path = root / (name + ".env")
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        path.chmod(0o600)
    project = "gm-zones-" + args.environment + "-" + args.revision[:12]
    common = {
        "image": args.backend_image,
        "pull_policy": "never",
        "restart": "unless-stopped",
        "read_only": True,
        "tmpfs": ["/tmp:size=64m,mode=1777"],
        "networks": ["existing"],
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "pids_limit": 96,
        "mem_limit": "256m",
        "memswap_limit": "384m",
        "cpus": 0.5,
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "5m", "max-file": "2"},
        },
    }
    services = {}
    for name, module, port in (
        ("application", "app.main:app", api),
        ("visual", "app.visualization.api:app", api + 1),
        ("direct", "app.direct.main:app", api + 2),
    ):
        services[name] = common | {
            "env_file": [{"path": "./" + name + ".env", "format": "raw"}],
            "ports": [f"127.0.0.1:{port}:8000"],
            "command": [
                "uvicorn",
                module,
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--workers",
                "1",
                "--proxy-headers",
                "--forwarded-allow-ips",
                ("172.28.20.1" if args.environment == "production" else "172.28.21.1"),
                "--no-access-log",
            ],
        }
    services["application"]["mem_limit"] = "384m"
    services["application"]["memswap_limit"] = "512m"
    services["application"]["cpus"] = 1
    services["application"]["volumes"] = [
        {
            "type": "bind",
            "source": m["Source"],
            "target": m["Destination"],
            "read_only": not m["RW"],
        }
        for m in inspect(prefix + "-backend-1")["Mounts"]
    ]
    services["visual"]["volumes"] = [
        f"/home/traver/greenmind-release-{args.environment}/archive:/archive:ro"
    ]
    services["direct"]["extra_hosts"] = [
        domain
        + ":"
        + ("172.28.20.1" if args.environment == "production" else "172.28.21.1")
    ]
    services["frontend"] = common | {
        "image": args.frontend_image,
        "ports": [f"127.0.0.1:{front}:3000"],
        "environment": {"INTERNAL_API_URL": "http://application:8000"},
        "tmpfs": common["tmpfs"] + ["/app/.next/cache:size=64m,mode=1777"],
        "mem_limit": "192m",
    }
    save(
        root / "compose.json",
        {
            "name": project,
            "services": services,
            "networks": {"existing": {"external": True, "name": prefix + "_default"}},
        },
    )
    save(
        root / "manifest.json",
        {
            "environment": args.environment,
            "revision": args.revision,
            "project": project,
            "target": str(current),
            "protected": before,
            "before_sha256": sha(root / "before.conf"),
            "candidate_sha256": sha(root / "candidate.conf"),
            "backend_image": args.backend_image,
            "frontend_image": args.frontend_image,
            "api_port": api,
            "frontend_port": front,
        },
    )
    unchanged(before)
    print("Prepared; database and public proxy unchanged.")


def compose(root, *args):
    return run(
        ["docker", "compose", "-f", str(root / "compose.json"), *args], timeout=240
    )


def start(root):
    m = json.loads((root / "manifest.json").read_text())
    assert sha(m["target"]) == m["before_sha256"]
    # The schema is already at 0022; upgrade only the additive, reviewed migration.
    check = "from app.database import engine; from sqlalchemy import text\nwith engine.connect() as c:\n assert c.execute(text('select version_num from alembic_version')).scalar() in ('0022','0023')"
    compose(root, "run", "--rm", "--no-deps", "application", "python", "-c", check)
    backup = """from app.database import engine
from sqlalchemy import text
import json
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 c.execute(text("SET LOCAL statement_timeout='5s'"))
 result={"revision":c.execute(text('select version_num from alembic_version')).scalar(),
 "users":[dict(r) for r in c.execute(text('select id,organization_id,role from users')).mappings()],
 "zones":[dict(r) for r in c.execute(text('select id,organization_id from zone')).mappings()]}
 print(json.dumps(result,default=str))
"""
    (root / "authorization-before.json").write_text(
        compose(root, "run", "--rm", "--no-deps", "application", "python", "-c", backup)
    )
    (root / "authorization-before.json").chmod(0o600)
    compose(
        root, "run", "--rm", "--no-deps", "application", "alembic", "upgrade", "0023"
    )
    compose(
        root, "up", "-d", "--no-deps", "application", "visual", "direct", "frontend"
    )
    for port, path in (
        (m["api_port"], "/health"),
        (m["api_port"] + 1, "/health"),
        (m["api_port"] + 2, "/health"),
        (m["frontend_port"], "/de"),
    ):
        for attempt in range(30):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=5
                ) as response:
                    assert response.status == 200
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(1)
    unchanged(m["protected"])
    save(
        root / "standby.json",
        {"passed": True, "at": time.time(), "revision": m["revision"]},
    )
    print("Standby healthy; public proxy unchanged.")


def activate(root):
    assert os.geteuid() == 0, "Final activation requires sudo"
    m = json.loads((root / "manifest.json").read_text())
    assert sha(m["target"]) == m["before_sha256"], "Proxy changed after review"
    assert sha(root / "candidate.conf") == m["candidate_sha256"]
    acceptance = json.loads((root / "acceptance.json").read_text())
    assert acceptance["passed"] and acceptance["revision"] == m["revision"]
    assert time.time() - acceptance["at"] < 3600
    unchanged(m["protected"])
    target = Path(m["target"])
    pending = target.with_suffix(".zone-pending")
    pending.write_bytes((root / "candidate.conf").read_bytes())
    pending.chmod(0o644)
    os.replace(pending, target)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except BaseException:
        target.write_bytes((root / "before.conf").read_bytes())
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
        raise
    unchanged(m["protected"])
    save(
        root / "activated.json",
        {"at": time.time(), "revision": m["revision"], "receivers_not_restarted": True},
    )
    print("Activated; existing containers and Raspberry Pis were not restarted.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["prepare", "start", "activate"])
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--environment", choices=ENVIRONMENTS)
    p.add_argument("--revision")
    p.add_argument("--backend-image")
    p.add_argument("--frontend-image")
    a = p.parse_args()
    if a.action == "prepare":
        prepare(a)
    elif a.action == "start":
        start(a.directory)
    else:
        activate(a.directory)
