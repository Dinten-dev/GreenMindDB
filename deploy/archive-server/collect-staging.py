"""Private regular-configuration export; never reads container credential environments."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

DEST = Path("/home/traver/greenmind-archive-rollout-20261008/staging-preparation-v2")
CONFIGS = {
    "base": "/home/traver/greenmind-staging/docker-compose.staging.yml",
    "admin": "/home/traver/greenmind-admin-staging/1b449e9/compose.json",
    "release": "/home/traver/greenmind-release-staging/61cce4e/compose.json",
    "direct": "/home/traver/greenmind-direct-staging/compose.yml",
}
PROTECTED = [
    "gm-zones-production-2e687bec010a-application-1",
    "gm-direct-production-direct-api-1",
    "greenminddb-backend-1",
]


def main():
    assert os.uname().nodename == "Biolingo"
    assert not DEST.exists(), (
        "Private preparation already exists; inspect it before retry"
    )
    DEST.mkdir(parents=True, mode=0o700)
    services = {}
    images = set()
    mounts = set()
    facts = []
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    template = '{"name":{{json .Name}},"image":{{json .Image}},"config":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"networks":{{json .NetworkSettings.Networks}},"mounts":{{json .Mounts}}}'
    states = [
        json.loads(line)
        for line in subprocess.check_output(
            ["docker", "inspect", "--format", template, *names], text=True, timeout=10
        ).splitlines()
    ]
    by_name = {value["name"].lstrip("/"): value for value in states}
    for name in PROTECTED:
        assert "gm-staging_default" not in by_name[name]["networks"], (
            "Production receiver shares staging network"
        )
    for key, value in CONFIGS.items():
        path = Path(value)
        rendered = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "--profile",
                    "*",
                    "--project-directory",
                    str(path.parent),
                    "-f",
                    str(path),
                    "config",
                    "--format",
                    "json",
                ],
                text=True,
                timeout=20,
                stderr=subprocess.DEVNULL,
            )
        )
        for service, config in rendered["services"].items():
            old = [
                n
                for n in names
                if by_name[n]["config"] == value and by_name[n]["service"] == service
            ]
            if len(old) != 1:
                continue  # Preserve only currently running services; inactive generations stay untouched.
            name = old[0]
            image = by_name[name]["image"]
            images.add(image)
            config.pop("build", None)
            config.pop("depends_on", None)
            config.pop("deploy", None)
            config.pop("container_name", None)
            config.pop("env_file", None)
            config["image"] = image
            config["pull_policy"] = "never"
            config["container_name"] = name
            config["cgroup_parent"] = "greenmind-staging.slice"
            config["mem_limit"] = "192m"
            if service == "postgres":
                config["mem_limit"] = "320m"
            if service == "minio":
                config["mem_limit"] = "256m"
            config["memswap_limit"] = config["mem_limit"]
            config["networks"] = {"staging": {"aliases": [service, name]}}
            env = config.setdefault("environment", {})
            for flag in [
                "RAW_ARCHIVE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            ]:
                env[flag] = "false"
            actual_mounts = by_name[name]["mounts"]
            config["volumes"] = []
            for m in actual_mounts:
                source = m["Source"]
                if source in {"/home/traver/greenmind-storage-statistics"}:
                    target = "/var/lib/greenmind/staging/storage-statistics"
                else:
                    target = "/var/lib/greenmind/staging/import" + source
                mounts.add((source, target))
                config["volumes"].append(
                    {
                        "type": "bind",
                        "source": target,
                        "target": m["Destination"],
                        "read_only": not m["RW"],
                    }
                )
            config["restart"] = "unless-stopped"
            services[key + "-" + service] = config
            facts.append(
                {
                    "name": name,
                    "image": image,
                    "source_config": value,
                    "service": service,
                }
            )
    pause = sorted(n for n in names if "gm-staging_default" in by_name[n]["networks"])
    copied = {v["name"] for v in facts}
    for name in set(pause) - copied:
        assert by_name[name]["service"] in {"frontend", "visual-frontend"}, (
            "Unaccounted staging data writer: " + name
        )
    result = {
        "name": "gm-archivehost-staging",
        "services": services,
        "networks": {
            "staging": {
                "name": "gm-archivehost-staging",
                "driver": "bridge",
                "ipam": {
                    "config": [{"subnet": "172.28.21.0/24", "gateway": "172.28.21.1"}]
                },
            }
        },
    }
    body = json.dumps(result, indent=2).encode()
    with (DEST / "compose.json").open("xb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(body)
    manifest = {
        "services": facts,
        "pause_containers": pause,
        "mounts": [{"source": a, "target": b} for a, b in sorted(mounts)],
        "images": sorted(images),
        "compose_sha256": hashlib.sha256(body).hexdigest(),
        "deletion_enabled": False,
        "activated": False,
    }
    with (DEST / "manifest.json").open("x") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(manifest, f, indent=2)
    print(
        json.dumps(
            {
                "services": len(services),
                "images": len(images),
                "manifest": str(DEST / "manifest.json"),
                "activated": False,
                "credential_values_printed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
