"""Native, keyless scheduler probe with synthetic local archive/provider fixtures."""

import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive.health import HealthProbe
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import parquet, runtime, schedule
from app.tiered_archive.latency import LatencyGuard
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import SnapshotPending, publish, verify_snapshot
from app.tiered_archive.policy import Candidate
from app.tiered_archive.resources import read_cgroup, reasons


def main():
    root = Path(sys.argv[1])
    root.mkdir(mode=0o700)
    baseline = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "baseline"
    urls = [r["url"] for r in json.loads(baseline.read_text())["endpoints"]]
    guard = LatencyGuard(
        baseline, checksum(baseline), urls, root, root / "control", "copy"
    )
    health = HealthProbe(
        urls, [root], 1024, min(2, 0.5 * (os.cpu_count() or 1)), on_sample=guard.observe
    )
    began = time.monotonic()
    report = {
        "passed": False,
        "fixture_archive_and_provider": True,
        "original_accesses": 0,
        "box_accesses": 0,
        "keys_used": False,
        "cases": [],
    }
    ledger = None

    def checkpoint():
        if time.monotonic() - began > 60 or not health() or reasons(read_cgroup()):
            raise ArchiveBlocked("resource_or_health_pause")

    class Box:
        identity = "synthetic-local-snapshot-flow-fixture"

        def __init__(self, base):
            self.base = base
            self.gets = 0
            base.mkdir()

        def publish(self, key, path):
            target = self.base / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

        def download(self, key, target, size, *, snapshot=None):
            self.gets += 1
            shutil.copyfile(self.base / key, target)
            assert target.stat().st_size <= size

    class GuardedHealth:
        def __call__(self):
            checkpoint()
            return True

        def close(self):
            pass

    try:
        checkpoint()
        assert os.getenv("TIERED_ARCHIVE_DELETE_ENABLED", "false") == "false"
        now = datetime.now(UTC)
        at = now - timedelta(days=100)
        for name in ("pending", "future", "corrupt"):
            directory = root / name
            directory.mkdir()
            ledger = Ledger(directory / "ledger")
            box = Box(directory / "box")
            rows = [
                {
                    "identity": "synthetic-row",
                    "organization": "11111111-1111-4111-8111-111111111111",
                    "sensor": "22222222-2222-4222-8222-222222222222",
                    "time_us": int(at.timestamp() * 1e6),
                    "body": {"value": 2.0},
                }
            ]
            path = directory / "rows.parquet"
            metadata = dict(
                parquet.write(path, rows, checkpoint=checkpoint), format="parquet"
            )
            candidate = Candidate(
                "staging",
                "gateway",
                "visual_reading",
                rows[0]["organization"],
                at,
                at + timedelta(days=1),
                metadata["content_sha256"],
                1,
                "dashboard",
                at + timedelta(days=1),
                complete=True,
                features_verified=True,
                projections_verified=True,
                dependencies_complete=True,
            )
            publish(
                candidate,
                [(path, metadata)],
                box,
                ledger,
                checkpoint=checkpoint,
                now=now - timedelta(minutes=2),
            )
            selected = [now - timedelta(minutes=3)]

            class Provider:
                def __init__(self, selection=selected):
                    self.selection = selection

                def read(self, identity):
                    return {
                        "id": identity,
                        "storage_box": 4,
                        "name": "fixture-snapshot",
                        "created": self.selection[0].isoformat(),
                    }

            @contextmanager
            def worker(ledger=ledger, box=box):
                yield ledger, box, checkpoint

            runtime.state_root = schedule.state_root = lambda ledger=ledger: ledger.root
            runtime.worker = worker
            runtime.wav_source = lambda *_: SimpleNamespace(
                catalog=SimpleNamespace(page=lambda *_: [])
            )
            provider = Provider()
            runtime.verify = (
                lambda _, identity, candidate=candidate, box=box, ledger=ledger, provider=provider: (
                    verify_snapshot(
                        candidate,
                        box,
                        ledger,
                        provider,
                        identity,
                        checkpoint=checkpoint,
                        now=now,
                    )
                )
            )
            schedule.environment = lambda: "staging"
            schedule.health_probe = lambda _: GuardedHealth()
            plan = directory / "schedule.json"
            plan.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "environment": "staging",
                        "wav_sources": ["gateway"],
                        "snapshot_id": 3,
                    }
                )
            )
            plan.chmod(0o600)
            if name == "pending":
                result = schedule.tick(plan, clock=lambda: now)
                receipt = ledger.get(candidate.identity)
                assert (
                    result["deferred"] == 1
                    and result["verified"] == result["evicted"] == 0
                )
                assert (
                    receipt["state"] == "published"
                    and "first_verified_at" not in receipt
                )
                assert box.gets == 0
                report["cases"].append(
                    {
                        "case": "pending_retained_without_false_verification",
                        "passed": True,
                    }
                )
                selected[0] = now
                result = schedule.tick(plan, clock=lambda: now)
                assert (
                    result["verified"] == 1
                    and ledger.get(candidate.identity)["first_verified_at"]
                    == now.isoformat()
                )
                assert box.gets == 2
                report["cases"].append(
                    {"case": "covering_snapshot_verifies_full_contents", "passed": True}
                )
            else:
                selected[0] = now + timedelta(seconds=1) if name == "future" else now
                if name == "corrupt":
                    receipt = ledger.get(candidate.identity)
                    (box.base / receipt["files"][0]["key"]).write_bytes(b"corrupt")
                try:
                    schedule.tick(plan, clock=lambda: now)
                    raise AssertionError("invalid_snapshot_not_blocked")
                except ArchiveBlocked as error:
                    assert not isinstance(error, SnapshotPending)
                    assert ledger.get(candidate.identity)["state"] == "published"
                report["cases"].append(
                    {"case": name + "_snapshot_still_blocks", "passed": True}
                )
            ledger.close()
            ledger = None
        checkpoint()
        report["runtime_sha256"] = runtime.runtime_sha256()
        report["passed"] = len(report["cases"]) == 4
    except Exception as error:  # noqa: BLE001 - Only a sanitized assertion result may leave this probe.
        report["failure_type"] = type(error).__name__
    finally:
        if ledger is not None:
            ledger.close()
        health.close()
        report["seconds"] = round(time.monotonic() - began, 3)
        report["resources"] = read_cgroup()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
