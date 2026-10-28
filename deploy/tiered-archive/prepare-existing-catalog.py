"""Prepare genuine existing SQL proofs in a private ledger; no operational writes."""

import json
import os
import runpy
import sqlite3
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1])
STATE = ROOT / "state"
CREDENTIALS = Path(os.environ["CREDENTIALS_DIRECTORY"])
PROFILE = json.loads((CREDENTIALS / "profile").read_text())
os.environ.update(PROFILE)
sys.path.insert(0, PROFILE["PYTHONPATH"])

from app.raw_archive.health import HealthProbe
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.storage import StorageBox
from app.tiered_archive import codec, parquet, runtime
from app.tiered_archive.latency import LatencyGuard
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import candidate_json, publish, verify_snapshot
from app.tiered_archive.resources import read_cgroup, reasons
from app.tiered_archive.source import Source
from app.tiered_archive.storage import Store
from sqlalchemy import create_engine, text


def main():
    package = json.loads((ROOT / "scope.json").read_text())
    snapshot = package["snapshot"]
    assert snapshot["id"] == 1532426 and snapshot["storage_box"] == 658321
    assert (
        package["historical_restore_proof_sha256"]
        == "a920268d0ea7a88731b740da388aefc65a3af9dfde9e3ad9480e3bb3b331c471"
    )
    assert (
        len(package["archives"]) == 7
        and sum(a["rows"] for a in package["archives"]) == 46
    )
    assert PROFILE["RAW_ARCHIVE_ENVIRONMENT"] == "staging"
    baseline = CREDENTIALS / "baseline"
    urls = [r["url"] for r in json.loads(baseline.read_text())["endpoints"]]
    guard = LatencyGuard(
        baseline, checksum(baseline), urls, STATE, STATE / "control", "copy"
    )
    health = HealthProbe(
        urls,
        [STATE],
        1024,
        min(2, 0.5 * (os.cpu_count() or 1)),
        on_sample=guard.observe,
    )
    started = time.monotonic()
    engines = {}
    ledger = transport = None
    stage = "initial"
    report = {
        "passed": False,
        "source_writes": 0,
        "box_writes": 0,
        "operational_ledger_writes": 0,
        "services_started": False,
        "sql_archives": [],
        "provider_validation": "operator_GET_before_and_after_required",
    }

    def checkpoint():
        if time.monotonic() - started > 210 or not health() or reasons(read_cgroup()):
            raise ArchiveBlocked("resource_or_health_pause")

    try:
        checkpoint()
        assert (
            runtime.runtime_sha256()
            == "cdc65fbaf137e8e244d4e760b7de62a13970dd8b6556b80be8e8063b5b6d6697"
        )
        ready = STATE / "prepared"
        ready.mkdir(mode=0o700)
        shadow = ready / "catalog"
        shadow.mkdir(mode=0o700)
        with sqlite3.connect(
            (CREDENTIALS / "ledger").as_uri() + "?mode=ro&immutable=1", uri=True
        ) as source:
            with sqlite3.connect(shadow / "tiered.sqlite3") as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            target.close()
        ledger = Ledger(shadow)
        assert ledger.status() == {"published": 2}
        previous = list(ledger.db.execute("SELECT id,receipt FROM bundle ORDER BY id"))
        previous_events = list(
            ledger.db.execute("SELECT * FROM event ORDER BY ordinal")
        )
        allowed = {r["key"]: r for r in package["files"]}
        transport = StorageBox(
            host="u676312.your-storagebox.de",
            user="u676312",
            key=CREDENTIALS / "sftp-key",
            known_hosts=CREDENTIALS / "sftp-hosts",
            root="greenmind-raw",
            kilobits=2048,
        )
        raw_store = Store(transport)
        current_keys, snapshot_keys = set(), set()

        class ExistingReadOnlyStore:
            identity = raw_store.identity

            def publish(self, key, path):
                # Rebind already published bytes; this adapter has no PUT method.
                expected = allowed[key]
                assert (
                    checksum(path) == expected["sha256"]
                    and path.stat().st_size == expected["bytes"]
                )
                returned = STATE / ("current-" + expected["sha256"])
                self.download(key, returned, expected["bytes"])
                returned.unlink()

            def download(self, key, target, size, *, snapshot=None):
                checkpoint()
                expected = allowed[key]
                assert expected["bytes"] == size and snapshot in (
                    None,
                    package["snapshot"]["name"],
                )
                raw_store.download(key, target, size, snapshot=snapshot)
                assert (
                    checksum(target) == expected["sha256"]
                    and target.stat().st_size == size
                )
                (snapshot_keys if snapshot else current_keys).add(key)

        class OperatorProviderPin:
            # Actual API data, not a fixture. The operator rechecks after this job
            # before any proposed state is allowed to replace operational files.
            def read(self, identity):
                assert identity == package["snapshot"]["id"]
                return dict(package["snapshot"])

        store = ExistingReadOnlyStore()
        assert all(json.loads(r[1])["destination"] == store.identity for r in previous)
        bindings = runtime.bindings_sha256(store)
        for archive in package["archives"]:
            stage = "verify_" + archive["identity"]
            checkpoint()
            manifest_ref = archive["manifest"]
            path = STATE / ("manifest-" + archive["identity"])
            store.download(
                manifest_ref["key"],
                path,
                manifest_ref["bytes"],
                snapshot=snapshot["name"],
            )
            manifest = json.loads(path.read_text())
            candidate = runtime.candidate_from_json(manifest["candidate"])
            assert candidate.identity == manifest["identity"] == archive["identity"]
            assert manifest["files"] == archive["files"] and len(manifest["files"]) == 1
            reference = manifest["files"][0]
            path = STATE / ("data-" + reference["sha256"])
            store.download(
                reference["key"], path, reference["bytes"], snapshot=snapshot["name"]
            )
            restored = list(parquet.read(path, reference, checkpoint=checkpoint))
            if candidate.source not in engines:
                url = PROFILE[
                    "RAW_ARCHIVE_" + candidate.source.upper() + "_DATABASE_URL"
                ]
                assert ":5433/" in url
                engines[candidate.source] = create_engine(
                    url,
                    pool_size=1,
                    max_overflow=0,
                    pool_timeout=5,
                    connect_args={
                        "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c work_mem=4096"
                    },
                )
                with engines[candidate.source].connect() as db:
                    assert (
                        db.execute(text("SELECT current_user")).scalar_one()
                        == "greenmind_archive_staging_ro"
                    )
                    assert (
                        db.execute(text("SHOW transaction_read_only")).scalar_one()
                        == "on"
                    )
            adapter = Source(
                engines[candidate.source],
                candidate.source,
                "staging",
                checkpoint=checkpoint,
            )
            original, flags = adapter.export(
                candidate.dataset,
                candidate.organization,
                candidate.start,
                candidate.end,
            )
            assert not adapter.page_full and codec.encode(restored) == codec.encode(
                original
            )
            recreated = adapter.candidate(
                candidate.dataset,
                candidate.organization,
                candidate.start,
                candidate.end,
                reference,
                flags,
                settled_at=candidate.settled_at,
            )
            assert recreated == candidate and flags["dependencies_complete"] is False
            assert candidate_json(candidate) == manifest["candidate"]
            # This is the actual publication timestamp of the approved 17-file
            # upload, already bound to its independent durable server proof.
            publish(
                candidate,
                [(path, reference)],
                store,
                ledger,
                checkpoint=checkpoint,
                now=datetime.fromisoformat(package["published_at"]),
            )
            verified = verify_snapshot(
                candidate,
                store,
                ledger,
                OperatorProviderPin(),
                snapshot["id"],
                checkpoint=checkpoint,
            )
            report["sql_archives"].append(
                {
                    "identity": candidate.identity,
                    "source": candidate.source,
                    "dataset": candidate.dataset,
                    "organization": candidate.organization,
                    "start": candidate.start.isoformat(),
                    "end": candidate.end.isoformat(),
                    "rows": candidate.rows,
                    "manifest_sha256": manifest_ref["sha256"],
                    "state": verified["state"],
                    "published_at": verified["published_at"],
                    "verified_at": verified["verified_at"],
                    "dependencies_complete": False,
                    "source_matches_current": True,
                    "current_readback_verified": all(
                        r["key"] in current_keys for r in [manifest_ref, reference]
                    ),
                    "snapshot_readback_verified": all(
                        r["key"] in snapshot_keys for r in [manifest_ref, reference]
                    ),
                }
            )
        assert ledger.status() == {"published": 2, "verified": 7}
        assert all(
            ledger.db.execute(
                "SELECT receipt FROM bundle WHERE id=?", (identity,)
            ).fetchone()[0]
            == raw
            for identity, raw in previous
        )
        assert (
            list(ledger.db.execute("SELECT * FROM event ORDER BY ordinal"))[
                : len(previous_events)
            ]
            == previous_events
        )
        report["verified_at"] = datetime.now(UTC).isoformat()
        report["schema"] = 1
        report["environment"] = "staging"
        report["phase"] = "restore_verified_configuration"
        report["runtime_sha256"] = runtime.runtime_sha256()
        report["bindings_sha256"] = bindings
        report["previous_ledger_sha256"] = package["previous_ledger_sha256"]
        report["snapshot"] = snapshot
        report["existing_receipts_preserved"] = True
        report["historical_snapshot_restore_passed"] = True
        report["sql_bundles_added"] = 7
        report["activation_authorized"] = report["deletion_enabled"] = False
        plan = json.loads((ROOT / "schedule.json").read_text())
        for dataset in ("visual_reading", "visual_wave"):
            candidates = [a for a in report["sql_archives"] if a["dataset"] == dataset]
            assert candidates
            task = {
                "dataset": dataset,
                "organization": candidates[0]["organization"],
                "start": min(a["start"] for a in candidates),
            }
            assert not any(t["dataset"] == dataset for t in plan["scopes"])
            plan["scopes"].append(task)
        plan["snapshot_id"] = snapshot["id"]
        report["wav_sources"] = plan["wav_sources"]
        reader = json.loads((CREDENTIALS / "reader-profile").read_text())
        proposed_writer = {**PROFILE, "TIERED_ARCHIVE_READS_ENABLED": "true"}
        proposed_reader = {**reader, "TIERED_ARCHIVE_READS_ENABLED": "true"}
        report["validation"] = runpy.run_path(str(ROOT / "phase-validator.py"))[
            "validate_restored"
        ](proposed_writer, proposed_reader, plan, {**report, "passed": True})
        report["current_files_read_back"] = len(current_keys)
        report["snapshot_files_read_back"] = len(snapshot_keys)
        assert len(current_keys) == len(snapshot_keys) == 14
        checkpoint()
        ledger.close()
        ledger = None
        with sqlite3.connect(shadow / "tiered.sqlite3") as db:
            db.execute("PRAGMA journal_mode=DELETE")
        db.close()
        with sqlite3.connect(
            (shadow / "catalog-read.sqlite3").as_uri() + "?mode=ro&immutable=1",
            uri=True,
        ) as view, sqlite3.connect(
            (shadow / "tiered.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True
        ) as main_db:
            assert list(view.execute("SELECT * FROM bundle ORDER BY id")) == list(
                main_db.execute("SELECT * FROM bundle ORDER BY id")
            )
        view.close()
        main_db.close()
        assert not (shadow / "tiered.sqlite3-wal").exists()
        for filename, body in [
            ("evidence.json", json.dumps({**report, "passed": True}, sort_keys=True)),
            ("schedule.json", json.dumps(plan, sort_keys=True)),
        ]:
            (ready / filename).write_text(body)
            (ready / filename).chmod(0o600)
        report["prepared_files"] = [
            {
                "name": str(path.relative_to(ready)),
                "bytes": path.stat().st_size,
                "sha256": checksum(path),
            }
            for path in sorted(ready.rglob("*"))
            if path.is_file()
        ]
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - Persist sanitized proof before cleanup.
        report["failure"] = {
            "stage": stage,
            "type": type(error).__name__,
            "frames": [
                {"file": Path(f.filename).name, "line": f.lineno}
                for f in traceback.extract_tb(error.__traceback__)
            ],
        }
        if isinstance(error, ArchiveBlocked):
            report["failure"]["reason"] = str(error)
    finally:
        if ledger is not None:
            ledger.close()
        if transport is not None:
            transport.close()
        for engine in engines.values():
            engine.dispose()
        health.close()
        report["seconds"] = round(time.monotonic() - started, 3)
        report["resources"] = read_cgroup()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
