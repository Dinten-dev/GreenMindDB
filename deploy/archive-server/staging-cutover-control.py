"""Fixed-cohort Staging controls; independent watchdog fences before source resume.

No production container, catalog, WAV deletion, credential extraction or retention.
Traver may invoke this exact root-owned script through a scoped sudo rule. The
separate source-host fence key accepts only target stop/status over forced SSH.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

SOURCE = os.uname().nodename == "Biolingo"
ARCHIVE = os.uname().nodename == "ubuntu-4gb-fsn1-1"
INSTALL = Path("/opt/greenmind/staging-cutover-20261010")
ROOT = Path("/var/lib/greenmind-staging-cutover-20261010")
OLD = Path("/home/traver/greenmind-archive-rollout-20261008/staging-preparation-v2")
PROXY = Path("/etc/nginx/sites-available/greenmind-staging")
STAGE_SHA = "6b0737287aa88ffaa15a26cf5351f78088a9df3198837fdc94c9dc2ae08e3b2c"
PROD_SHA = "f4e7b516852a52152e03685f59ec1b396c5634af269114e5c81e9c5f28e95436"
PORTS = {
    8124: 28124,
    8125: 28125,
    8126: 28126,
    8002: 28002,
    3124: 23124,
    3009: 23009,
    9002: 29002,
}
COUNT_TABLES = {
    "plantdb_staging": ("zone", "sensor", "wav_file"),
    "greenmind_direct_staging": ("direct_device", "direct_revision"),
}
RECEIVERS = [
    "gm-zones-production-2e687bec010a-application-1",
    "gm-direct-production-direct-api-1",
    "greenminddb-backend-1",
]


def run(args, timeout=15):
    return subprocess.check_output(args, timeout=timeout, stderr=subprocess.DEVNULL)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024**2), b""):
            h.update(b)
    return h.hexdigest()


def save(path, value):
    with path.open("x") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)


def inspect(names):
    return [
        json.loads(v)
        for v in run(
            [
                "docker",
                "inspect",
                "--format",
                '{"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"networks":{{json .NetworkSettings.Networks}}}',
                *names,
            ]
        ).splitlines()
    ]


def counts():
    result = {}
    for database, tables in COUNT_TABLES.items():
        result[database] = {}
        for table in tables:
            sql = f"BEGIN READ ONLY; SET LOCAL statement_timeout=8000; SELECT count(*) FROM {table}; COMMIT;"
            body = run(
                [
                    "docker",
                    "exec",
                    "gm-staging-postgres-1",
                    "psql",
                    "-U",
                    "plantuser",
                    "-d",
                    database,
                    "-Xt",
                    "--set",
                    "ON_ERROR_STOP=on",
                    "-c",
                    sql,
                ],
                12,
            ).decode()
            numbers = [int(v.strip()) for v in body.splitlines() if v.strip().isdigit()]
            assert len(numbers) == 1
            result[database][table] = numbers[0]
    return result


def health(baseline=None):
    assert sha(Path("/etc/nginx/sites-available/greenmind-prod")) == PROD_SHA
    for port in (8120, 8003, 8000, 8140):
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            assert r.status == 200
    now = inspect(RECEIVERS)
    assert all(v["running"] for v in now)
    if baseline is not None:
        assert now == baseline
    return now


def cohort_path(cohort):
    assert re.fullmatch("[a-f0-9]{24}", cohort)
    p = ROOT / cohort
    assert p.parent == ROOT and not p.is_symlink()
    return p


def target_config(p):
    config = json.loads((p / "compose.json").read_text())
    assert (
        sha(p / "compose.json")
        == json.loads((p / "target.json").read_text())["compose_sha256"]
    )
    assert 1 <= len(config["services"]) <= 24
    for v in config["services"].values():
        assert (
            "staging" in v["container_name"] and "production" not in v["container_name"]
        )
        assert v["cgroup_parent"] == "greenmind-staging.slice"
        for x in v.get("ports", []):
            assert x["host_ip"] == "127.0.0.1"
        for flag in (
            "RAW_ARCHIVE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
            "VISUAL_PRUNE_ENABLED",
        ):
            assert v["environment"][flag] == "false"
    return config


def target_stop(p):
    c = target_config(p)
    names = [v["container_name"] for v in c["services"].values()]
    running = [v["name"].lstrip("/") for v in inspect(names) if v["running"]]
    # A stale watchdog must never stop containers recreated by a newer cohort.
    for name in running:
        config_file = (
            run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
                    name,
                ]
            )
            .decode()
            .strip()
        )
        assert config_file == str(p / "compose.json"), "Different Staging owner"
    if running:
        run(["docker", "stop", "--time", "20", *running], 55)
    assert not any(v["running"] for v in inspect(names))
    return {"stopped": True, "accepted": (p / "accepted.json").exists()}


def fence_target(cohort):
    request = json.dumps({"action": "stop", "cohort": cohort}).encode() + b"\n"
    cmd = [
        "ssh",
        "-F",
        "/dev/null",
        "-T",
        "-i",
        str(INSTALL / "fence-key"),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(INSTALL / "archive-known-hosts"),
        "-o",
        "ConnectTimeout=5",
        "greenmind-staging-fence@2.31.22.33",
    ]
    completed = subprocess.run(
        cmd, input=request, capture_output=True, timeout=70, check=True
    )
    return json.loads(completed.stdout)


def proxy(p, mode):
    original = (p / "proxy-before.conf").read_bytes()
    assert hashlib.sha256(original).hexdigest() == STAGE_SHA
    candidate = original
    for old, new in PORTS.items():
        before = f"127.0.0.1:{old}".encode()
        assert before in candidate
        candidate = candidate.replace(before, f"127.0.0.1:{new}".encode())
    fenced = candidate.replace(
        b"# HTTPS \xe2\x80\x94 Staging\nserver {",
        b"# HTTPS \xe2\x80\x94 Staging\nserver {\n    if ($request_method !~ ^(GET|HEAD|OPTIONS)$) { return 503; }",
        1,
    )
    assert fenced != candidate
    choices = {"original": original, "fenced": fenced, "complete": candidate}
    previous = PROXY.read_bytes()
    assert previous in choices.values()
    body = choices[mode]
    fd, name = tempfile.mkstemp(dir=PROXY.parent, prefix=".greenmind-cohort-")
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o644)
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(name, PROXY)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
        health(json.loads((p / "baseline.json").read_text())["receivers"])
    except BaseException:
        assert PROXY.read_bytes() == body
        fd, name = tempfile.mkstemp(
            dir=PROXY.parent, prefix=".greenmind-cohort-rollback-"
        )
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o644)
            f.write(previous)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, PROXY)
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
        raise
    return {"sha256": sha(PROXY), "mode": mode}


def rollback_source(p, cohort):
    if (p / "accepted.json").exists():
        return {"rollback_withheld": True, "reason": "accepted_owner"}
    fenced = fence_target(cohort)
    if fenced.get("accepted") is True:
        return {"rollback_withheld": True, "reason": "accepted_target_owner"}
    assert fenced == {"stopped": True, "accepted": False}
    proxy(p, "original")
    m = json.loads((p / "baseline.json").read_text())
    names = m["pause_containers"]
    run(["docker", "start", *names], 60)
    assert all(v["running"] for v in inspect(names))
    health(m["receivers"])
    run(["systemctl", "stop", "greenmind-stage-watchdog-" + cohort + ".timer"])
    save(
        p / ("rollback-" + str(time.time_ns()) + ".json"),
        {"target_fenced": True, "source_resumed": True, "deleted_files": 0},
    )
    return {"rollback_verified": True}


def source_action(action, p, cohort, request):
    if action == "prepare":
        assert not p.exists()
        assert sha(PROXY) == STAGE_SHA
        available = (
            next(
                int(x.split()[1])
                for x in Path("/proc/meminfo").read_text().splitlines()
                if x.startswith("MemAvailable:")
            )
            / 1024
        )
        assert available >= 704, "Keep original source reserve before control startup"
        m = json.loads((OLD / "manifest.json").read_text())
        names = m["pause_containers"]
        assert len(names) == len(set(names)) == 18
        states = inspect(names)
        assert all(
            v["running"] and set(v["networks"]) == {"gm-staging_default"}
            for v in states
        )
        # Existing production proxy must have no dependency on staged listeners.
        assert not re.search(
            rb"127\.0\.0\.1:(8124|8125|8126|8002|3009|3124|9002)\b",
            Path("/etc/nginx/sites-available/greenmind-prod").read_bytes(),
        )
        p.mkdir(mode=0o700)
        baseline = m | {"receivers": health(), "stage_states": states}
        save(p / "baseline.json", baseline)
        with (p / "proxy-before.conf").open("xb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(PROXY.read_bytes())
        return {
            "prepared": True,
            "cohort": cohort,
            "source_images": {v["name"].lstrip("/"): v["image"] for v in states},
        }
    m = json.loads((p / "baseline.json").read_text())
    health(m["receivers"])
    if action == "arm":
        unit = "greenmind-stage-watchdog-" + cohort
        run(
            [
                "systemd-run",
                "--unit=" + unit,
                "--on-active=720",
                "/usr/bin/python3",
                str(INSTALL / "control.py"),
                "--rollback",
                cohort,
            ]
        )
        save(p / "armed.json", {"unit": unit, "seconds": 720})
        return {"armed": True}
    if action == "pause":
        assert (p / "armed.json").exists() and sha(PROXY) == STAGE_SHA
        names = m["pause_containers"]
        core = ["gm-staging-postgres-1", "gm-staging-minio-1"]
        run(
            ["docker", "stop", "--time", "20", *[v for v in names if v not in core]], 60
        )
        save(p / "cold-counts.json", counts())
        run(["docker", "stop", "--time", "20", *core], 60)
        assert not any(v["running"] for v in inspect(names))
        health(m["receivers"])
        save(p / "paused.json", {"stopped": True})
        return {"paused": True}
    if action == "snapshot":
        assert (p / "paused.json").exists() and not any(
            v["running"] for v in inspect(m["pause_containers"])
        )
        paths = [v["source"] for v in m["mounts"]]
        assert all(
            v.startswith(
                (
                    "/home/traver/greenmind-",
                    "/mnt/HC_Volume_106755700/docker/volumes/gm-staging_",
                )
            )
            for v in paths
        )
        with (p / "cold.tar").open("xb") as f:
            os.fchmod(f.fileno(), 0o600)
            subprocess.run(
                [
                    "nice",
                    "-n",
                    "19",
                    "ionice",
                    "-c",
                    "3",
                    "tar",
                    "-C",
                    "/",
                    "-cpf",
                    "-",
                    *[v.lstrip("/") for v in paths],
                ],
                stdout=f,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=True,
            )
            f.flush()
            os.fsync(f.fileno())
        assert (p / "cold.tar").stat().st_size < 4 * 1024**3
        proof = {
            "cohort": cohort,
            "sha256": sha(p / "cold.tar"),
            "bytes": (p / "cold.tar").stat().st_size,
            "consistent_cold_copy": True,
            "source_stopped": True,
            "table_counts": json.loads((p / "cold-counts.json").read_text()),
        }
        health(m["receivers"])
        save(p / "snapshot.json", proof)
        return proof
    if action == "stream":
        assert not any(v["running"] for v in inspect(m["pause_containers"]))
        with (p / "cold.tar").open("rb") as f:
            for b in iter(lambda: f.read(256 * 1024), b""):
                sys.stdout.buffer.write(b)
        return None
    if action == "proxy":
        return proxy(p, request["mode"])
    if action == "accept":
        assert request["target_accepted"] is True and not any(
            v["running"] for v in inspect(m["pause_containers"])
        )
        assert fence_target(cohort) == {"stopped": False, "accepted": True}
        save(
            p / "accepted.json",
            {
                "owner": "archive",
                "cohort": cohort,
                "snapshot": json.loads((p / "snapshot.json").read_text()),
                "deleted_files": 0,
            },
        )
        run(["systemctl", "stop", "greenmind-stage-watchdog-" + cohort + ".timer"])
        return {"accepted": True}
    if action == "rollback":
        return rollback_source(p, cohort)
    raise ValueError("Unknown fixed source action")


def archive_action(action, p, cohort, request):
    if action == "prepare":
        assert not p.exists()
        old = Path("/var/lib/greenmind/staging/compose-v6.json")
        c = json.loads(old.read_text())
        names = [v["container_name"] for v in c["services"].values()]
        assert not any(v["running"] for v in inspect(names))
        available = (
            next(
                int(x.split()[1])
                for x in Path("/proc/meminfo").read_text().splitlines()
                if x.startswith("MemAvailable:")
            )
            / 1024
        )
        assert available >= 1800
        p.mkdir(mode=0o700)
        save(p / "source.json", request["source"])
        imported = json.loads(
            Path("/var/lib/greenmind/staging/images-verified.json").read_text()
        )["images"]
        facts = json.loads(
            Path(
                "/home/traver/greenmind-archive-rollout-20261008/staging-manifest-v2.json"
            ).read_text()
        )["services"]
        source = request["source"]["source_images"]
        for v in facts:
            assert source[v["name"]] == v["image"]
        for v in c["services"].values():
            # The only explicit old-schema alignment was base-backend; retain it.
            assert v["image"] in set(imported.values())
            for mount in v.get("volumes", []):
                path = Path(mount["source"])
                assert str(path).startswith("/var/lib/greenmind/staging/")
                mount["source"] = str(
                    p / "fresh" / path.relative_to("/var/lib/greenmind/staging")
                )
        save(p / "compose.json", c)
        save(
            p / "target.json",
            {"compose_sha256": sha(p / "compose.json"), "cohort": cohort},
        )
        target_config(p)
        return {"prepared": True, "cohort": cohort}
    if action == "import":
        proof = request["snapshot"]
        assert (
            proof["cohort"] == cohort
            and proof["source_stopped"]
            and proof["consistent_cold_copy"]
        )
        size = proof["bytes"]
        assert type(size) == int and 0 < size < 4 * 1024**3
        h = hashlib.sha256()
        with (p / "cold.tar").open("xb") as f:
            os.fchmod(f.fileno(), 0o600)
            left = size
            while left:
                b = sys.stdin.buffer.read(min(256 * 1024, left))
                assert b
                f.write(b)
                h.update(b)
                left -= len(b)
            f.flush()
            os.fsync(f.fileno())
        assert h.hexdigest() == proof["sha256"]
        allowed = [
            v["source"].lstrip("/")
            for v in json.loads(
                Path(
                    "/home/traver/greenmind-archive-rollout-20261008/staging-manifest-v2.json"
                ).read_text()
            )["mounts"]
        ]
        dest = p / "fresh" / "import"
        dest.mkdir(mode=0o700, parents=True)
        with tarfile.open(p / "cold.tar") as t:
            entries = t.getmembers()
            assert len(entries) < 100000 and sum(v.size for v in entries) < 4 * 1024**3
            for v in entries:
                n = v.name.rstrip("/")
                assert not n.startswith("/") and ".." not in Path(n).parts
                assert v.isfile() or v.isdir()
                assert any(n == x or n.startswith(x + "/") for x in allowed)
            t.extractall(dest, filter="fully_trusted")
        (dest / "home/traver/greenmind-storage-statistics").rename(
            p / "fresh" / "storage-statistics"
        )
        c = target_config(p)
        for v in c["services"].values():
            for mount in v.get("volumes", []):
                assert Path(mount["source"]).exists()
        save(p / "restored.json", proof)
        return {"restored": True, "sha256": proof["sha256"]}
    if action == "start":
        assert (p / "restored.json").exists()
        c = target_config(p)
        command = ["docker", "compose", "-f", str(p / "compose.json")]
        # All target writers are fenced from public routes until Source publishes.
        run(
            command
            + ["up", "-d", "--no-build", "--no-deps", "base-postgres", "base-minio"],
            60,
        )
        for attempt in range(15):
            try:
                actual = counts()
                assert (
                    actual
                    == json.loads((p / "restored.json").read_text())["table_counts"]
                )
                break
            except subprocess.CalledProcessError:
                if attempt == 14:
                    raise
                time.sleep(2)
        save(p / "counts-verified.json", actual)
        run(
            command
            + [
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                *[n for n in c["services"] if n not in {"base-postgres", "base-minio"}],
            ],
            90,
        )
        deadline = time.monotonic() + 90
        ports = {
            8124: "/health",
            8125: "/health",
            8126: "/health",
            8002: "/health",
            3124: "/de",
            3009: "/de",
            9002: "/minio/health/live",
        }
        while time.monotonic() < deadline:
            try:
                for port, path in ports.items():
                    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                        assert r.status == 200
                assert all(
                    v["running"]
                    for v in inspect(
                        [v["container_name"] for v in c["services"].values()]
                    )
                )
                break
            except (OSError, AssertionError):
                time.sleep(2)
        else:
            raise RuntimeError("Target startup health timeout")
        save(p / "started.json", {"ready": True})
        return {"ready": True}
    if action == "stop":
        if (p / "accepted.json").exists():
            return {"stopped": False, "accepted": True}
        return target_stop(p)
    if action == "status":
        c = target_config(p)
        return {
            "running": sum(
                v["running"]
                for v in inspect([v["container_name"] for v in c["services"].values()])
            ),
            "accepted": (p / "accepted.json").exists(),
        }
    if action == "accept":
        assert (p / "started.json").exists()
        c = target_config(p)
        assert all(
            v["running"]
            for v in inspect([v["container_name"] for v in c["services"].values()])
        )
        save(
            p / "accepted.json",
            {
                "owner": "archive",
                "cohort": cohort,
                "snapshot": json.loads((p / "restored.json").read_text()),
                "deletion_enabled": False,
            },
        )
        return {"accepted": True}
    raise ValueError("Unknown fixed target action")


def main():
    assert os.geteuid() == 0 and (SOURCE or ARCHIVE)
    parser = argparse.ArgumentParser()
    parser.add_argument("--fence", action="store_true")
    parser.add_argument("--rollback")
    args = parser.parse_args()
    if args.rollback:
        assert SOURCE and not args.fence
        print(json.dumps(rollback_source(cohort_path(args.rollback), args.rollback)))
        return
    # Binary payload must use the same unbuffered stream as its JSON header.
    raw = sys.stdin.buffer.readline(16385)
    assert raw.endswith(b"\n") and len(raw) <= 16384
    request = json.loads(raw)
    assert set(request) <= {
        "action",
        "cohort",
        "source",
        "snapshot",
        "mode",
        "target_accepted",
    }
    action = request["action"]
    cohort = request["cohort"]
    p = cohort_path(cohort)
    if args.fence:
        assert ARCHIVE and action in {"stop", "status"}
    value = (source_action if SOURCE else archive_action)(action, p, cohort, request)
    if value is not None:
        print(json.dumps(value))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - redact private operator driver errors
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(e).__name__,
                    "deleted_files": 0,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
