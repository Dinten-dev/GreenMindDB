"""Complete Docker's legacy multi-image OCI index without changing image contents."""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def complete_index(original, destination, expected):
    assert not destination.exists(), "Preserve prior image evidence"
    with tarfile.open(original) as source:
        legacy = json.load(source.extractfile("manifest.json"))
        index = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [],
        }
        added, identities = {}, set()
        for entry in legacy:
            config = source.extractfile(entry["Config"]).read()
            identity = "sha256:" + hashlib.sha256(config).hexdigest()
            assert identity in expected and identity not in identities
            identities.add(identity)
            layers = ["sha256:" + Path(v).name for v in entry["Layers"]]
            assert layers == json.loads(config)["rootfs"]["diff_ids"]
            image = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": identity,
                    "size": len(config),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar",
                        "digest": digest,
                        "size": source.getmember(name).size,
                    }
                    for name, digest in zip(entry["Layers"], layers, strict=True)
                ],
            }
            body = encoded(image)
            digest = hashlib.sha256(body).hexdigest()
            name = "blobs/sha256/" + digest
            tag = "greenmind-staging-import:" + identity.split(":")[1][:12]
            index["manifests"].append(
                {
                    "mediaType": image["mediaType"],
                    "digest": "sha256:" + digest,
                    "size": len(body),
                    "annotations": {
                        "io.containerd.image.name": "docker.io/library/" + tag,
                        "org.opencontainers.image.ref.name": tag,
                    },
                }
            )
            if name in source.getnames():
                assert source.extractfile(name).read() == body
            else:
                added[name] = body
        assert identities == set(expected), "Missing pinned images"
        with destination.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            with tarfile.open(fileobj=stream, mode="w") as target:
                for member in source:
                    if member.name == "index.json":
                        continue
                    target.addfile(
                        member, source.extractfile(member) if member.isfile() else None
                    )
                for name, body in {**added, "index.json": encoded(index)}.items():
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(body), 0o644
                    target.addfile(member, io.BytesIO(body))
            stream.flush()
            os.fsync(stream.fileno())
    return len(identities)


def main():
    root = Path("/home/traver/greenmind-archive-rollout-20261008")
    expected = json.loads((root / "staging-manifest-v2.json").read_text())["images"]
    from importlib.machinery import SourceFileLoader

    installer = SourceFileLoader(
        "staging_installer", str(root / "install-staging.py")
    ).load_module()
    proof = json.loads((root / "images-evidence.json").read_text())
    original = root / "staging-images.tar"
    assert (
        installer.digest(original) == proof["sha256"]
        and original.stat().st_size == proof["bytes"]
    )
    output = root / "staging-images-complete-index.tar"
    count = complete_index(original, output, expected)
    print(
        json.dumps(
            {
                "images": count,
                "source_sha256": proof["sha256"],
                "transport_sha256": installer.digest(output),
                "image_contents_changed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
