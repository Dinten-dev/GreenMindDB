"""The real private owner serves independent clients and rejects split ownership."""

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def short_socket_directory():
    # Darwin's sockaddr_un is shorter than pytest's default temporary path.
    with tempfile.TemporaryDirectory(prefix="gm-live-", dir="/tmp") as directory:
        yield Path(directory)


def test_unix_owner_is_shared_exclusive_and_restart_is_volatile(tmp_path, short_socket_directory):
    directory = short_socket_directory / "s"
    directory.mkdir(mode=0o700)
    socket = directory / "live.sock"
    environment = dict(
        os.environ,
        DIRECT_LIVE_SOCKET=str(socket),
        DIRECT_DATABASE_URL="unused-not-a-database-connection",
    )
    log_path = tmp_path / "owner.log"

    def launch(log):
        return subprocess.Popen(
            [sys.executable, "-m", "app.direct.live_worker"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def stop(process):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def ready(client, process):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            if process.poll() is not None:
                pytest.fail(log_path.read_text())
            try:
                if client.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        pytest.fail("Private live owner did not start")

    with (
        log_path.open("w") as log,
        httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket)), base_url="http://live", timeout=1
        ) as one,
        httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket)), base_url="http://live", timeout=1
        ) as two,
    ):
        process = launch(log)
        try:
            ready(one, process)
            device, lease = str(uuid.uuid4()), str(uuid.uuid4())
            principal = "a" * 64
            assert (
                one.post(
                    "/dispatch",
                    json={
                        "namespace": "a" * 64,
                        "op": "lease",
                        "device": device,
                        "lease": lease,
                        "principal": principal,
                    },
                ).status_code
                == 200
            )
            result = two.post(
                "/dispatch",
                json={
                    "namespace": "a" * 64,
                    "op": "preview",
                    "device": device,
                    "lease": lease,
                    "principal": principal,
                    "seconds": 30,
                    "after": 0,
                    "generation": "",
                },
            )
            assert result.status_code == 200 and result.json()["rail"] is None
            second = launch(log)
            try:
                assert second.wait(timeout=8) != 0
                # A rejected second owner must not unlink the working socket.
                assert two.get("/health").status_code == 200
            finally:
                if second.poll() is None:
                    stop(second)
            stop(process)
            process = launch(log)
            ready(one, process)
            result = two.post(
                "/dispatch",
                json={
                    "namespace": "a" * 64,
                    "op": "preview",
                    "device": device,
                    "lease": lease,
                    "principal": principal,
                    "seconds": 30,
                    "after": 0,
                    "generation": "",
                },
            )
            assert result.status_code == 404
        finally:
            stop(process)


def test_idle_ram_expires_without_another_device_or_viewer_request():
    from fastapi.testclient import TestClient

    from app.direct.live_state import LiveStore
    from app.direct.live_worker import create_live_worker

    clock = [100.0]
    wall = [time.time()]
    store = LiveStore(clock=lambda: clock[0], wall=lambda: wall[0])
    with TestClient(create_live_worker(store=store)):
        device = str(uuid.uuid4())
        lease = str(uuid.uuid4())
        principal = "a" * 64
        store.lease(device, lease, principal)
        start = int((wall[0] - 1) * 1e6)
        session = str(uuid.uuid4())
        config = dict(
            session_id=session,
            session_start_us=start,
            source_rate=400,
            raw_rate=400,
            preview_profile="raw400-box32-v1",
            firmware_version="synthetic-live",
        )
        generation = store.control(device, config)["generation"]
        store.push(
            device,
            dict(
                generation=generation,
                session_id=session,
                sequence=0,
                points=[dict(frame=383, samples=32, mv=1650.0)],
                rail=dict(start_us=start, end_us=start + 960000, raw_samples=384, rail_samples=307),
            ),
        )
        store.mark(
            device,
            lease,
            principal,
            str(uuid.uuid4()),
            dict(generation=generation, session_id=session, frame=383),
        )
        assert store.devices[device].points and store.devices[device].markers
        clock[0] += 181
        wall[0] += 181
        until = time.monotonic() + 2
        while time.monotonic() < until:
            with store.lock:
                if not store.devices:
                    break
            time.sleep(0.02)
        with store.lock:
            assert store.devices == {}
