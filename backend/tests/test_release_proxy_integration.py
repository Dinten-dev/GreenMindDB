"""Real local nginx reload/rollback while a synthetic old HTTP receiver accepts data.

This tests proxy continuity, not physical Gateway or firmware correctness.
Opt-in Docker context; unique containers/network are always removed afterwards.
"""

import importlib.util
import json
import os
import runpy
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
NGINX_IMAGE = "nginx@sha256:ef8676b33d681f272ba429b27658bdd7e640963279714c96bddf1dc76307f7b6"
PYTHON_IMAGE = "python@sha256:c4634f578a412db396771b61b064c6e546c9d6414c7fb5b1b05d5871f1885f7b"
MOCK = """import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
seen=set()
lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  with lock: body=json.dumps({'port':self.server.server_port,'sequences':sorted(seen)}).encode()
  self.send_response(200); self.end_headers(); self.wfile.write(body)
 def do_POST(self):
  if self.path=='/api/v1/gateways/heartbeat':
   self.send_response(410); self.end_headers(); self.wfile.write(b'{"detail":{"action":"RESET_TO_SETUP_MODE"}}'); return
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  with lock: seen.add(data['sequence'])
  self.send_response(201); self.end_headers(); self.wfile.write(b'{"accepted":true}')
for port in (8000,3000,8004,3004):
 threading.Thread(target=ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever,daemon=True).start()
threading.Event().wait()
"""


