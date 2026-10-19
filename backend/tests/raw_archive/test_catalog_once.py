import json
from datetime import UTC, datetime, timedelta

from app.raw_archive import catalog_once, recovery


def configure(tmp_path, monkeypatch, deadline):
    monkeypatch.setenv("RAW_ARCHIVE_PREPARATION_DEADLINE", deadline.isoformat())
    monkeypatch.setattr(
        "sys.argv",
        [
            "catalog-once",
            "--output",
            str(tmp_path / "backups"),
            "--report",
            str(tmp_path / "catalog-published.json"),
        ],
    )


def test_expiration_does_not_start_backup_or_claim_success(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, datetime.now(UTC) - timedelta(seconds=1))
    monkeypatch.setattr(
        recovery, "entrypoint", lambda: (_ for _ in ()).throw(AssertionError("must not run"))
    )
    assert catalog_once.main() == 0
    report = json.loads((tmp_path / "catalog-expired.json").read_text())
    assert not report["complete"] and not (tmp_path / "catalog-published.json").exists()


def test_resource_pause_is_retryable_and_does_not_claim_publication(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, datetime.now(UTC) + timedelta(hours=24))

    def paused():
        print(json.dumps({"status": "paused", "deleted_files": 0}))
        return 75

    monkeypatch.setattr(recovery, "entrypoint", paused)
    assert catalog_once.main() == 75
    assert not (tmp_path / "catalog-published.json").exists()
