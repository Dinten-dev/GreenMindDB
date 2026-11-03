"""Owner-only generated-copy reaping, also used by the root Docker dispatcher."""

import argparse
import hashlib
import json
from pathlib import Path

from app.visualization.archive_export_storage import ExportStorageError, ExportStore
from app.visualization.archive_jobs import connect


def adopt_reviewed(store, db, manifest):
    """Explicit offline provenance approval; never guess from a filename."""
    rows = json.loads(manifest.read_text())
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ExportStorageError("Bounded reviewed inventory required")
    if len({row["name"] for row in rows if isinstance(row, dict) and "name" in row}) != len(rows):
        raise ExportStorageError("Unique reviewed copy names required")
    approved = []
    for row in rows:
        if (
            set(row) != {"name", "size", "sha256", "generated_export_copy"}
            or row["generated_export_copy"] is not True
        ):
            raise ExportStorageError("Explicit generated-copy approval required")
        from app.visualization.archive_export_storage import COPY

        if not COPY.fullmatch(row["name"]):
            raise ExportStorageError("Invalid approved copy name")
        path = store.path / row["name"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"]:
            raise ExportStorageError("Reviewed copy changed")
        digest = hashlib.sha256()
        with path.open("rb") as body:
            for block in iter(lambda: body.read(1024**2), b""):
                digest.update(block)
        if digest.hexdigest() != row["sha256"]:
            raise ExportStorageError("Reviewed copy changed")
        approved.append(row["name"])
    for name in approved:
        store.register(db, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--adopt-reviewed", type=Path)
    args = parser.parse_args()
    with ExportStore(args.state) as store, store.locked(), connect(args.state) as db:
        if args.adopt_reviewed:
            adopt_reviewed(store, db, args.adopt_reviewed)
        deleted = store.reap(db)
    print(json.dumps({"deleted_export_copies": deleted}))


if __name__ == "__main__":
    main()
