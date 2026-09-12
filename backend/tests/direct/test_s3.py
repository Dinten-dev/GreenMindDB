import os
import time
import uuid
from urllib.parse import urlsplit

import pytest

from app.direct.assembler import assemble_one
from app.direct.config import DirectSettings
from app.direct.retention import run_retention
from tests.direct.conftest import build_pipeline
from tests.direct.test_pipeline import current_manifest


@pytest.mark.integration
def test_real_minio_roundtrip_and_retention(tmp_path):
    endpoint = os.environ.get("DIRECT_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Dedicated local MinIO test endpoint not configured")
    assert urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
    bucket = "greenmind-direct-test-" + uuid.uuid4().hex
    cfg = DirectSettings(
        database_url=f"sqlite:///{tmp_path}/direct.db",
        ingest_enabled=True,
        require_tls=False,
        artifact_root=tmp_path,
        idle_seconds=1,
        storage="s3",
        s3_endpoint_url=endpoint,
        s3_bucket=bucket,
        s3_access_key="direct-local-test",
        s3_secret_key="direct-local-test-password",
    )
    p = build_pipeline(cfg)
    p.store.client.create_bucket(Bucket=bucket)
    try:
        assert p.upload(bytes(1200)).status_code == 201
        assert assemble_one(p.engine, cfg, p.store, now=time.time() + cfg.late_seconds + 100)
        manifest = current_manifest(p)
        key = manifest["runs"][0]["key"]
        assert p.store.get(key).startswith(b"RIFF")
        future = time.time() + 91 * 86400
        cfg.retention_enabled = True
        assert run_retention(p.engine, cfg, p.store, now=future)["segments"] == 1
        assert p.store.get(key).startswith(b"RIFF")
        cfg.retention_dry_run = False
        assert run_retention(p.engine, cfg, p.store, now=future)["segments"] == 1
        assert p.store.client.list_objects_v2(Bucket=bucket)["KeyCount"] == 0
    finally:
        for item in p.store.client.list_objects_v2(Bucket=bucket).get("Contents", []):
            p.store.client.delete_object(Bucket=bucket, Key=item["Key"])
        p.store.client.delete_bucket(Bucket=bucket)
        p.client.close()
        p.engine.dispose()
