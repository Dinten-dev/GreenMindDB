"""Isolated, immutable read-service releases. Never starts/stops an ingestion service.

Run prepare/start/verify as the Docker operator; activate/rollback require sudo.
The installed nginx configuration is the source of truth, guarded by its hash.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import signal
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = {
    "staging": ("gm-staging", "test.green-mind.ch", "172.28.21.1", 8005, 3005, 3001),
    "production": ("greenminddb", "green-mind.ch", "172.28.20.1", 8004, 3004, 3000),
}
KEEP = (
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "S3_ENDPOINT",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_REGION",
    "COOKIE_DOMAIN",
    "SENSOR_EXPORT_MAX_ROWS",
    "SENSOR_EXPORT_MAX_BYTES",
    "SENSOR_EXPORT_MAX_KINDS",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args, data=None):
    result = subprocess.run(
        list(args), input=data, text=True, capture_output=True, timeout=180
    )
    # Subprocess errors can contain inherited credentials. Never echo them.
    require(
        result.returncode == 0, f"Command failed: {args[0]} (exit {result.returncode})"
    )
    return result.stdout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def inspect(name):
    return json.loads(run("docker", "inspect", name))[0]


def protected():
    names = run("docker", "ps", "--format", "{{.Names}}").splitlines()
    return {
        n: inspect(n)["State"]["StartedAt"]
        for n in names
        if not re.match(r"gm-(visual|release)-(staging|production)-", n)
    }


def unchanged(before):
    for name, started in before.items():
        state = inspect(name)["State"]
        require(
            state["Running"] and state["StartedAt"] == started,
            f"Existing service changed: {name}",
        )


@contextmanager
def locked(root):
    with (root.parent / ".release.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def compose(root, *args):
    return run("docker", "compose", "-f", str(root / "compose.json"), *args)


def environment(metadata):
    return dict(line.split("=", 1) for line in metadata["Config"]["Env"] if "=" in line)


def env_text(values):
    require(
        all("\n" not in str(v) and "\r" not in str(v) for v in values.values()),
        "Invalid environment value",
    )
    return "".join(f"{k}={v}\n" for k, v in values.items())


def proposal(original, env, api, front):
    _, _, _, base_api, base_front, legacy_front = CONFIG[env]
    # Only our exact old generated block may be replaced. All ingestion locations stay byte-identical.
    start = original.find("    # Authenticated visualization only;")
    marker = "    # API → Backend directly"
    require(original.count(marker) == 1, "Unsupported nginx template; review manually")
    if start >= 0:
        original = original[:start] + original[original.index(marker, start) :]
    require(
        "location ^~ /api/v1/visualization/" not in original,
        "Unmanaged visualization route",
    )
    index = original.index("    # Frontend (Next.js")
    prefix, tail = original[:index], original[index:]
    candidates = re.findall(r"proxy_pass http://127\.0\.0\.1:(\d+);", tail)
    require(
        len(candidates) >= 1
        and (
            int(candidates[0]) == legacy_front
            or int(candidates[0]) in range(base_front, base_front + 40, 2)
        ),
        "Unexpected frontend route",
    )
    previous = int(candidates[0])
    tail = tail.replace(
        f"proxy_pass http://127.0.0.1:{previous};",
        f"proxy_pass http://127.0.0.1:{front};",
        1,
    )
    headers = """        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_connect_timeout 3s;
        proxy_read_timeout 30s;
        proxy_redirect off;
"""
    block = "    # Authenticated visualization only; all ingestion remains on the old API.\n"
    for location, destination in (
        ("= /visualization-health", f"{api}/health"),
        ("^~ /api/v1/visualization/", str(api)),
        ("~ ^/api/v1/sensors/[0-9a-fA-F-]+/(data|export)$", str(api)),
    ):
        block += f"    location {location} {{\n        proxy_pass http://127.0.0.1:{destination};\n{headers}    }}\n"
    block += f"""    location ^~ /_next/static/ {{
        proxy_pass http://127.0.0.1:{front};
        proxy_intercept_errors on;
        error_page 404 = @visual_previous_assets;
{headers}    }}
    location @visual_previous_assets {{
        proxy_pass http://127.0.0.1:{previous};
{headers}    }}

