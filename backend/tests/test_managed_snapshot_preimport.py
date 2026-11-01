import hashlib
import json
import types
from pathlib import Path

import pytest

from app.raw_archive import snapshot_preimport_launcher as launcher
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import check_loaded_function_code


def test_loaded_code_object_must_match_pinned_source(tmp_path):
    source = b"def operation():\n    return 17\n"
    path = tmp_path / "consumer.py"
    path.write_bytes(source)
    module = types.ModuleType("consumer")
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 -- fixed local test source
    assert (
        check_loaded_function_code(module, path, source, ["operation"])["operation"]
        == module.operation.__code__
    )
    module.operation = lambda: 18
    with pytest.raises(ArchiveBlocked, match="bytecode differs"):
        check_loaded_function_code(module, path, source, ["operation"])


def test_launcher_rejects_handoff_symlinks_and_late_app_import(tmp_path):
    target = tmp_path / "handoff.json"
    target.write_text("{}")
    link = tmp_path / "handoff-link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="preimport_snapshot_closed"):
        launcher.read_root_handoff(link)
    # These tests already imported app; the real launcher must reject that state
    # before reading any scope or starting a consumer.
    with pytest.raises(ValueError, match="preimport_snapshot_closed"):
        launcher.run("/unavailable", "0" * 64)


def test_preimport_audit_rejects_unlisted_and_pyc(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    backend = root / "backend"
    app = backend / "app"
    app.mkdir(parents=True)
    source = app / "sample.py"
    source.write_text("VALUE = 1\n")
    (backend / "requirements.lock").write_text("a\n")
    (backend / "requirements-archive.lock").write_text("b\n")
    paths = [source, backend / "requirements.lock", backend / "requirements-archive.lock"]
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(backend).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    runtime_sha = digest.hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"commit": "a" * 40, "runtime_sha256": runtime_sha, "files": files})
    )
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    for path in [*paths, manifest]:
        path.chmod(0o444)
    for path in (app, backend, root):
        path.chmod(0o555)
    original_lstat = Path.lstat

    def root_lstat(path):
        info = original_lstat(path)
        return types.SimpleNamespace(st_mode=info.st_mode, st_uid=0)

    def read(path, expected, *, mode=0o400, limit=1024 * 1024):
        body = Path(path).read_bytes()
        launcher.need(len(body) <= limit and hashlib.sha256(body).hexdigest() == expected)
        return body

    monkeypatch.setattr(Path, "lstat", root_lstat)
    monkeypatch.setattr(launcher, "root_file", read)
    launcher.audit_runtime(root, manifest_sha, runtime_sha)
    app.chmod(0o755)
    (app / "rogue.py").write_text("bad\n")
    (app / "rogue.py").chmod(0o444)
    app.chmod(0o555)
    with pytest.raises(ValueError, match="preimport_snapshot_closed"):
        launcher.audit_runtime(root, manifest_sha, runtime_sha)
    app.chmod(0o755)
    (app / "rogue.py").unlink()
    (app / "rogue.pyc").write_bytes(b"bad")
    (app / "rogue.pyc").chmod(0o444)
    app.chmod(0o555)
    with pytest.raises(ValueError, match="preimport_snapshot_closed"):
        launcher.audit_runtime(root, manifest_sha, runtime_sha)
    app.chmod(0o755)
    backend.chmod(0o755)
    root.chmod(0o755)
