"""A legacy multi-image OCI index must preserve every pinned image and layer."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "image_index",
    Path(__file__).resolve().parents[3] / "deploy/archive-server/normalize-image-index.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bundle(path):
    entries, manifest, expected = {}, [], []
    for text in (b"first synthetic layer", b"second synthetic layer"):
        layer = hashlib.sha256(text).hexdigest()
        config = json.dumps({"rootfs": {"diff_ids": ["sha256:" + layer]}}).encode()
        identity = hashlib.sha256(config).hexdigest()
        name = "blobs/sha256/" + identity
        entries[name], entries["blobs/sha256/" + layer] = config, text
        manifest.append({"Config": name, "RepoTags": None, "Layers": ["blobs/sha256/" + layer]})
        expected.append("sha256:" + identity)
    entries["manifest.json"] = json.dumps(manifest).encode()
    entries["index.json"] = b'{"manifests":[]}'
    with tarfile.open(path, "w") as output:
        for name, body in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            output.addfile(member, io.BytesIO(body))
    return entries, expected


def test_every_image_indexed_without_changing_blobs(tmp_path):
    original, target = tmp_path / "original.tar", tmp_path / "normalized.tar"
    entries, expected = bundle(original)
    before = original.read_bytes()
    assert MODULE.complete_index(original, target, expected) == 2
    assert original.read_bytes() == before
    with tarfile.open(target) as archive:
        index = json.load(archive.extractfile("index.json"))
        assert len(index["manifests"]) == 2
        for name, body in entries.items():
            if name != "index.json":
                assert archive.extractfile(name).read() == body
        assert target.stat().st_mode & 0o077 == 0


def test_unknown_image_rejected_before_output(tmp_path):
    original, target = tmp_path / "original.tar", tmp_path / "normalized.tar"
    _, expected = bundle(original)
    with pytest.raises(AssertionError):
        MODULE.complete_index(original, target, expected[:1])
    assert not target.exists()
