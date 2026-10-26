"""Real local nginx: nested Direct reads, upload isolation, exact rollback."""

import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from app.raw_archive.compat_proxy import draft, rollback

pytestmark = pytest.mark.integration


def test_current_production_proxy_reads_and_uploads_remain_separate(tmp_path):
    source = os.getenv("ARCHIVE_TEST_NGINX_CONFIG")
    if not source:
        pytest.skip("Supply a read-only copy of the current production proxy")
    original = Path(source).read_text()
    candidate = draft(original, hashlib.sha256(original.encode()).hexdigest())
    assert rollback(candidate) == original

    def isolated(body):
        body = re.sub(
            r"^\s*(?:ssl_certificate(?:_key)?\s|ssl_dhparam\s|include /etc/letsencrypt/).*\n",
            "",
            body,
            flags=re.M,
        )
        body = body.replace("listen 443 ssl http2;", "listen 8080;").replace(
            "listen [::]:443 ssl http2;", "listen [::]:8080;"
        )
        body = body.replace("listen 172.28.20.1:9444 ssl;", "listen 127.0.0.1:9444;")
        body = body.replace("listen 80;", "listen 18080;").replace(
            "listen [::]:80;", "listen [::]:18080;"
        )
        mocks = "\n".join(
            f'server {{ listen 127.0.0.1:{port}; location / {{ return 200 "{port}:$request_method"; }} }}'
            for port in (8000, 8003, 8120, 8122, 8140, 3140)
        )
        return (
            "worker_processes 1; events { worker_connections 128; } http {\n"
            + body
            + mocks
            + "\n}\n"
        )

    config = tmp_path / "nginx.conf"
    config.write_text(isolated(original))
    docker = shutil.which("docker")
    assert docker
    name = "gm-compat-nginx-" + uuid.uuid4().hex
    subprocess.run(
        [
            docker,
            "create",
            "--name",
            name,
            "--label",
            "greenmind.compat-test=true",
            "--memory",
            "64m",
            "--cpus",
            "0.5",
            "-p",
            "127.0.0.1::8080",
            "nginx:1.28-alpine",
            "nginx",
            "-c",
            "/fixture/nginx.conf",
            "-g",
            "daemon off;",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        subprocess.run(
            [docker, "cp", str(tmp_path) + "/.", name + ":/fixture"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run([docker, "start", name], check=True, capture_output=True, timeout=10)
        address = subprocess.check_output(
            [docker, "port", name, "8080"], text=True, timeout=10
        ).strip()
        with httpx.Client(
            base_url="http://" + address,
            headers={"Host": "green-mind.ch"},
            timeout=3,
            trust_env=False,
        ) as client:
            for _ in range(30):
                try:
                    if client.get("/health").text == "8120:GET":
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            assert client.get("/api/v1/wav/count").text == "8120:GET"
            config.write_text(isolated(candidate))
            subprocess.run(
                [docker, "cp", str(config), name + ":/fixture/nginx.conf"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                [docker, "exec", name, "nginx", "-c", "/fixture/nginx.conf", "-t"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                [docker, "exec", name, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            time.sleep(0.3)
            for path in (
                "/api/v1/wav/count",
                "/api/v1/wav/download/" + str(uuid.uuid4()),
                "/api/v1/direct-ingest/segments",
                "/api/v1/direct-ingest/segments/" + str(uuid.uuid4()) + "/runs/0",
            ):
                assert client.get(path).text == "8140:GET"
                assert client.post(path, content=b"test").status_code == 405
            for _ in range(20):
                assert client.post("/api/v1/wav/upload", content=b"synthetic").text == "8120:POST"
                assert client.post("/api/v1/ingest", content=b"synthetic").text == "8120:POST"
                assert (
                    client.post("/api/v1/direct-ingest/chunks", content=b"synthetic").text
                    == "8003:POST"
                )
            assert client.get("/api/v1/gateway/desired-state").status_code == 503
            config.write_text(isolated(rollback(candidate)))
            subprocess.run(
                [docker, "cp", str(config), name + ":/fixture/nginx.conf"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                [docker, "exec", name, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            time.sleep(0.3)
            assert client.get("/api/v1/wav/count").text == "8120:GET"
    finally:
        label = subprocess.check_output(
            [
                docker,
                "inspect",
                "--format",
                '{{index .Config.Labels "greenmind.compat-test"}}',
                name,
            ],
            text=True,
            timeout=10,
        ).strip()
        assert label == "true"
        subprocess.run([docker, "rm", "-f", name], check=True, capture_output=True, timeout=20)
