"""Minimal reversible proxy switch, gated by a fresh pinned real-data acceptance."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.compat_proxy import rollback
from app.raw_archive.observation import protected
from app.raw_archive.policy import checksum
from app.raw_archive.runner import health_probe

RUNTIME = Path(__file__).resolve().parents[3]
TARGET = Path("/etc/nginx/sites-available/greenmind-prod")


def publish(source, expected):
    assert checksum(TARGET) == expected, (
        "Production proxy changed; inspect before proceeding"
    )
    path = TARGET.with_suffix(".archive-compat-candidate")
    assert not path.exists()
    with path.open("xb") as body:
        os.chmod(path, 0o644)
        body.write(source.read_bytes())
        body.flush()
        os.fsync(body.fileno())
    os.replace(path, TARGET)
    subprocess.run(["nginx", "-t"], check=True, timeout=8, capture_output=True)
    subprocess.run(
        ["systemctl", "reload", "nginx"], check=True, timeout=8, capture_output=True
    )


def main():
    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--acceptance-sha256", required=True)
    parser.add_argument("--rehearse", action="store_true")
    args = parser.parse_args()
    assert os.geteuid() == 0
    bundle = json.loads((RUNTIME / "bundle.json").read_text())
    state = (
        Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation")
        / bundle["revision"][:12]
        / "reader"
    )
    manifest = json.loads((state / "manifest.json").read_text())
    assert manifest["started"]
    assert (
        not args.acceptance.is_symlink() and args.acceptance.stat().st_mode & 0o077 == 0
    )
    assert args.acceptance.stat().st_size <= 1024**2
    assert checksum(args.acceptance) == args.acceptance_sha256
    acceptance = json.loads(args.acceptance.read_text())
    assert acceptance["revision"] == bundle["revision"] and acceptance["passed"] is True
    assert acceptance["base"] == "http://127.0.0.1:8141"
    assert (
        datetime.now(UTC) - timedelta(minutes=15)
        <= datetime.fromisoformat(acceptance["at"])
        <= datetime.now(UTC)
    )
    required = {
        "gateway_old_url_local",
        "gateway_old_url_archive_only",
        "direct_old_url_local",
        "direct_old_url_archive_only",
        "real_zone_denial",
        "sha256_and_wav_decode",
        "cookie_authentication",
        "no_ingestion_routes",
    }
    assert required.issubset(set(acceptance["checks"])), (
        "Real old-URL acceptance remains incomplete"
    )
    before, candidate = state / "before.conf", state / "candidate.conf"
    assert checksum(before) == manifest["before_sha256"]
    assert checksum(candidate) == manifest["candidate_sha256"]
    assert rollback(candidate.read_text(), reader_port=8141) == before.read_text()
    probe = health_probe()
    try:
        assert probe(), "Receiver headroom unavailable"
        baseline = protected()
        try:
            publish(candidate, manifest["before_sha256"])
            with httpx.Client(timeout=3, trust_env=False) as client:
                for route in ("/api/v1/wav/count", "/api/v1/direct-ingest/segments"):
                    assert (
                        client.get("https://green-mind.ch" + route).status_code == 401
                    )
            current = protected()
            assert all(
                current[k] == v for k, v in baseline.items() if k != "greenmind-prod"
            )
            assert probe(), "Receiver health/headroom changed"
        except Exception:
            # A failing nginx test still left the candidate on disk; restore original.
            if checksum(TARGET) == manifest["candidate_sha256"]:
                publish(before, manifest["candidate_sha256"])
            raise
        if args.rehearse:
            publish(before, manifest["candidate_sha256"])
            assert protected() == baseline
        print(
            json.dumps(
                {
                    "activated": not args.rehearse,
                    "rehearsed": args.rehearse,
                    "receivers_unchanged": True,
                    "deleted_files": 0,
                }
            )
        )
    finally:
        probe.close()


if __name__ == "__main__":
    main()
