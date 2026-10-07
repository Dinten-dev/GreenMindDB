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


def make_parts(path, job, db):
    from app.raw_archive.coordination import yield_requested

    items = json.loads(job["items"])
    names = json.loads(job["parts"])
    if any(Path(name).name != name or not name.startswith(job["id"] + "-") for name in names):
        raise RuntimeError("Ungültiger Exportteilpfad")
    created = [path / name for name in names]
    completed = saved_progress(created, items)
    if completed != job["completed"]:
        raise RuntimeError("Gesicherter Exportfortschritt stimmt nicht überein")
    gateway = _get_s3_client()
    direct = ArtifactStore(DirectSettings())
    active = None
    zipper = None
    manifest = []
    in_part = 0
    part = len(created)
    verified = completed
    job_started = time.monotonic()

    def finish():
        nonlocal zipper, active, part, manifest, in_part
        if zipper is None:
            return
        zipper.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zipper.close()
        zipper = None
        with active.open("rb") as body:
            os.fsync(body.fileno())
        finished = active.with_suffix(".zip")
        os.link(active, finished)  # Preserve private evidence; never overwrite a part.
        created.append(finished)
        with db:
            db.execute(
                "UPDATE jobs SET parts=?,completed=? WHERE id=?",
                (json.dumps([p.name for p in created]), verified, job["id"]),
            )
        active = None
        part += 1
        manifest = []
        in_part = 0

    try:
        for index, item in enumerate(items[completed:], start=completed):
            if time.monotonic() - job_started >= 60 and yield_requested(("copy", "catalog")):
                raise CapacityPause("waiting archive job")
            headroom(path)
            if zipper is None or (in_part and in_part + item["size"] > PART_BYTES):
                finish()
                active = path / f"{job['id']}-{part:03d}-{uuid.uuid4().hex}.part"
                zipper = zipfile.ZipFile(
                    active, "x", compression=zipfile.ZIP_STORED, allowZip64=True
                )
                manifest = []
                in_part = 0
            digest = hashlib.sha256()
            count = 0
            start = time.monotonic()
            body = source_for(item, gateway, direct)
            spool = path / f"{job['id']}-{uuid.uuid4().hex}.raw-part"
            try:
                with spool.open("xb") as target:
                    while block := body.read(64 * 1024):
                        headroom(path)
                        count += len(block)
                        if count > item["size"]:
                            raise RuntimeError("Original größer als Katalogangabe")
                        digest.update(block)
                        target.write(block)
                        time.sleep(max(0, count / 1048576 - (time.monotonic() - start)))
                    target.flush()
                    os.fsync(target.fileno())
            finally:
                body.close()
            if count != item["size"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError("Original oder Archivkopie stimmt nicht mit Prüfsumme überein")
            with spool.open("rb") as source, zipper.open(item["name"], "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, 64 * 1024)
            manifest.append({k: v for k, v in item.items() if k != "key" and k != "bucket"})
            in_part += count
            verified = index + 1
        finish()
        with db:
            db.execute(
                "UPDATE jobs SET status='ready',parts=? WHERE id=?",
                (json.dumps([p.name for p in created]), job["id"]),
            )
    except CapacityPause:
        if manifest:
            finish()
        elif zipper is not None:
            zipper.close()
        raise
    except Exception:
        if zipper is not None:
            zipper.close()
        raise


def saved_progress(parts, items):
    """Only complete, fully decoded matching ZIPs can carry progress forward."""
    completed = 0
    for path in parts:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Gesicherter Exportteil fehlt")
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo("manifest.json")
            if info.file_size > 8 * 1024**2:
                raise RuntimeError("Exportmanifest zu groß")
            entries = json.loads(bundle.read(info))
            expected = [{k: v for k, v in item.items() if k not in {"key", "bucket"}}
                        for item in items[completed:completed + len(entries)]]
            if not entries or entries != expected or len(bundle.infolist()) != len(entries) + 1:
                raise RuntimeError("Exportteil gehört nicht zum Auftrag")
            for item in entries:
                if bundle.getinfo(item["name"]).file_size != item["size"]:
                    raise RuntimeError("Exportgröße stimmt nicht überein")
                digest = hashlib.sha256()
                with bundle.open(item["name"]) as body:
                    for block in iter(lambda: body.read(64 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != item["sha256"]:
                    raise RuntimeError("Gesicherter Exportteil beschädigt")
            completed += len(entries)
    return completed


def run_once():
    from app.raw_archive.coordination import lease
    from app.raw_archive.health import SafetyPause

    path = root()
    with connect(path) as db:
        pending = db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','working') AND created>=? LIMIT 1",
            (time.time() - EXPIRES,),
        ).fetchone()
    if not pending:
        return {"status": "idle"}
    try:
        context = nullcontext() if os.getenv("RAW_ARCHIVE_COORDINATION_LEASE_EXTERNAL") == "true" else lease("export")
        with context:
            return _run_once()
    except SafetyPause as error:
        return {"status": "paused_for_other_archive_job", "reason": error.code}


def _run_once():
    path = root()
    try:
        headroom(path)
    except CapacityPause:
        return {"status": "paused_for_host_load"}
    with connect(path) as db:
        # An interrupted job starts over; no incomplete ZIP can become ready.
        db.execute("UPDATE jobs SET completed=0 WHERE status IN ('queued','working') AND parts='[]'")
        db.execute("UPDATE jobs SET status='queued' WHERE status='working'")
        db.commit()
        job = db.execute(
            "SELECT * FROM jobs WHERE status='queued' AND created>=? ORDER BY created LIMIT 1",
            (time.time() - EXPIRES,),
        ).fetchone()
        if not job:
            return {"status": "idle"}
        with db:
            db.execute("UPDATE jobs SET status='working' WHERE id=?", (job["id"],))
        try:
            make_parts(path, job, db)
            return {"status": "ready", "id": job["id"]}
        except CapacityPause:
            with db:
                db.execute(
                    "UPDATE jobs SET status='queued',error=NULL WHERE id=?",
                    (job["id"],),
                )
            return {"status": "paused_for_host_load", "id": job["id"]}
        except Exception as exc:
            with db:
                db.execute(
                    "UPDATE jobs SET status='failed',error=? WHERE id=?",
                    (type(exc).__name__, job["id"]),
                )
            return {"status": "failed", "id": job["id"], "error": type(exc).__name__}


if __name__ == "__main__":
    print(json.dumps(run_once()))