def test_nginx_switch_and_failed_switch_keep_old_receiver_alive(tmp_path, monkeypatch):
    context = os.environ.get("RELEASE_DOCKER_CONTEXT")
    if not context:
        pytest.skip("Opt-in local Docker proxy rehearsal")
    spec = importlib.util.spec_from_file_location(
        "release_rehearsal", ROOT / "deploy/release/release.py"
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    tag = "gm-proxy-test-" + uuid.uuid4().hex[:10]
    proxy, receiver = tag + "-nginx", tag + "-receiver"
    docker = ["docker", "--context", context]

    def execute(*args, data=None):
        result = subprocess.run(
            [*docker, *args], input=data, capture_output=True, text=True, timeout=90
        )
        if result.returncode:
            error = subprocess.CalledProcessError(
                result.returncode, args, result.stdout, result.stderr
            )
            error.add_note(result.stderr)
            raise error
        return result.stdout.strip()

    original = """pid /run/nginx.pid;\nevents {}\nhttp { server { listen 80;
    # API → Backend directly
    location /api/ { proxy_pass http://127.0.0.1:8000; }
    # Frontend (Next.js)
    location / { proxy_pass http://127.0.0.1:3000; }
} }\n"""
    guard = runpy.run_path(str(ROOT / "deploy/release/gateway_guard.py"))
    original = guard["render"](original, "production")
    candidate, _ = release.proposal(original, "production", 8004, 3004)
    original = original.replace("http://127.0.0.1:", "http://receiver:")
    candidate = candidate.replace("http://127.0.0.1:", "http://receiver:")
    target = tmp_path / "installed.conf"
    target.write_text(original)
    (tmp_path / "nginx.before.conf").write_text(original)
    (tmp_path / "nginx.proposed.conf").write_text(candidate)
    manifest = {
        "environment": "staging",
        "before_sha256": release.digest(target),
        "after_sha256": release.digest(tmp_path / "nginx.proposed.conf"),
        "protected": {},
        "previous_visual": False,
    }
    release.write_json(tmp_path / "manifest.json", manifest)
    thread_errors = []
    finish = threading.Event()
    try:
        execute("network", "create", tag)
        execute(
            "run",
            "-d",
            "--name",
            receiver,
            "--network",
            tag,
            "--network-alias",
            "receiver",
            "--memory",
            "96m",
            PYTHON_IMAGE,
            "python",
            "-u",
            "-c",
            MOCK,
        )
        execute(
            "run",
            "-d",
            "--name",
            proxy,
            "--network",
            tag,
            "-p",
            "127.0.0.1::80",
            "--memory",
            "96m",
            NGINX_IMAGE,
        )
        port = json.loads(execute("inspect", proxy))[0]["NetworkSettings"]["Ports"]["80/tcp"][0][
            "HostPort"
        ]
        base = "http://127.0.0.1:" + port

        def install():
            execute(
                "exec",
                "-i",
                proxy,
                "sh",
                "-c",
                "cat > /etc/nginx/nginx.conf.pending && mv /etc/nginx/nginx.conf.pending /etc/nginx/nginx.conf",
                data=target.read_text(),
            )

        for _attempt in range(30):
            try:
                execute("exec", proxy, "test", "-s", "/run/nginx.pid")
                break
            except subprocess.CalledProcessError:
                time.sleep(0.1)
        install()
        execute("exec", proxy, "nginx", "-t")
        execute("exec", proxy, "nginx", "-s", "reload")

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as response:  # noqa: S310 - local Docker loopback
                return json.load(response)

        for _attempt in range(30):
            try:
                if get("/")["port"] == 3000:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("Local proxy never became ready")
        started = json.loads(execute("inspect", receiver))[0]["State"]["StartedAt"]

        def assert_gateway_shield():
            for path in (
                "/api/v1/gateway/desired-state",
                "/api/v1/gateway/desired-state/",
                "/api/v1/gateway/app-release/1.0.0/download",
                "/api/v1/gateway/config-release/1/download",
                "/api/v1/gateways/local-gateway/commands",
            ):
                with pytest.raises(urllib.error.HTTPError) as error:
                    get(path)
                assert error.value.code == 503
            request = urllib.request.Request(base + "/api/v1/gateways/heartbeat", data=b"{}")  # noqa: S310 - local test server
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)  # noqa: S310 - local test server
            assert error.value.code == 503
            assert b"RESET_TO_SETUP_MODE" not in error.value.read()

        assert_gateway_shield()

        def continuous():
            sequence = 0
            deadline = time.monotonic() + 20
            while not finish.is_set() and time.monotonic() < deadline:
                try:
                    request = urllib.request.Request(  # noqa: S310 - local Docker loopback
                        base + "/api/v1/ingest",
                        data=json.dumps({"sequence": sequence}).encode(),
                        headers={"Content-Type": "application/json"},
                    )  # noqa: S310 - local Docker loopback
                    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                        assert response.status == 201
                    sequence += 1
                    time.sleep(0.01)
                except BaseException as error:
                    thread_errors.append(error)
                    break
            return sequence

        def unchanged(_):
            state = json.loads(execute("inspect", receiver))[0]["State"]
            assert state["Running"] and state["StartedAt"] == started

        def control(*args, **kwargs):
            if args == ("nginx", "-t"):
                install()
                try:
                    return execute("exec", proxy, "nginx", "-t")
                except subprocess.CalledProcessError as error:
                    raise RuntimeError("Candidate nginx syntax rejected") from error
            assert args == ("systemctl", "reload", "nginx")
            return execute("exec", proxy, "nginx", "-s", "reload")

        monkeypatch.setattr(release.os, "geteuid", lambda: 0)
        monkeypatch.setattr(release, "check_previous", lambda *_: None)
        monkeypatch.setattr(release, "run", control)
        monkeypatch.setattr(release, "unchanged", unchanged)
        monkeypatch.setattr(release, "check", lambda *_: {"passed": True})
        monkeypatch.setattr(release, "compose", lambda *_: '{"compacted_chunks":0}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            stream = pool.submit(continuous)
            time.sleep(0.2)
            release.activate_release(tmp_path, manifest, target)
            for _ in range(30):
                if get("/")["port"] == 3004 and get("/api/v1/visualization/probe")["port"] == 8004:
                    break
                time.sleep(0.1)
            assert get("/")["port"] == 3004
            assert get("/api/v1/visualization/probe")["port"] == 8004
            assert get("/api/v1/ingest")["port"] == 8000
            assert_gateway_shield()
            release.rollback_release(tmp_path, manifest, target)
            for _ in range(30):
                if get("/")["port"] == 3000:
                    break
                time.sleep(0.1)
            assert get("/")["port"] == 3000
            assert_gateway_shield()
            (tmp_path / "nginx.proposed.conf").write_text("invalid_nginx_directive;\n")
            with pytest.raises(RuntimeError, match="syntax rejected"):
                release.activate_release(tmp_path, manifest, target)
            assert target.read_text() == original
            time.sleep(0.2)
            finish.set()
            sent = stream.result(timeout=10)
        assert sent >= 10
        assert thread_errors == []
        assert get("/api/v1/ingest")["sequences"] == list(range(sent))
        unchanged({})
        print(
            json.dumps(
                {
                    "sent": sent,
                    "missing": 0,
                    "receiver_restarts": 0,
                    "switch": True,
                    "rollback": True,
                    "invalid_candidate_restored": True,
                    "gateway_controls_blocked": True,
                    "heartbeat_reset_suppressed": True,
                }
            )
        )
    finally:
        finish.set()
        for name in (proxy, receiver):
            subprocess.run([*docker, "rm", "-f", name], capture_output=True, timeout=30)
        subprocess.run([*docker, "network", "rm", tag], capture_output=True, timeout=30)
