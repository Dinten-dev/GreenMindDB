"""Hash-checked staging image/data import; separate core and application startup."""

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

INCOMING = Path("/home/traver/greenmind-archive-rollout-20261008")
ROOT = Path("/var/lib/greenmind/staging")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as body:
        for block in iter(lambda: body.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def resolve_images(archive, expected, normalized):
    """Legacy configuration IDs and OCI manifest IDs must prove identical contents."""
    ids = subprocess.check_output(
        ["docker", "image", "ls", "-q", "--no-trunc"], text=True
    ).splitlines()
    installed = json.loads(
        subprocess.check_output(["docker", "image", "inspect", *sorted(set(ids))])
    )
    result = {}
    descriptors = {}
    with tarfile.open(normalized) as source:
        for entry in json.load(source.extractfile("index.json"))["manifests"]:
            body = json.load(
                source.extractfile("blobs/sha256/" + entry["digest"].split(":")[1])
            )
            descriptors[body["config"]["digest"]] = entry["digest"]
    # OCI retains original config bytes; Docker 29 omits deprecated build-container fields.
    legacy_fields = {
        "Hostname",
        "Domainname",
        "AttachStdin",
        "AttachStdout",
        "AttachStderr",
        "Tty",
        "OpenStdin",
        "StdinOnce",
        "Image",
    }
    with tarfile.open(archive) as source:
        entries = json.load(source.extractfile("manifest.json"))
        for entry in entries:
            raw = source.extractfile(entry["Config"]).read()
            identity = "sha256:" + hashlib.sha256(raw).hexdigest()
            assert identity in expected, "Unexpected image configuration"
            config = json.loads(raw)
            matches = [
                v
                for v in installed
                if v["Architecture"] == config["architecture"]
                and v["Os"] == config["os"]
                and v["RootFS"]["Layers"] == config["rootfs"]["diff_ids"]
                and all(
                    v["Config"].get(k) == value
                    for k, value in config["config"].items()
                    if k not in legacy_fields
                )
                and v["Id"] == descriptors[identity]
            ]
            pinned = "greenmind-staging-import:" + identity.split(":")[1][:12]
            matches = [v for v in matches if pinned in (v.get("RepoTags") or [])]
            assert len(matches) == 1, (
                "Image configuration and every filesystem layer must match"
            )
            result[identity] = matches[0]["Id"]
    assert set(result) == set(expected), "Missing staging images"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("images", "data", "core", "apps"), required=True
    )
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    manifest = json.loads((INCOMING / "staging-manifest-v2.json").read_text())
    original = INCOMING / "staging-compose-v2.json"
    assert digest(original) == manifest["compose_sha256"]
    if args.phase == "images":
        evidence = json.loads((INCOMING / "images-evidence.json").read_text())
        archive = INCOMING / "staging-images.tar"
        assert (
            archive.stat().st_size == evidence["bytes"]
            and digest(archive) == evidence["sha256"]
        )
        assert set(evidence["images"]) == set(manifest["images"])
        normalized = INCOMING / "staging-images-complete-index.tar"
        assert normalized.exists(), (
            "Complete OCI index required for multi-image Docker load"
        )
        subprocess.run(["docker", "load", "-i", str(normalized)], check=True)
        images = resolve_images(archive, manifest["images"], normalized)
        config = json.loads(original.read_text())
        for service in config["services"].values():
            service["image"] = images[service["image"]]
            assert all(p["host_ip"] == "127.0.0.1" for p in service.get("ports", []))
            assert service["cgroup_parent"] == "greenmind-staging.slice"
            for flag in (
                "RAW_ARCHIVE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            ):
                assert service["environment"][flag] == "false"
        with (ROOT / "compose.json").open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(config, output, indent=2)
        proof = {
            "source_tar_sha256": evidence["sha256"],
            "images": images,
            "compose_sha256": digest(ROOT / "compose.json"),
            "deletion_enabled": False,
        }
        with (ROOT / "images-verified.json").open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(proof, output, indent=2)
        print(json.dumps({"verified_images": len(images), "deletion_enabled": False}))
        return
    if args.phase == "data":
        evidence = json.loads((INCOMING / "data-evidence.json").read_text())
        archive = INCOMING / "staging-data.tar"
        assert evidence["consistent_cold_copy"] is True
        assert (
            archive.stat().st_size == evidence["bytes"]
            and digest(archive) == evidence["sha256"]
        )
        destination = ROOT / "import"
        assert not destination.exists(), "Never overwrite an earlier staging import"
        permitted = [v["source"].lstrip("/") for v in manifest["mounts"]]
        with tarfile.open(archive) as source:
            entries = source.getmembers()
            assert len(entries) < 100000 and sum(v.size for v in entries) < 4 * 1024**3
            for entry in entries:
                name = entry.name.rstrip("/")
                assert not name.startswith("/") and ".." not in Path(name).parts
                assert entry.isfile() or entry.isdir(), (
                    "No archive symlinks or device files"
                )
                assert any(name == v or name.startswith(v + "/") for v in permitted)
            destination.mkdir(mode=0o700)
            source.extractall(
                destination, filter="fully_trusted"
            )  # All members and the complete tar digest checked above.
        stats = destination / "home/traver/greenmind-storage-statistics"
        stats.rename(ROOT / "storage-statistics")
        for mount in manifest["mounts"]:
            assert Path(mount["target"]).exists()
        with (ROOT / "data-verified.json").open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(evidence, output, indent=2)
        print(
            json.dumps(
                {"verified_data_bytes": evidence["bytes"], "deletion_enabled": False}
            )
        )
        return
    proof = json.loads((ROOT / "images-verified.json").read_text())
    assert digest(ROOT / "compose.json") == proof["compose_sha256"]
    assert (ROOT / "data-verified.json").is_file()
    config = json.loads((ROOT / "compose.json").read_text())
    selected = [n for n in config["services"] if n in {"base-postgres", "base-minio"}]
    if args.phase == "apps":
        selected = [n for n in config["services"] if n not in selected]
    for service in selected:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "compose.json"),
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                service,
            ],
            check=True,
        )
    print(
        json.dumps(
            {"phase": args.phase, "started": selected, "deletion_enabled": False}
        )
    )


if __name__ == "__main__":
    main()
