"""One bounded original-WAV export; run under an isolated systemd service."""

import hashlib
import json
import os
import shutil
import time
import uuid
import zipfile
from contextlib import nullcontext
from pathlib import Path

from app.direct.config import DirectSettings
from app.direct.storage import ArtifactStore
from app.raw_archive import reader as archive_reader
from app.services.wav_service import _get_s3_client
from app.visualization.archive_export_storage import ExportStore, reap_exports
from app.visualization.archive_jobs import EXPIRES, PART_BYTES, connect, root


class CapacityPause(RuntimeError):
    """Leave a verified export queued while ingestion needs host resources."""


def headroom(path):
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            values["available_kib"] = int(line.split()[1])
    usage = shutil.disk_usage(path)
    if (
        values["available_kib"] < 512 * 1024
        or os.getloadavg()[0] > 2.4
        or usage.free < max(2 * 1024**3, usage.total * 0.05)
    ):
        raise CapacityPause("Server braucht Vorrang; Export später wiederholen")


def source_for(item, gateway, direct):
    if item["kind"] == "gateway":
        response = archive_reader.get_object(
            gateway, Bucket=item["bucket"], Key=item["key"], kind="gateway"
        )
        return response["Body"]
    if direct.client is None or item["bucket"] != direct.settings.s3_bucket:
        raise RuntimeError("Direct-Quelle nicht verfügbar")
    response = archive_reader.get_object(
        direct.client, Bucket=item["bucket"], Key=item["key"], kind="direct"
    )
    return response["Body"]


class ExportCancelled(RuntimeError):
    pass


def check_cancelled(db, identity):
    row = db.execute("SELECT status FROM jobs WHERE id=?", (identity,)).fetchone()
    if not row or row["status"] in {"cancelled", "expired"}:
        raise ExportCancelled("Export cancelled")


class BudgetWriter:
    def __init__(self, file, store, db, identity):
        self.file, self.store, self.db, self.identity = file, store, db, identity

    def __getattr__(self, name):
        return getattr(self.file, name)

    def write(self, data):
        with self.store.locked():
            check_cancelled(self.db, self.identity)
            self.store.budget(self.db, growing=self.identity, additional=len(data))
            return self.file.write(data)


def make_parts(path, job, db, *, store=None):
    if store is not None:
        return _make_parts(path, job, db, store)
    with ExportStore(path) as store:
        with store.locked():
            lock = store.acquire_job(job["id"])
        if lock is None:
            raise RuntimeError("Export already active")
        try:
            return _make_parts(path, job, db, store)
        finally:
            os.close(lock)


def _make_parts(path, job, db, store):
    from app.raw_archive.coordination import yield_requested
    from app.visualization.archive_export_storage import part_names

    items = json.loads(job["items"])
    names = json.loads(job["parts"])
    part_names(job)
    created = [path / name for name in names]
    active = None
    output = None
    zipper = None
    manifest = []
    in_part = 0
    part = len(created)
    verified = 0
    dirty_zip = False
    job_started = time.monotonic()

    def finish():
        nonlocal zipper, active, output, part, manifest, in_part
        if zipper is None:
            return
        zipper.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zipper.close()
        zipper = None
        output.flush()
        os.fsync(output.fileno())
        output.close()
        output = None
        finished = active.with_suffix(".zip")
        with store.locked():
            check_cancelled(db, job["id"])
            os.link(active.name, finished.name, src_dir_fd=store.fd, dst_dir_fd=store.fd)
            os.fsync(store.fd)
            created.append(finished)
            with db:
                db.execute(
                    "UPDATE jobs SET parts=?,completed=? WHERE id=?",
                    (json.dumps([p.name for p in created]), verified, job["id"]),
                )
            store.unlink(active.name)
        active = None
        part += 1
        manifest = []
        in_part = 0

    try:
        completed = saved_progress(created, items)
        if completed != job["completed"]:
            raise RuntimeError("Gesicherter Exportfortschritt stimmt nicht überein")
        verified = completed
        gateway = _get_s3_client()
        direct = ArtifactStore(DirectSettings())
        for index, item in enumerate(items[completed:], start=completed):
            check_cancelled(db, job["id"])
            if time.monotonic() - job_started >= 60 and yield_requested(("copy", "catalog")):
                raise CapacityPause("waiting archive job")
            headroom(path)
            if zipper is None or (in_part and in_part + item["size"] > PART_BYTES):
                finish()
                active = path / f"{job['id']}-{part:03d}-{uuid.uuid4().hex}.part"
                output = active.open("xb", buffering=0)
                zipper = zipfile.ZipFile(
                    BudgetWriter(output, store, db, job["id"]),
                    "w",
                    compression=zipfile.ZIP_STORED,
                    allowZip64=True,
                )
                manifest = []
                in_part = 0
            digest = hashlib.sha256()
            count = 0
            start = time.monotonic()
            spool = path / f"{job['id']}-{uuid.uuid4().hex}.raw-part"
            body = None
            try:
                with archive_reader.export_scratch(store.scratch_path(job["id"])):
                    body = source_for(item, gateway, direct)
                with spool.open("xb", buffering=0) as raw:
                    target = BudgetWriter(raw, store, db, job["id"])
                    while block := body.read(64 * 1024):
                        headroom(path)
                        check_cancelled(db, job["id"])
                        count += len(block)
                        if count > item["size"]:
                            raise RuntimeError("Original größer als Katalogangabe")
                        digest.update(block)
                        target.write(block)
                        time.sleep(max(0, count / 1048576 - (time.monotonic() - start)))
                    target.flush()
                    os.fsync(target.fileno())
                if count != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise RuntimeError(
                        "Original oder Archivkopie stimmt nicht mit Prüfsumme überein"
                    )
                dirty_zip = True
                with (
                    spool.open("rb") as source,
                    zipper.open(item["name"], "w", force_zip64=True) as target,
                ):
                    for block in iter(lambda: source.read(64 * 1024), b""):
                        headroom(path)
                        target.write(block)
                dirty_zip = False
            finally:
                try:
                    if body is not None:
                        body.close()
                finally:
                    with store.locked():
                        store.unlink(spool.name)
                        store.clear_scratch(job["id"])
            manifest.append({k: v for k, v in item.items() if k not in {"key", "bucket"}})
            in_part += count
            verified = index + 1
        finish()
        with store.locked(), db:
            check_cancelled(db, job["id"])
            db.execute("UPDATE jobs SET status='ready' WHERE id=?", (job["id"],))
    except BaseException as error:
        try:
            if isinstance(error, CapacityPause) and manifest and not dirty_zip:
                finish()
            elif zipper is not None:
                zipper.close()
        finally:
            if output is not None:
                output.close()
            with store.locked():
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                keep = part_names(row) if isinstance(error, CapacityPause) else ()
                store.clean_job(row, keep=keep)
                if not isinstance(error, CapacityPause):
                    with db:
                        db.execute(
                            "UPDATE jobs SET parts='[]',completed=0 WHERE id=?", (job["id"],)
                        )
        raise


