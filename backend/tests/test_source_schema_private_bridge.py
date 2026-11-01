"""Private operator protocol on real local pipes/files; no SSH, Docker or databases."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "deploy/tiered-archive/source-schema-bridge"
spec = importlib.util.spec_from_file_location("schema_bridge_test", HERE / "controller.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
w = controller.wire


def module(name):
    namespace = {"__name__": "local_bridge_test"}
    exec(  # noqa: S102 -- exact local operator source, no native invocation
        compile(controller.program(name), "pinned_" + name, "exec", dont_inherit=True), namespace
    )
    return namespace


def request():
    value = {
        "format": "greenmind.private-source-schema-request.v1",
        "scope_sha256": "a" * 64,
        "source_identity_sha256": "b" * 64,
        "source_machine_sha256": "065c65fd7f52297f59a00eddc22986c3b0c1530aa7e12b99e9d4d88c1155dd58",
        "source_container": "fa5679cd84c16b5024251bb1f93414201fb091900af590cb63a7ecc087dddb71",
        "database_oid": 16384,
        "server_version_num": 150010,
        "snapshot": "00000003-0000001B-1",
        "program_sha256": hashlib.sha256(controller.program("source.py")).hexdigest(),
        "transport_binding_sha256": controller.binding(),
        "pg_dump_args": [
            "--no-password",
            "--format=custom",
            "--schema-only",
            "--no-owner",
            "--no-acl",
            "--no-blobs",
            "--snapshot=00000003-0000001B-1",
            "--dbname=plantdb",
        ],
        "max_bytes": w.MAX_BYTES,
        "application_data_authorized": False,
    }
    return value


def payload_wire(value, body, *, change=None, terminal=True):
    header = {
        "kind": "schema-stream-v1",
        "request_sha256": w.digest(value),
        "startup": {"pid": 123},
        "physical_identity_verified": True,
    }
    trailer = {
        "kind": "schema-end-v1",
        "request_sha256": w.digest(value),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "owned_dump_wait_exit": 0,
        "source_unchanged": True,
    }
    if change:
        change(header, trailer)
    raw = json.dumps(header).encode() + b"\n"
    # Split the actual five-byte custom header across frames deliberately.
    for block in [body[:1], body[1:4], body[4:]]:
        if block:
            for at in range(0, len(block), w.BLOCK):
                part = block[at : at + w.BLOCK]
                raw += struct.pack(">I", len(part)) + part
    raw += struct.pack(">I", 0) + json.dumps(trailer).encode() + b"\n"
    if terminal:
        raw += (
            json.dumps(
                {
                    "kind": "source-admin-terminal-v1",
                    "request_sha256": w.digest(value),
                    "actual_owned_wait_exit": 0,
                    "source_unit_terminal_verified": True,
                }
            ).encode()
            + b"\n"
        )
    return raw


def write_pipe(fd, raw):
    try:
        w.write_all(fd, raw, time.monotonic() + 5)
    finally:
        os.close(fd)


def test_actual_pipe_relay_and_receiver_full_hash_no_mac_payload_file(tmp_path):
    value = request()
    body = b"PGDMP" + b"synthetic schema-only definition" * 8000
    folder = tmp_path / "archive-private"
    folder.mkdir(mode=0o700)
    (folder / "request.json").write_text(json.dumps(value))
    receiver = module("receive.py")
    # Test adapter only: actual local user owns the private synthetic directory.
    receiver["private"] = lambda path, **kw: Path(path)
    source_in, source_out = os.pipe()
    receiver_in, receiver_out = os.pipe()
    produced = payload_wire(value, body, terminal=False)
    producer = threading.Thread(target=write_pipe, args=(source_out, produced))
    result = {}
    errors = []

    def consume():
        try:
            result.update(receiver["receive"](receiver_in, folder, value, time.monotonic() + 5))
        except BaseException as error:
            errors.append(error)
        finally:
            os.close(receiver_in)

    consumer = threading.Thread(target=consume)
    producer.start()
    consumer.start()
    try:
        _, proof = controller.relay_body(source_in, receiver_out, value, time.monotonic() + 5)
        w.write_json(
            receiver_out,
            {
                "kind": "source-admin-terminal-v1",
                "request_sha256": w.digest(value),
                "actual_owned_wait_exit": 0,
                "source_unit_terminal_verified": True,
            },
            time.monotonic() + 5,
        )
    finally:
        os.close(source_in)
        os.close(receiver_out)
        producer.join(6)
        consumer.join(6)
    assert not producer.is_alive() and not consumer.is_alive() and not errors
    assert result["sha256"] == proof["sha256"] == hashlib.sha256(body).hexdigest()
    assert (folder / "schema.dump").read_bytes() == body
    assert (folder / "schema.dump").stat().st_mode & 0o777 == 0o600
    # Callback cannot consume a receipt before the receiver's actual own wait.
    assert not (folder / "response.json").exists()
    receipt = json.loads((folder / "response.ready.json").read_bytes())
    assert receipt["schema"]["sha256"] == proof["sha256"]
    assert set(tmp_path.iterdir()) == {folder}


@pytest.mark.parametrize(
    "case", ["checksum", "source_exit", "source_changed", "header_request", "truncated"]
)
def test_real_receiver_refuses_bad_or_unfinished_source_and_never_publishes(tmp_path, case):
    value = request()
    body = b"PGDMP synthetic"
    folder = tmp_path / "archive"
    folder.mkdir(mode=0o700)
    (folder / "request.json").write_text(json.dumps(value))

    def alter(header, trailer):
        if case == "checksum":
            trailer["sha256"] = "f" * 64
        if case == "source_exit":
            trailer["owned_dump_wait_exit"] = 1
        if case == "source_changed":
            trailer["source_unchanged"] = False
        if case == "header_request":
            header["request_sha256"] = "e" * 64

    raw = payload_wire(value, body, change=alter, terminal=case != "truncated")
    source_in, source_out = os.pipe()
    thread = threading.Thread(target=write_pipe, args=(source_out, raw))
    thread.start()
    receiver = module("receive.py")
    receiver["private"] = lambda path, **kw: Path(path)
    try:
        with pytest.raises(RuntimeError):
            receiver["receive"](source_in, folder, value, time.monotonic() + 2)
    finally:
        os.close(source_in)
        thread.join(3)
    assert not (folder / "response.json").exists() and not (folder / "response.ready.json").exists()


@pytest.mark.parametrize("case", ["master", "full", "snapshot", "source", "flag_type"])
def test_actual_source_request_validator_never_broadens_schema_only(case):
    source = module("source.py")
    value = request()
    spec = {
        "request": value,
        "scope_sha256": value["scope_sha256"],
        "source_identity_sha256": value["source_identity_sha256"],
        "source_postmaster_start": "2026-08-31T16:13:00.505241+00:00",
        "program_sha256": value["program_sha256"],
        "transport_sha256": value["transport_binding_sha256"],
        "source_unit": "gm-schema-source-" + "a" * 32 + ".service",
    }
    assert source["validate"](spec) == value
    if case == "master":
        value["application_data_authorized"] = True
    if case == "flag_type":
        value["application_data_authorized"] = 0
    if case == "full":
        value["pg_dump_args"].remove("--schema-only")
    if case == "snapshot":
        value["snapshot"] = "bad; SQL"
    if case == "source":
        value["database_oid"] = 301614
    with pytest.raises(RuntimeError):
        source["validate"](spec)


def test_existing_admin_transport_matches_existing_hosts_without_secret_argv(monkeypatch):
    calls = []
    admin = controller.ExistingAdmin()
    monkeypatch.setattr(admin, "password", lambda role: b"ONLY_SYNTHETIC_SUDO_SECRET")

    class Process:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.stdin = type("Pipe", (), {"fileno": lambda self: 11})()

    writes = []
    monkeypatch.setattr(controller.subprocess, "Popen", Process)
    monkeypatch.setattr(w, "write_all", lambda fd, value, deadline: writes.append(value))
    for role in ("source", "archive"):
        admin.start(
            role,
            b"# fixed Root program",
            unit="gm-schema-"
            + ("source-" if role == "source" else "receive-")
            + "a" * 32
            + ".service",
            directory="/var/lib/greenmind/synthetic/only",
        )
    assert calls[0][0][-2] == "traver@188.245.247.156"
    assert calls[1][0][-2] == "traver@2.31.22.33"
    assert all("ONLY_SYNTHETIC_SUDO_SECRET" not in json.dumps(call[0]) for call in calls)
    assert writes == [b"ONLY_SYNTHETIC_SUDO_SECRET\n"] * 2
    assert all(
        "--property=MemoryMax=64M" in call[0][-1]
        and "--property=CPUQuota=5%" in call[0][-1]
        and "--property=MemorySwapMax=0" in call[0][-1]
        for call in calls
    )
    assert not any("-i" == part for call in calls for part in call[0])


def test_controller_default_is_inert(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["controller"])
    monkeypatch.setattr(controller.subprocess, "Popen", lambda *a, **k: pytest.fail("No native"))
    assert controller.main() == 0
    assert json.loads(capsys.readouterr().out) == {"inert": True, "native_calls": 0}


@pytest.mark.parametrize("source_exit", [0, 7])
def test_complete_controller_uses_actual_owned_wait_before_receipt_promotion(
    tmp_path, monkeypatch, source_exit
):
    folder = tmp_path / "archive-private"
    folder.mkdir(mode=0o700)
    value = request()
    (folder / "request.json").write_text(json.dumps(value))
    scope = {
        "format": "greenmind.private-admin-schema-bridge.v1",
        "execution_authorized": True,
        "expires_unix": int(time.time()) + 60,
        "run_id": "a" * 32,
        "directory": "/var/lib/greenmind/synthetic/only",
        "provider_scope_sha256": value["scope_sha256"],
        "source_identity_sha256": value["source_identity_sha256"],
        "source_postmaster_start": "2026-08-31T16:13:00.505241+00:00",
        "transport_binding_sha256": controller.binding(),
    }
    children = {}
    body = b"PGDMP" + b"ONLY SYNTHETIC SCHEMA" * 500

    # Remote operations are explicit local fixtures; relay and wait logic are real.
    class Admin:
        def start(self, role, code, **kwargs):
            wire_code = (HERE / "wire.py").read_text()
            prelude = (
                wire_code
                + "\nimport sys\nown={'pid':os.getpid(),'synthetic':True}\nwrite_json(1,{'ready_for_control':True,'startup':own},time.monotonic()+5)\nspec=read_json(0,time.monotonic()+5)\n"
            )
            if role == "source":
                post = (
                    "\nvalue=spec['request']\nwrite_json(1,{'kind':'schema-stream-v1','request_sha256':digest(value),'physical_identity_verified':True,'startup':own},time.monotonic()+5)\nbody="
                    + repr(body)
                    + "\nimport struct\nwrite_all(1,struct.pack('>I',len(body))+body+struct.pack('>I',0),time.monotonic()+5)\nwrite_json(1,{'kind':'schema-end-v1','request_sha256':digest(value),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'owned_dump_wait_exit':0,'source_unchanged':True},time.monotonic()+5)\nraise SystemExit("
                    + str(source_exit)
                    + ")\n"
                )
            else:
                receive = (
                    (HERE / "receive.py")
                    .read_text()
                    .replace('if __name__ == "__main__":', "if False:")
                )
                post = (
                    "\n"
                    + receive
                    + "\nprivate=lambda p,**kw:Path(p)\nfolder=Path("
                    + repr(str(folder))
                    + ")\nwrite_json(1,{'receiver_admitted':True,'request_sha256':digest(spec['request'])},time.monotonic()+5)\nresult=receive(0,folder,spec['request'],time.monotonic()+5)\nwrite_json(1,{'passed':True,**result,'startup':own},time.monotonic()+5)\n"
                )
                # receive.py imports the injected wire module used in real programs.
                prelude = (
                    controller.program("receive.py")
                    .decode()
                    .replace('if __name__ == "__main__":', "if False:")
                    + "\nimport sys\nown={'pid':os.getpid(),'synthetic':True}\nwrite_json(1,{'ready_for_control':True,'startup':own},time.monotonic()+5)\nspec=read_json(0,time.monotonic()+5)\n"
                )
                post = (
                    "\nprivate=lambda p,**kw:Path(p)\nfolder=Path("
                    + repr(str(folder))
                    + ")\nwrite_json(1,{'receiver_admitted':True,'request_sha256':digest(spec['request'])},time.monotonic()+5)\nresult=receive(0,folder,spec['request'],time.monotonic()+5)\nwrite_json(1,{'passed':True,**result,'startup':own},time.monotonic()+5)\n"
                )
            child = subprocess.Popen(
                [sys.executable, "-B", "-c", prelude + post],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            children[role] = child
            return child

    terminals = []

    def terminal(admin, role, message, deadline):
        assert children[role].returncode == 0
        assert message["startup"]["pid"] == children[role].pid
        terminals.append(role)
        if role == "archive":
            assert (
                hashlib.sha256((folder / "response.ready.json").read_bytes()).hexdigest()
                == message["receipt_sha256"]
            )
            os.rename(folder / "response.ready.json", folder / "response.json")
        return {"terminal": True}

    monkeypatch.setattr(controller, "root_control", terminal)
    if source_exit:
        with pytest.raises(RuntimeError, match="owned_admin_exit_failed"):
            controller.bridge(Admin(), scope, value)
        assert not (folder / "response.json").exists() and not terminals
        assert children["source"].returncode == 7
    else:
        result = controller.bridge(Admin(), scope, value)
        assert (
            result["passed"]
            and result["source_owned_wait_exit"] == result["receiver_owned_wait_exit"] == 0
        )
        assert terminals == ["source", "archive"]
        assert (
            json.loads((folder / "response.json").read_bytes())["schema"]["sha256"]
            == hashlib.sha256(body).hexdigest()
        )
    assert all(child.poll() is not None for child in children.values())
