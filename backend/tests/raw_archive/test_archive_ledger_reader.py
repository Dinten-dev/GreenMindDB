"""The archive reader never needs journal-directory write permission."""

import os
import sqlite3
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked, Ledger


def test_source_journal_stays_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("RAW_ARCHIVE_LEDGER_JOURNAL_MODE", raising=False)
    ledger = Ledger(tmp_path)
    assert ledger.db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    ledger.close()


@pytest.mark.parametrize(
    "mode,role,host",
    [
        ("OFF", "archive", "ubuntu-4gb-fsn1-1"),
        ("DELETE", "source", "ubuntu-4gb-fsn1-1"),
        ("DELETE", "archive", "Biolingo"),
    ],
)
def test_invalid_opt_in_opens_no_journal(tmp_path, monkeypatch, mode, role, host):
    monkeypatch.setenv("RAW_ARCHIVE_LEDGER_JOURNAL_MODE", mode)
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", role)
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(nodename=host))
    with pytest.raises(ArchiveBlocked):
        Ledger(tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_readonly_reader_sees_only_committed_archive_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("RAW_ARCHIVE_LEDGER_JOURNAL_MODE", "DELETE")
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(nodename="ubuntu-4gb-fsn1-1"))
    ledger = Ledger(tmp_path)
    assert ledger.path.stat().st_mode & 0o777 == 0o640
    reader = sqlite3.connect(ledger.path.as_uri() + "?mode=ro", uri=True, timeout=2)
    ledger.db.execute("INSERT INTO archive VALUES ('one','verified','{}')")
    assert reader.execute("SELECT count(*) FROM archive").fetchone() == (0,)
    ledger.db.commit()
    assert reader.execute("SELECT count(*) FROM archive").fetchone() == (1,)
    ledger.db.execute("INSERT INTO archive VALUES ('two','verified','{}')")
    ledger.db.rollback()
    assert reader.execute("SELECT count(*) FROM archive").fetchone() == (1,)
    reader.close()
    ledger.close()
    assert not list(tmp_path.glob("archive.sqlite3-*"))
