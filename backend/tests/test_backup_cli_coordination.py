import json
import sys
from contextlib import nullcontext

import pytest

from app.tiered_archive import __main__ as cli
from app.tiered_archive import execution


@pytest.mark.parametrize("source", ["gateway", "direct"])
@pytest.mark.parametrize("backup_format", [
    "schema-master-v2", "postgres-full-v1", "schema-master-provider-v1",
    "schema-master-provider-sql-audit-v3",
])
def test_backup_cli_orders_matching_catalog_and_source_lease(
    tmp_path, monkeypatch, capsys, source, backup_format
):
    events = []
    active = False

    def leased(callback, *, job_kind):
        nonlocal active
        assert job_kind == "catalog"
        events.append("lease-open")
        active = True
        try:
            return callback()
        finally:
            active = False
            events.append("lease-close")

    def backup(actual_source, *, backup_format: str, coordination_kind):
        assert active and actual_source == source and coordination_kind == "catalog"
        events.append(backup_format)
        return {"backup": "test-result"}

    monkeypatch.setattr(sys, "argv", ["tiered-archive", "backup", "--source", source,
                                      "--format", backup_format])
    monkeypatch.setattr(cli.runtime, "state_root", lambda: tmp_path)
    monkeypatch.setattr(execution, "bounded_job", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(cli, "with_source_catalogue_lease", leased)
    monkeypatch.setattr(cli.runtime, "backup", backup)
    cli.main()
    assert events == ["lease-open", backup_format, "lease-close"]
    assert json.loads(capsys.readouterr().out) == {"backup": "test-result"}
