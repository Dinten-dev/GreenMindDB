"""Read-only deletion rehearsal. Outputs a draft, never executes removal."""

import argparse
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .deletion import check_quarantine, eligible_after, proposal, verify_preparation_evidence
from .policy import ArchiveBlocked, Recording
from .recovery import private_directory, recovery_index


class ReadLedger:
    def __init__(self, database):
        self.database = database

    def load(self, recording):
        row = self.database.execute(
            "SELECT state,receipt FROM archive WHERE id=?", (recording.archive_id,)
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None


def main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from .catalog import DirectCatalog, GatewayCatalog
    from .health import SafetyPause
    from .runner import destination_from_environment, health_probe, source_settings
    from .worker import configuration

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish-index", action="store_true")
    parser.add_argument(
        "--verify-proof",
        type=Path,
        help="Rehearse an unsigned technical proposal; never authorizes or executes removal",
    )
    parser.add_argument("--proof-sha256", default="")
    args = parser.parse_args()
    config = replace(configuration(), enabled=True, delete_enabled=False, reads_accepted=False)
    if args.verify_proof:
        config = replace(
            config,
            deletion_approval_file=args.verify_proof,
            deletion_approval_sha256=args.proof_sha256,
        )
    config.guard()
    private_directory(args.output)
    healthy = health_probe()
    now, started = datetime.now(UTC), time.monotonic()
    report = {
        "schema": 1,
        "at": now.isoformat(),
        "deleted": 0,
        "approved": False,
        "eligible": [],
        "blocked": {},
        "maximum_files": 10,
        "maximum_bytes": 5 * 1024**2,
    }
    engines, catalogs, settings, clients = {}, {}, {}, []
    selected, total = [], 0
    try:
        for kind in ("gateway", "direct"):
            values = source_settings(kind, config.namespace)
            settings[kind] = values
            engine = create_engine(
                values["database"],
                pool_size=1,
                max_overflow=0,
                connect_args={
                    "connect_timeout": 5,
                    "options": "-c default_transaction_read_only=on "
                    "-c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=8192",
                },
            )
            engines[kind] = engine
            sessions = sessionmaker(bind=engine)
            catalogs[kind] = (
                GatewayCatalog(sessions)
                if kind == "gateway"
                else DirectCatalog(sessions, values["bucket"])
            )
        with closing(
            sqlite3.connect(
                (config.root / "archive.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2
            )
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA cache_size=-1024")
            ledger = ReadLedger(db)
            # Five slots per pipeline prevent a Gateway-only pilot. Discovery
            # is deliberately bounded; this is not an all-files reconciliation.
            if args.verify_proof:
                reviewed = proposal(config, now)
                query = db.execute(
                    "SELECT receipt FROM archive WHERE state='verified' AND id IN ("
                    + ",".join("?" for _ in reviewed["candidates"])
                    + ") ORDER BY id",
                    tuple(reviewed["candidates"]),
                )
            else:
                query = db.execute("""SELECT receipt FROM (
                SELECT receipt FROM archive WHERE state='verified'
                AND json_extract(receipt,'$.recording.kind')='gateway' LIMIT 500)
                UNION ALL SELECT receipt FROM (
                SELECT receipt FROM archive WHERE state='verified'
                AND json_extract(receipt,'$.recording.kind')='direct' LIMIT 500)""")
            per_kind = {"gateway": 0, "direct": 0}
            for (encoded,) in query:
                if time.monotonic() - started > 60:
                    raise ArchiveBlocked("Preflight reached its time budget")
                receipt = json.loads(encoded)
                record = Recording.from_receipt(receipt["recording"])
                if not args.verify_proof and per_kind.get(record.kind, 5) >= 5:
                    continue
                if eligible_after(record, receipt) > now:
                    continue
                if not healthy():
                    raise SafetyPause("Preflight paused for receiver headroom")
                try:
                    check_quarantine(config, record)
                    catalog = catalogs[record.kind]
                    if record.kind == "gateway":
                        current = catalog.inspect(uuid.UUID(record.identity))
                    else:
                        segment, revision = record.identity.split(":")
                        current = catalog.inspect(segment, int(revision))
                    if isinstance(current, list):
                        current = next(
                            item
                            for item in current
                            if item.content_identity() == record.content_identity()
                        )
                    if current.content_identity() != record.content_identity():
                        raise ArchiveBlocked("Source identity changed")
                    if len(selected) >= 10 or total + current.size > 5 * 1024**2:
                        break
                    selected.append(current)
                    per_kind[current.kind] += 1
                    total += current.size
                    report["eligible"].append(current.archive_id)
                except Exception as error:
                    name = type(error).__name__
                    report["blocked"][name] = report["blocked"].get(name, 0) + 1
            if selected:
                destination = destination_from_environment()
                raw, digest = recovery_index(selected, ledger, config.namespace, destination)
                path = args.output / (digest + ".json")
                if not path.exists():
                    with path.open("xb") as body:
                        os.chmod(path, 0o600)
                        body.write(raw)
                        body.flush()
                        os.fsync(body.fileno())
                key = f"{config.namespace}/recovery/{digest}.json"
                report["recovery_index"] = {"key": key, "sha256": digest, "size": len(raw)}
                if args.publish_index:
                    if not healthy():
                        raise SafetyPause("Index publication paused for receiver headroom")
                    destination.publish_recovery(path, key)
                    report["recovery_index"]["published_verified"] = True
                if args.verify_proof:
                    report["proof_passed"] = []
                    reviewed = proposal(config, now)
                    if set(reviewed["candidates"]) != {item.archive_id for item in selected}:
                        raise ArchiveBlocked("Rehearsal must cover the entire reviewed pilot")
                    import boto3
                    from botocore.config import Config as BotoConfig

                    from .diagnostics import client_from_environment
                    from .storage import S3Source

                    diagnostic = client_from_environment()
                    clients.append(diagnostic)
                    sources = {}
                    for kind, values in settings.items():
                        client = boto3.client(
                            "s3",
                            endpoint_url=values["endpoint"],
                            aws_access_key_id=values["access"],
                            aws_secret_access_key=values["secret"],
                            config=BotoConfig(
                                connect_timeout=5,
                                read_timeout=15,
                                retries={"total_max_attempts": 2},
                                s3={"addressing_style": "path"},
                            ),
                        )
                        clients.append(client)
                        sources[kind] = S3Source(
                            client,
                            diagnostic_client=diagnostic,
                            allow_legacy_null=reviewed.get("legacy_null_accepted") is True,
                        )
                    for recording in selected:
                        if not healthy():
                            raise SafetyPause("Proof readback paused for receiver headroom")
                        receipt = dict(ledger.load(recording)[1])
                        receipt.setdefault("first_verified_at", receipt.get("verified_at"))
                        try:
                            verify_preparation_evidence(
                                config, recording, receipt, destination, now
                            )
                            if (
                                sources[recording.kind].deletion_target(
                                    recording, receipt["snapshot"]
                                )
                                is None
                            ):
                                raise ArchiveBlocked("Proposed pilot source is already absent")
                            report["proof_passed"].append(recording.archive_id)
                        except ArchiveBlocked as error:
                            report.setdefault("proof_blocked", []).append(
                                {"id": recording.archive_id, "reason": str(error)}
                            )
    except (ArchiveBlocked, SafetyPause) as error:
        report["pause_reason"] = str(error)
    finally:
        for engine in engines.values():
            engine.dispose()
        for client in clients:
            client.close()
    ready = bool(
        args.verify_proof
        and selected
        and not report.get("proof_blocked")
        and set(report.get("proof_passed", [])) == {item.archive_id for item in selected}
        and "pause_reason" not in report
    )
    report["status"] = (
        "paused"
        if "pause_reason" in report
        else "READY_FOR_PILOT_APPROVAL"
        if ready
        else "draft_only"
        if selected
        else "no_eligible_candidates"
    )
    report["delete_enabled"] = False
    report["remaining_gates"] = (
        ["explicit_deletion_authorization"]
        if ready
        else [
            "bucket_versioning",
            "snapshot_readback",
            "catalog_restore",
            "reviewed_manifest",
            "explicit_deletion_authorization",
        ]
    )
    path = args.output / (now.strftime("%Y%m%dT%H%M%S") + "-preflight.json")
    with path.open("x") as body:
        os.chmod(path, 0o600)
        json.dump(report, body, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