def saved_progress(parts, items):
    """Only complete, fully decoded matching ZIPs can carry progress forward."""
    completed = 0
    for path in parts:
        headroom(path.parent)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Gesicherter Exportteil fehlt")
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo("manifest.json")
            if info.file_size > 8 * 1024**2:
                raise RuntimeError("Exportmanifest zu groß")
            entries = json.loads(bundle.read(info))
            expected = [
                {k: v for k, v in item.items() if k not in {"key", "bucket"}}
                for item in items[completed : completed + len(entries)]
            ]
            if not entries or entries != expected or len(bundle.infolist()) != len(entries) + 1:
                raise RuntimeError("Exportteil gehört nicht zum Auftrag")
            for item in entries:
                if bundle.getinfo(item["name"]).file_size != item["size"]:
                    raise RuntimeError("Exportgröße stimmt nicht überein")
                digest = hashlib.sha256()
                with bundle.open(item["name"]) as body:
                    for block in iter(lambda: body.read(64 * 1024), b""):
                        headroom(path.parent)
                        digest.update(block)
                if digest.hexdigest() != item["sha256"]:
                    raise RuntimeError("Gesicherter Exportteil beschädigt")
            completed += len(entries)
    return completed


def run_once():
    from app.raw_archive.coordination import lease
    from app.raw_archive.health import SafetyPause

    path = root()
    reap_exports(path)
    with connect(path) as db:
        pending = db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','working') AND created>=? LIMIT 1",
            (time.time() - EXPIRES,),
        ).fetchone()
    if not pending:
        return {"status": "idle"}
    try:
        context = (
            nullcontext()
            if os.getenv("RAW_ARCHIVE_COORDINATION_LEASE_EXTERNAL") == "true"
            else lease("export")
        )
        with context:
            return _run_once()
    except SafetyPause as error:
        return {"status": "paused_for_other_archive_job", "reason": error.code}


def _run_once():
    path = root()
    with ExportStore(path) as store, connect(path) as db:
        with store.locked():
            store.reap(db)
        try:
            headroom(path)
        except CapacityPause:
            return {"status": "paused_for_host_load"}
        lock = None
        with store.locked():
            rows = db.execute(
                "SELECT * FROM jobs WHERE status='queued' AND created>? ORDER BY created",
                (time.time() - EXPIRES,),
            ).fetchall()
            for job in rows:
                lock = store.acquire_job(job["id"])
                if lock is not None:
                    break
            if lock is None:
                return {"status": "idle"}
            with db:
                db.execute(
                    "UPDATE jobs SET status='working',completed=CASE WHEN parts='[]' THEN 0 ELSE completed END WHERE id=?",
                    (job["id"],),
                )
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        try:
            make_parts(path, job, db, store=store)
            return {"status": "ready", "id": job["id"]}
        except CapacityPause:
            with store.locked(), db:
                db.execute(
                    "UPDATE jobs SET status='queued',error=NULL WHERE id=? AND status='working'",
                    (job["id"],),
                )
            return {"status": "paused_for_host_load", "id": job["id"]}
        except ExportCancelled:
            return {"status": "cancelled", "id": job["id"]}
        except Exception as exc:
            with store.locked(), db:
                db.execute(
                    "UPDATE jobs SET status='failed',error=? WHERE id=? AND status='working'",
                    (type(exc).__name__, job["id"]),
                )
            return {"status": "failed", "id": job["id"], "error": type(exc).__name__}
        finally:
            os.close(lock)


if __name__ == "__main__":
    print(json.dumps(run_once()))
