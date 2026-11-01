"""The worker announces readiness only under both live lease callbacks."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive import coordination, remote_lease
from app.tiered_archive.managed_snapshot import _root_handoff_pointer


def test_ready_marker_is_pid_bound_and_existing_pointer_is_reusable(tmp_path, monkeypatch):
    pointer = tmp_path / "ready.json"
    waiting = tmp_path / "waiting.json"
    epoch = "a" * 32
    evidence = tmp_path / "handoff.json"
    value = {"schema": 1, "scope_epoch": epoch, "path": str(evidence), "sha256": "b" * 64}
    monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_READY", str(pointer))
    monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_HANDOFF_WAITING", str(waiting))
    monkeypatch.setenv("INVOCATION_ID", "c" * 32)
    calls = []
    monkeypatch.setattr(remote_lease, "require_bound_source_lease", lambda: calls.append("source-bound"))
    monkeypatch.setattr(coordination, "bound_job_lock", lambda: (tmp_path / "jobs.lock", SimpleNamespace(st_dev=2, st_ino=3)))
    real_stat, real_text = Path.stat, Path.read_text
    class RootStat:
        def __init__(self, inner): self.inner = inner
        @property
        def st_uid(self): return 0
        def __getattr__(self, name): return getattr(self.inner, name)
    monkeypatch.setattr(Path, "stat", lambda path, *args, **kw: RootStat(real_stat(path, *args, **kw)))
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kw: "23 (python) " + " ".join(["0"] * 19 + ["555"])
                        if str(path) == "/proc/self/stat" else real_text(path, *args, **kw))
    def source_check():
        calls.append("source-check")
        if not pointer.exists():
            pointer.write_text(json.dumps(value))
            pointer.chmod(0o400)
    monkeypatch.setattr(remote_lease, "source_checkpoint", source_check)
    assert _root_handoff_pointer(epoch) == (evidence, "b" * 64)
    marker = json.loads(waiting.read_text())
    assert marker["scope_epoch"] == epoch and marker["start_ticks"] == 555 and marker["lock_inode"] == 3
    assert calls == ["source-bound", "source-check"]
    assert _root_handoff_pointer(epoch) == (evidence, "b" * 64)
    assert calls == ["source-bound", "source-check"]
