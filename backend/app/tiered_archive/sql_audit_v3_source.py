"""Source-root DBA engine for one explicitly permitted V3 approval.

Default inert. Existing Docker/DB credentials stay in Source memory and are
never returned, printed or persisted by this module. No DDL is executed.
"""

import http.client
import json
import os
import socket
import stat
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.raw_archive.policy import ArchiveBlocked

from . import sql_audit_v3

CONTAINER = "fa5679cd84c16b5024251bb1f93414201fb091900af590cb63a7ecc087dddb71"
IMAGE = "sha256:f74040a77544227fc269b66ed23d9e0205d53f706c8ddaf00ed7032440b271ca"
SOCKET = Path("/var/run/docker.sock")


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


class _Docker(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(4)
        self.sock.connect(str(SOCKET))


def _observe_container():
    """Inspect only the pinned Source DB container; keep all env values private."""
    try:
        info = SOCKET.lstat()
        need(
            stat.S_ISSOCK(info.st_mode) and info.st_uid == 0,
            "Source Docker socket identity differs",
        )
        client = _Docker("localhost", timeout=4)
        try:
            client.request("GET", "/containers/" + CONTAINER + "/json")
            response = client.getresponse()
            raw = response.read(1024 * 1024 + 1)
            need(
                response.status == 200 and len(raw) <= 1024 * 1024,
                "Source DB container inspection failed",
            )
        finally:
            client.close()
        value = json.loads(raw)
        state = value["State"]
        need(
            value["Id"] == CONTAINER
            and value["Image"] == IMAGE
            and state["Running"] is True
            and type(state["Pid"]) is int
            and state["Pid"] > 1
            and type(state["StartedAt"]) is str
            and state["StartedAt"],
            "Source DB container generation differs",
        )
        env = value["Config"]["Env"]
        need(
            type(env) is list and all(type(x) is str and "=" in x for x in env),
            "Source DB container environment differs",
        )
        credentials = {}
        for item in env:
            name, value = item.split("=", 1)
            if name in {"POSTGRES_USER", "POSTGRES_PASSWORD"}:
                need(name not in credentials, "Source DBA configuration ambiguous")
                credentials[name] = value
        need(
            credentials.get("POSTGRES_USER") == "admin"
            and type(credentials.get("POSTGRES_PASSWORD")) is str
            and 0 < len(credentials["POSTGRES_PASSWORD"]) <= 4096,
            "Existing Source DBA login unavailable",
        )
        return {"pid": state["Pid"], "started_at": state["StartedAt"]}, credentials[
            "POSTGRES_PASSWORD"
        ]
    except ArchiveBlocked:
        raise
    except Exception:
        raise ArchiveBlocked("Source DB container metadata unavailable") from None


def provision_source(
    *,
    permit_path,
    permit_sha256,
    binding_path,
    binding_sha256,
    lease_session,
    checkpoint,
    enabled=False,
):
    """Run the pinned DBA provisioner on Source only after explicit local enablement.

    The caller owns Archive→Source lease and authenticated Root handoff; an
    unknown commit outcome must be reconciled before any retry.
    """
    need(
        enabled is True
        and os.geteuid() == 0
        and os.getenv("TIERED_ARCHIVE_SQL_V3_SOURCE_DBA_ENABLED") == "true",
        "Explicit Source-root V3 DBA enablement required",
    )
    generation, password = _observe_container()
    url = URL.create(
        "postgresql+psycopg2",
        username="admin",
        password=password,
        host="127.0.0.1",
        port=5432,
        database="plantdb",
    )
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 3,
            "options": "-c statement_timeout=8000 -c lock_timeout=150 -c search_path=pg_catalog,public",
        },
    )
    try:
        result = sql_audit_v3.provision(
            engine,
            permit_path=permit_path,
            permit_sha256=permit_sha256,
            binding_path=binding_path,
            binding_sha256=binding_sha256,
            lease_session=lease_session,
            checkpoint=checkpoint,
        )
        after, _ = _observe_container()
        need(
            after == generation,
            "Source DBA container changed; approval outcome requires reconciliation",
        )
        return result
    except Exception:
        # A database transaction might have committed. Never infer safe retry.
        raise ArchiveBlocked(
            "V3 Source DBA provision blocked or outcome requires reconciliation"
        ) from None
    finally:
        engine.dispose()
        password = None