"""
    return (prefix + tail).replace(marker, block + marker), previous


def make_compose(manifest, values, uid, gid):
    env = manifest["environment"]
    prefix, domain, bridge, *_ = CONFIG[env]
    common = {
        "image": manifest["backend_image"],
        "pull_policy": "never",
        "platform": "linux/amd64",
        "restart": "unless-stopped",
        "user": f"{uid}:{gid}",
        "env_file": [{"path": "./runtime.env", "format": "raw"}],
        "networks": ["existing"],
        "extra_hosts": [f"{domain}:{bridge}"],
        "read_only": True,
        "tmpfs": ["/tmp:size=32m,mode=1777"],
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "pids_limit": 64,
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "5m", "max-file": "2"},
        },
        "mem_limit": "256m",
        "memswap_limit": "512m",
        "cpus": 0.2,
    }
    services = {
        "api": common
        | {
            "command": [
                "uvicorn",
                "app.visualization.api:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--workers",
                "1",
                "--limit-concurrency",
                "12",
                "--no-access-log",
                "--proxy-headers",
                "--forwarded-allow-ips",
                bridge + ",127.0.0.1",
            ],
            "ports": [f"127.0.0.1:{manifest['api_port']}:8000"],
            "cpus": 0.3,
        },
        "frontend": {
            k: v
            for k, v in common.items()
            if k not in ("env_file", "user", "extra_hosts")
        },
        # No --prune flag. This release path cannot remove historical SQL chunks.
        "worker": common
        | {
            "profiles": ["workers"],
            "command": ["python", "-m", "app.visualization.worker", "run"],
            "volumes": [
                manifest.get(
                    "archive_directory",
                    str(Path(manifest["directory"]).parent / "archive"),
                )
                + ":/archive"
            ],
        },
        "direct-worker": common
        | {
            "profiles": ["workers"],
            "command": ["python", "-m", "app.visualization.direct", "run"],
        },
    }
    services["frontend"]["tmpfs"] = [
        *common["tmpfs"],
        "/app/.next/cache:size=64m,mode=1777",
    ]
    services["frontend"].update(
        image=manifest["frontend_image"],
        ports=[f"127.0.0.1:{manifest['frontend_port']}:3000"],
        mem_limit="192m",
        cpus=0.25,
    )
    return {
        "name": manifest["project"],
        "services": services,
        "networks": {"existing": {"external": True, "name": prefix + "_default"}},
    }


def prepare(args):
    root = args.directory.resolve()
    require(not root.exists(), "Use a fresh release directory")
    require(
        re.fullmatch(r"[0-9a-f]{40}", args.revision),
        "An exact source commit is required",
    )
    prefix, domain, bridge, api, front, _ = CONFIG[args.environment]
    target = Path(
        "/etc/nginx/sites-available/"
        + (
            "greenmind-prod"
            if args.environment == "production"
            else "greenmind-staging"
        )
    )
    original = target.read_text()
    # Retain old frontends for browser assets; use a fresh bounded port pair.
    for offset in range(0, 40, 2):
        sockets = []
        try:
            for port in (api + offset, front + offset):
                connection = socket.socket()
                sockets.append(connection)
                connection.bind(("127.0.0.1", port))
            api, front = api + offset, front + offset
            break
        except OSError:
            if offset == 38:
                raise RuntimeError(
                    "No free release ports; retire an unused old read release"
                )
        finally:
            for connection in sockets:
                connection.close()
    before = protected()
    require(prefix + "-backend-1" in before, "Existing receiver must be running")
    legacy = environment(inspect(prefix + "-backend-1"))
    direct_name = "gm-direct-" + args.environment + "-direct-api-1"
    require(
        direct_name in before,
        "First prepare/activate the isolated Direct receiver using deploy/direct-production (or direct-staging)",
    )
    direct = environment(inspect(direct_name))
    require(
        direct.get("DIRECT_ENVIRONMENT") == args.environment,
        "Cross-environment Direct configuration",
    )
    require(
        "/greenmind_direct_" + args.environment
        in direct.get("DIRECT_DATABASE_URL", ""),
        "Wrong Direct database",
    )
    require(
        direct.get("DIRECT_S3_BUCKET", "").startswith(
            "greenmind-direct-" + args.environment + "-"
        ),
        "Wrong Direct bucket",
    )
    require(
        direct.get("DIRECT_DASHBOARD_ORIGIN") == "https://" + domain,
        "Wrong pairing origin",
    )
    values = {k: legacy[k] for k in KEEP if k in legacy}
    values.update({k: v for k, v in direct.items() if k.startswith("DIRECT_")})
    values.update(
        ENVIRONMENT=args.environment,
        RELEASE_REVISION=args.revision,
        SERVICE_ROLE="visualization",
        COOKIE_SECURE="true",
        FRONTEND_URL="https://" + domain,
        CORS_ORIGINS="https://" + domain,
        VISUAL_DIRECT_ENABLED="true",
        VISUAL_DIRECT_FINE_DAYS="1",
        DIRECT_INGEST_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
        VISUAL_ARCHIVE_DIR="/archive",
        VISUAL_MAX_HOST_LOAD="2.4",
        VISUAL_BATCH_PAUSE="0.1",
        VISUAL_WAV_PAUSE="0.5",
        VISUAL_CYCLE_PAUSE="20",
        OPENBLAS_NUM_THREADS="1",
    )
    for ref in (args.backend_image, args.frontend_image):
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", ref),
            "Use an immutable loaded image ID",
        )
        image = json.loads(run("docker", "image", "inspect", ref))[0]
        require(
            image.get("Architecture") == "amd64" and image.get("Os") == "linux",
            "Release requires linux/amd64 images",
        )
        require(
            image["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
            == args.revision,
            "Image/source revision mismatch",
        )
    manifest = dict(
        environment=args.environment,
        revision=args.revision,
        directory=str(root),
        project="gm-release-"
        + args.environment
        + "-"
        + args.revision[:12]
        + "-"
        + hashlib.sha256(str(root).encode()).hexdigest()[:6],
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        api_port=api,
        frontend_port=front,
        target=str(target),
        before_sha256=hashlib.sha256(original.encode()).hexdigest(),
        protected=before,
        created_at=time.time(),
    )
    values["RELEASE_ID"] = manifest["project"]
    updated, previous = proposal(original, args.environment, api, front)
    manifest.update(
        after_sha256=hashlib.sha256(updated.encode()).hexdigest(),
        previous_frontend_port=previous,
        previous_visual="location ^~ /api/v1/visualization/" in original,
    )
    # Preserve existing archive mount paths; manifests contain /archive references.
    archives = set()
    for name in run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines():
        if (
            name.startswith(
                (
                    "gm-visual-" + args.environment + "-",
                    "gm-release-" + args.environment + "-",
                )
            )
            and name.endswith("-worker-1")
            and not name.endswith("-direct-worker-1")
        ):
            archives.update(
                m["Source"]
                for m in inspect(name).get("Mounts", [])
                if m["Destination"] == "/archive"
            )
    require(
        len(archives) <= 1,
        "Conflicting historical archive mounts; review before release",
    )
    archive = Path(next(iter(archives))) if archives else root.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_mounts = inspect(prefix + "-postgres-1")["Mounts"]
    database_path = next(
        m["Source"]
        for m in database_mounts
        if m["Destination"] == "/var/lib/postgresql/data"
    )
    require(
        archive.stat().st_dev != Path(database_path).stat().st_dev,
        "Archive must remain on a separate filesystem from PostgreSQL",
    )
    manifest["archive_directory"] = str(archive)
    root.mkdir(parents=True, mode=0o700)
    (root / "runtime.env").write_text(env_text(values))
    (root / "runtime.env").chmod(0o600)
    (root / "nginx.before.conf").write_text(original)
    (root / "nginx.proposed.conf").write_text(updated)
    write_json(
        root / "compose.json", make_compose(manifest, values, os.getuid(), os.getgid())
    )
    manifest["artifacts"] = {
        name: digest(root / name) for name in ("compose.json", "runtime.env")
    }
    write_json(root / "manifest.json", manifest)
    print("Prepared immutable isolated release. No services or proxy changed.")


def check(root, manifest):
    verify_artifacts(root, manifest)
    worker_started = []
    for service, image in (
        ("api", manifest["backend_image"]),
        ("frontend", manifest["frontend_image"]),
        ("worker", manifest["backend_image"]),
        ("direct-worker", manifest["backend_image"]),
    ):
        container = compose(root, "ps", "-q", service).strip()
        require(bool(container), "Candidate missing: " + service)
        current = inspect(container)
        require(
            current["State"]["Running"]
            and not current["State"]["OOMKilled"]
            and current["Image"] == image,
            "Candidate image/state mismatch: " + service,
        )
        if service in ("worker", "direct-worker"):
            worker_started.append(
                datetime.fromisoformat(
                    current["State"]["StartedAt"].replace("Z", "+00:00")
                ).timestamp()
            )
    unchanged(manifest["protected"])
    result = json.loads(
        compose(
            root,
            "exec",
            "-T",
            "-e",
            "RELEASE_WORKERS_STARTED_AFTER=" + str(max(worker_started)),
            "api",
            "python",
            "-m",
            "app.visualization.release_check",
        )
    )
    require(result["passed"], "Authenticated readiness check failed")
    with urllib.request.urlopen(
        f"http://127.0.0.1:{manifest['frontend_port']}/de/app/sensors", timeout=10
    ) as response:
        require(response.status == 200, "Frontend readiness failed")
    result.update(
        checked_at=time.time(),
        manifest_sha256=digest(root / "manifest.json"),
        existing_services_unchanged=True,
    )
    write_json(root / "validation.json", result)
    return result


def replace_proxy(path, source):
    temporary = path.with_suffix(".release-pending")
    temporary.write_bytes(source.read_bytes())
    temporary.chmod(0o644)
    os.replace(temporary, path)
    run("nginx", "-t")
    run("systemctl", "reload", "nginx")


def verify_artifacts(root, manifest):
    require(
        set(manifest["artifacts"]) == {"compose.json", "runtime.env"},
        "Incomplete prepared configuration",
    )
    for name, expected in manifest["artifacts"].items():
        require(
            name in ("compose.json", "runtime.env") and digest(root / name) == expected,
            "Prepared configuration changed",
        )


def acceptance(path, manifest):
    evidence = json.loads(path.read_text())
    require(
        evidence.get("environment") == "staging"
        and evidence.get("revision") == manifest["revision"],
        "Staging evidence must match this commit",
    )
    require(
        0 <= time.time() - evidence.get("checked_at", 0) < 7 * 86400,
        "Staging evidence expired",
    )
    for image in ("backend_image", "frontend_image"):
        require(
            evidence.get(image) == manifest[image], "Staging image mismatch: " + image
        )
    for key in (
        "gateway_direct_parallel",
        "wav_sample_integrity",
        "interruption_recovery",
        "rollback_rehearsal",
        "ui_review",
    ):
        require(evidence.get(key) is True, "Missing Staging acceptance: " + key)


def activate_release(root, manifest, target, acceptance_path=None):
    require(os.geteuid() == 0, "sudo is required for the reviewed nginx switch")
    require(digest(target) == manifest["before_sha256"], "Active configuration changed")
    if manifest["environment"] == "production":
        require(
            acceptance_path is not None,
            "Production requires exact-commit Staging acceptance evidence",
        )
        acceptance(acceptance_path, manifest)
    check(root, manifest)
    try:
        replace_proxy(target, root / "nginx.proposed.conf")
        domain = CONFIG[manifest["environment"]][1]
        compose(
            root,
            "exec",
            "-T",
            "-e",
            "RELEASE_CHECK_BASE=https://" + domain,
            "api",
            "python",
            "-m",
            "app.visualization.release_check",
        )
        unchanged(manifest["protected"])
    except BaseException:
        replace_proxy(target, root / "nginx.before.conf")
        raise
    write_json(
        root / "activated.json",
        {
            "activated_at": time.time(),
            "manifest_sha256": digest(root / "manifest.json"),
        },
    )


def check_previous(root, manifest):
    old = (root / "nginx.before.conf").read_text()
    if manifest["previous_visual"]:
        found = re.search(
            r"location = /visualization-health\s*\{.*?proxy_pass http://127\.0\.0\.1:(\d+)/health;",
            old,
            re.S,
        )
        require(found is not None, "Previous read API route unknown")
        port = int(found.group(1))
    else:
        port = 8000 if manifest["environment"] == "production" else 8001
    for destination, path in (
        (port, "/health"),
        (manifest["previous_frontend_port"], "/de/app/sensors"),
    ):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{destination}{path}", timeout=10
        ) as response:
            require(
                response.status == 200, "Previous services are not ready for rollback"
            )


def rollback_release(root, manifest, target):
    require(os.geteuid() == 0, "sudo required")
    require(
        digest(target) == manifest["after_sha256"],
        "Active configuration is not this release",
    )
    state = json.loads(
        compose(
            root,
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "app.visualization.release_check",
            "--state",
        )
    )
    require(
        manifest["previous_visual"] or state["compacted_chunks"] == 0,
        "Restore compacted SQL archives before returning to the legacy API",
    )
    unchanged(manifest["protected"])
    check_previous(root, manifest)
    try:
        replace_proxy(target, root / "nginx.before.conf")
        unchanged(manifest["protected"])
    except BaseException:
        replace_proxy(target, root / "nginx.proposed.conf")
        raise
    write_json(root / "rolled-back.json", {"rolled_back_at": time.time()})


def operate(args):
    root = args.directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    require(manifest["directory"] == str(root), "Release was moved; prepare again")
    target = Path(manifest["target"])
    expected = "/etc/nginx/sites-available/" + (
        "greenmind-prod"
        if manifest["environment"] == "production"
        else "greenmind-staging"
    )
    require(str(target) == expected, "Unexpected proxy target")
    require(
        digest(root / "nginx.before.conf") == manifest["before_sha256"]
        and digest(root / "nginx.proposed.conf") == manifest["after_sha256"],
        "Proxy artifacts changed",
    )
    with locked(root):
        verify_artifacts(root, manifest)
        if args.action == "start":
            require(
                digest(target) == manifest["before_sha256"],
                "Active configuration changed",
            )
            unchanged(manifest["protected"])
            compose(root, "config", "--quiet")
            compose(
                root,
                "run",
                "--rm",
                "--no-deps",
                "api",
                "python",
                "-m",
                "app.visualization.worker",
                "init",
            )
            compose(
                root,
                "run",
                "--rm",
                "--no-deps",
                "api",
                "python",
                "-m",
                "app.visualization.direct",
                "init",
            )
            compose(root, "up", "-d", "--no-build", "--no-deps", "api", "frontend")
            unchanged(manifest["protected"])
        elif args.action == "verify":
            print(json.dumps(check(root, manifest)))
        elif args.action == "activate":
            with Path("/run/lock/greenmind-visualization-proxy.lock").open(
                "a"
            ) as proxy_lock:
                fcntl.flock(proxy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                activate_release(root, manifest, target, args.acceptance)
        elif args.action == "rollback":
            with Path("/run/lock/greenmind-visualization-proxy.lock").open(
                "a"
            ) as proxy_lock:
                fcntl.flock(proxy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                rollback_release(root, manifest, target)
        elif args.action == "workers":
            # Worker handover is explicit, recoverable, and never touches an assembler.
            require(
                digest(target) in (manifest["before_sha256"], manifest["after_sha256"]),
                "Active configuration changed",
            )
            names = run("docker", "ps", "--format", "{{.Names}}").splitlines()
            suffixes = (
                "-visual-worker-1",
                "-visual-direct-worker-1",
                "-worker-1",
                "-direct-worker-1",
            )
            old = [
                n
                for n in names
                if n.startswith(
                    (
                        "gm-visual-" + manifest["environment"] + "-",
                        "gm-release-" + manifest["environment"] + "-",
                    )
                )
                and n.endswith(suffixes)
                and not n.startswith(manifest["project"] + "-")
            ]
            try:
                for n in old:
                    run("docker", "stop", "--time", "30", n)
                compose(
                    root,
                    "--profile",
                    "workers",
                    "up",
                    "-d",
                    "--no-build",
                    "--no-deps",
                    "worker",
                    "direct-worker",
                )
                starts = [
                    datetime.fromisoformat(
                        inspect(compose(root, "ps", "-q", name).strip())["State"][
                            "StartedAt"
                        ].replace("Z", "+00:00")
                    ).timestamp()
                    for name in ("worker", "direct-worker")
                ]
                for attempt in range(20):
                    try:
                        compose(
                            root,
                            "exec",
                            "-T",
                            "-e",
                            "RELEASE_WORKERS_STARTED_AFTER=" + str(max(starts)),
                            "api",
                            "python",
                            "-m",
                            "app.visualization.release_check",
                            "--workers-ready",
                        )
                        break
                    except RuntimeError:
                        if attempt == 19:
                            raise
                        time.sleep(2)
                check(root, manifest)
            except BaseException:
                compose(root, "stop", "worker", "direct-worker")
                for n in old:
                    run("docker", "start", n)
                raise
            unchanged(manifest["protected"])


def main():
    os.umask(0o077)

    def interrupted(signum, frame):
        raise InterruptedError("Release interrupted; restoring proxy if necessary")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "start", "workers", "verify", "activate", "rollback"),
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--environment", choices=CONFIG)
    parser.add_argument("--revision")
    parser.add_argument("--backend-image")
    parser.add_argument("--frontend-image")
    parser.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        require(
            all(
                (
                    args.environment,
                    args.revision,
                    args.backend_image,
                    args.frontend_image,
                )
            ),
            "Prepare requires environment, revision and both image IDs",
        )
        prepare(args)
    else:
        operate(args)


if __name__ == "__main__":
    main()
