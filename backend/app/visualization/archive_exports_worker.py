"""One bounded original-WAV export; run under an isolated systemd service."""

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from app.direct.config import DirectSettings
from app.direct.storage import ArtifactStore
from app.raw_archive import reader as archive_reader
from app.services.wav_service import _get_s3_client
from app.visualization.archive_exports import EXPIRES, PART_BYTES, connect, root


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
        raise RuntimeError("Server braucht Vorrang; Export später wiederholen")


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
    items = json.loads(job["items"])
    gateway = _get_s3_client()
    direct = ArtifactStore(DirectSettings())
    created = []
    active = None
    zipper = None
    manifest = []
    in_part = 0
    part = 0
    try:
        for index, item in enumerate(items):
            headroom(path)
            if zipper is None or (in_part and in_part + item["size"] > PART_BYTES):
                if zipper is not None:
                    zipper.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                    zipper.close()
                    with active.open("rb") as f:
                        os.fsync(f.fileno())
                    finished = path / f"{job['id']}-{part:03d}.zip"
                    os.replace(active, finished)
                    created.append(finished)
                    part += 1
                active = path / f"{job['id']}-{part:03d}.part"
                zipper = zipfile.ZipFile(
                    active, "x", compression=zipfile.ZIP_STORED, allowZip64=True
                )
                manifest = []
                in_part = 0
            digest = hashlib.sha256()
            count = 0
            start = time.monotonic()
            body = source_for(item, gateway, direct)
            try:
                with zipper.open(item["name"], "w", force_zip64=True) as target:
                    while block := body.read(64 * 1024):
                        count += len(block)
                        if count > item["size"]:
                            raise RuntimeError("Original größer als Katalogangabe")
                        digest.update(block)
                        target.write(block)
                        time.sleep(max(0, count / 1048576 - (time.monotonic() - start)))
            finally:
                body.close()
            if count != item["size"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError("Original oder Archivkopie stimmt nicht mit Prüfsumme überein")
            manifest.append({k: v for k, v in item.items() if k != "key" and k != "bucket"})
            in_part += count
            with db:
                db.execute("UPDATE jobs SET completed=? WHERE id=?", (index + 1, job["id"]))
        zipper.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zipper.close()
        zipper = None
        with active.open("rb") as f:
            os.fsync(f.fileno())
        finished = path / f"{job['id']}-{part:03d}.zip"
        os.replace(active, finished)
        created.append(finished)
        with db:
            db.execute(
                "UPDATE jobs SET status='ready',parts=? WHERE id=?",
                (json.dumps([p.name for p in created]), job["id"]),
            )
    except Exception:
        if zipper is not None:
            zipper.close()
        if active is not None:
            active.unlink(missing_ok=True)
        for part_file in created:
            part_file.unlink(missing_ok=True)
        raise


def run_once():
    path = root()
    headroom(path)
    with connect(path) as db:
        # An interrupted job starts over; no incomplete ZIP can become ready.
        interrupted = db.execute("SELECT id FROM jobs WHERE status='working'").fetchall()
        for old in interrupted:
            for suffix in ("part", "zip"):
                for file in path.glob(f"{old['id']}-*.{suffix}"):
                    file.unlink(missing_ok=True)
        db.execute("UPDATE jobs SET status='queued',completed=0 WHERE status='working'")
        expired = db.execute(
            "SELECT id,parts FROM jobs WHERE created<?", (time.time() - EXPIRES,)
        ).fetchall()
        for old in expired:
            for name in json.loads(old["parts"]):
                target = path / name
                if target.parent == path and target.name.startswith(old["id"] + "-"):
                    target.unlink(missing_ok=True)
        db.execute("DELETE FROM jobs WHERE created<?", (time.time() - EXPIRES,))
        db.commit()
        job = db.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
        ).fetchone()
        if not job:
            return {"status": "idle"}
        with db:
            db.execute("UPDATE jobs SET status='working',completed=0 WHERE id=?", (job["id"],))
        try:
            make_parts(path, job, db)
            return {"status": "ready", "id": job["id"]}
        except Exception as exc:
            with db:
                db.execute(
                    "UPDATE jobs SET status='failed',error=? WHERE id=?",
                    (type(exc).__name__, job["id"]),
                )
            return {"status": "failed", "id": job["id"], "error": type(exc).__name__}


if __name__ == "__main__":
    print(json.dumps(run_once()))
