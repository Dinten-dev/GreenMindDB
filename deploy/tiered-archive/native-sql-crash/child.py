"""Crash only an exact synthetic SQL fixture; never consume operational profiles."""

import json
import os
import re
import sys
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import codec, runtime
from app.tiered_archive.deletion import evict
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.source import Source
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool


def main():
    root, case, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    assert case.parent == root / "state/cases" and not case.is_symlink()
    assert re.fullmatch(r"[a-f0-9]{32}", case.name)
    context = codec.decode((case / "context.json").read_bytes())
    binding = json.loads((root / "test-binding.json").read_text())
    name = binding["database"]
    assert re.fullmatch(r"gm_tiered_accept_[a-f0-9]{24}", name)
    assert context["database"] == name and context["test_fixture"] is True
    candidate = runtime.candidate_from_json(context["candidate"])
    assert candidate.environment == "staging"
    assert candidate.organization == "11111111-1111-4111-8111-111111111111"
    assert context["bindings_sha256"] == "b" * 64
    assert context["runtime_sha256"] == runtime.runtime_sha256()
    admin = json.loads(
        (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "test-db-admin").read_text()
    )
    # Acceptance is counterfactual test data confined to this child process.
    # No operational env file, activation file or acceptance proof is modified.
    os.environ["TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED"] = "true"
    assert all(
        os.getenv(key, "false") == "false"
        for key in (
            "TIERED_ARCHIVE_DELETE_ENABLED",
            "RAW_ARCHIVE_DELETE_ENABLED",
            "RETENTION_ENABLED",
            "DIRECT_RETENTION_ENABLED",
        )
    )
    engine = create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=admin["user"],
            password=admin["password"],
            host="127.0.0.1",
            port=5433,
            database=name,
        ),
        poolclass=NullPool,
        connect_args={
            "application_name": "gm-crash-" + case.name,
            "options": "-c statement_timeout=3000 -c lock_timeout=150 -c work_mem=4096",
        },
    )
    with engine.connect() as db:
        assert db.execute(text("SELECT current_database()")).scalar_one() == name
        assert (
            db.execute(
                text(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=:name"
                ),
                {"name": name},
            ).scalar_one()
            == "greenmind-synthetic-test:" + binding["token"]
        )

    def crash(phase, code):
        marker = case / "crash.json"
        with marker.open("x") as stream:
            json.dump({"phase": phase, "database": name, "test_fixture": True}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(code)  # Deliberately bypass Python/SQLAlchemy finalizers.

    class CrashLedger(Ledger):
        def save(self, candidate, state, data, now=None):
            if state == "evicted" and mode == "after_sql_commit":
                crash("source_committed_local_completion_missing", 73)
            receipt = super().save(candidate, state, data, now)
            if state == "deleting" and mode == "before_source_delete":
                crash("durable_intent_before_source_delete", 71)
            if state == "evicted" and mode == "after_local_completion":
                crash("source_and_local_completion_committed", 74)
            return receipt

    class CrashSource(Source):
        def delete(self, candidate):
            super().delete(candidate)
            if mode == "before_sql_commit":
                crash("source_delete_executed_but_uncommitted", 72)

    ledger = CrashLedger(case / "ledger")
    source = CrashSource(engine, candidate.source, "staging", checkpoint=lambda: None)
    source.expected = context["expected"]
    try:
        receipt = evict(
            candidate,
            source,
            ledger,
            authorization_path=case / "fixture-permit.json",
            authorization_sha256=checksum(case / "fixture-permit.json"),
            runtime_sha256=context["runtime_sha256"],
            bindings_sha256=context["bindings_sha256"],
            checkpoint=lambda: None,
            clock=lambda: context["now"],
        )
        print(json.dumps({"state": receipt["state"], "test_fixture": True}))
    except ArchiveBlocked as error:
        print(json.dumps({"blocked": str(error), "test_fixture": True}))
    finally:
        ledger.close()
        engine.dispose()


if __name__ == "__main__":
    main()
