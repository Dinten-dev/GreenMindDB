"""Hash-guarded archive-only update; running jobs and receivers are untouched."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and not args.previous.is_symlink()
    assert (
        args.previous.stat().st_uid == 0 and args.previous.stat().st_mode & 0o077 == 0
    )
    previous = json.loads(args.previous.read_text())
    assert args.previous.parent == Path(previous["state"])
    assert set(previous["overrides"]) == {
        "greenmind-raw-copy.service",
        "greenmind-archive-export.service",
    }
    for unit, item in previous["overrides"].items():
        path = Path(item["path"])
        assert path.parent == Path("/etc/systemd/system") / (unit + ".d")
        assert not path.is_symlink() and digest(path) == item["sha256"]
    bundle = json.loads((PACKAGE / "bundle.json").read_text())
    for name, expected in bundle["files"].items():
        path = PACKAGE / name
        assert (
            PACKAGE in path.resolve().parents
            and not path.is_symlink()
            and digest(path) == expected
        )
    revision = bundle["revision"]
    assert len(revision) == 40 and all(c in "0123456789abcdef" for c in revision)
    protected = runpy.run_path(
        str(PACKAGE / "deploy/raw-archive/acceptance/install.py")
    )["protected"]
    before = protected()
    assert before["production_proxy"] == bundle["proxy_before_sha256"]
    runtime = Path("/opt/greenmind/archive-acceptance") / revision[:12]
    state = (
        Path("/mnt/HC_Volume_106755700/greenmind-archive-acceptance") / revision[:12]
    )
    assert not runtime.exists() and not state.exists()
    shutil.copytree(PACKAGE, runtime)
    for path in runtime.rglob("*"):
        assert not path.is_symlink()
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
    runtime.chmod(0o755)
    state.mkdir(mode=0o700)
    old_compose = Path(previous["state"]) / "exports.json"
    config = json.loads(old_compose.read_text())
    worker = config["services"]["export-worker"]
    worker["volumes"] = [
        value.replace(previous["runtime"], str(runtime)) for value in worker["volumes"]
    ]
    config["name"] = "gm-archive-jobs-" + revision[:12]
    compose = state / "exports.json"
    compose.write_text(json.dumps(config, indent=2) + "\n")
    compose.chmod(0o600)
    overrides = {}
    backups = {}
    updated = []
    try:
        for unit, item in previous["overrides"].items():
            path = Path(item["path"])
            assert digest(path) == item["sha256"]
            old = path.read_text()
            text = old.replace(previous["runtime"], str(runtime))
            if unit == "greenmind-archive-export.service":
                text = text.replace(str(old_compose), str(compose))
            else:
                text += "SuccessExitStatus=75\n"
            assert str(runtime) in text and previous["runtime"] not in text
            pending = state / (unit + ".next.conf")
            with pending.open("x") as body:
                body.write(text)
                body.flush()
                os.fsync(body.fileno())
            pending.chmod(0o644)
            overrides[unit] = {"path": str(path), "sha256": digest(pending)}
            backup = state / (unit + ".previous.conf")
            path.rename(backup)
            backups[unit] = {
                "original_path": str(path),
                "backup_path": str(backup),
                "sha256": item["sha256"],
            }
            os.link(
                pending, path
            )  # Atomic publication; never overwrite concurrent edits.
            updated.append((unit, path))
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        for unit in overrides:
            actual = subprocess.check_output(
                ["systemctl", "show", unit, "--property=ExecStart", "--value"],
                text=True,
                timeout=4,
            )
            assert str(runtime) in actual
        assert protected() == before
    except Exception:
        for unit, path in updated:
            assert digest(path) == overrides[unit]["sha256"]
            path.rename(state / (path.name + ".failed"))
        for item in backups.values():
            assert (
                digest(Path(item["backup_path"])) == item["sha256"]
                and not Path(item["original_path"]).exists()
            )
            Path(item["backup_path"]).rename(item["original_path"])
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=10)
        raise
    manifest = {
        "revision": revision,
        "runtime": str(runtime),
        "state": str(state),
        "protected": before,
        "overrides": overrides,
        "previous_overrides": backups,
        "previous_revision": previous["revision"],
        "receivers_unchanged": True,
        "destructive_flags_enabled": False,
        "deleted_files": 0,
        "copy_minimum_memory_mib": 512,
        "applies_to_next_scheduled_run": True,
    }
    path = state / "installation.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
