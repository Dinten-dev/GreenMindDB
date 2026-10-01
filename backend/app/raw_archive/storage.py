"""Storage adapters used only by the separately started archive worker."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError

from .policy import ArchiveBlocked, Recording, RemoteMissing, cutoff
from .telemetry import measure

SFTP_TIMEOUT_SECONDS = 300


class S3Source:
    def __init__(self, client):
        self.client = client

    def snapshot(self, recording: Recording) -> dict:
        head = self.client.head_object(Bucket=recording.bucket, Key=recording.key)
        if head["ContentLength"] != recording.size:
            raise ArchiveBlocked("Source size changed")
        return {name: head.get(name) for name in ("VersionId", "ETag", "ContentLength")} | {
            "LastModified": head["LastModified"].isoformat(),
        }

    def download(self, recording: Recording, snapshot: dict, path: Path) -> None:
        args = {"Bucket": recording.bucket, "Key": recording.key, "IfMatch": snapshot["ETag"]}
        if snapshot.get("VersionId") not in (None, "null"):
            args["VersionId"] = snapshot["VersionId"]
        body = self.client.get_object(**args)["Body"]
        written = 0
        started = time.monotonic()
        try:
            with path.open("xb") as target:
                while block := body.read(256 * 1024):
                    written += len(block)
                    if written > recording.size:
                        raise ArchiveBlocked("Source exceeds declared length")
                    target.write(block)
                    time.sleep(max(0, written / 1048576 - (time.monotonic() - started)))
                target.flush()
                os.fsync(target.fileno())
        finally:
            body.close()

    def evict(self, recording: Recording, snapshot: dict) -> None:
        # An unversioned delete has a check/delete race. Never use it here.
        version = snapshot.get("VersionId")
        if version in (None, "", "null"):
            raise ArchiveBlocked("Exact-version deletion needs S3 versioning; keep local WAV")
        if datetime.fromisoformat(snapshot["LastModified"]) >= cutoff(datetime.now(UTC)):
            raise ArchiveBlocked("The object itself is not from a closed Swiss calendar day")
        if self.client.get_bucket_versioning(Bucket=recording.bucket).get("Status") != "Enabled":
            raise ArchiveBlocked("Source bucket versioning is not enabled")
        try:
            lifecycle = self.client.get_bucket_lifecycle_configuration(Bucket=recording.bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
                raise
        else:
            if any(rule.get("Status") == "Enabled" for rule in lifecycle.get("Rules", [])):
                raise ArchiveBlocked("Unreviewed bucket lifecycle may bypass archive safety")
        args = {"Bucket": recording.bucket, "Key": recording.key, "VersionId": version}
        try:
            head = self.client.head_object(**args)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "NoSuchVersion", "404"}:
                return  # Durable deleting receipt + fresh remote readback permits recovery.
            raise
        if head["ETag"] != snapshot["ETag"] or head["ContentLength"] != snapshot["ContentLength"]:
            raise ArchiveBlocked("Immutable source snapshot changed")
        versions = self.client.list_object_versions(Bucket=recording.bucket, Prefix=recording.key)
        exact = [entry for entry in versions.get("Versions", []) if entry["Key"] == recording.key]
        markers = [
            entry for entry in versions.get("DeleteMarkers", []) if entry["Key"] == recording.key
        ]
        if (
            versions.get("IsTruncated")
            or markers
            or len(exact) != 1
            or exact[0]["VersionId"] != version
            or not exact[0]["IsLatest"]
        ):
            raise ArchiveBlocked("Other object versions need separate reconciliation")
        self.client.delete_object(**args)
        try:
            self.client.head_object(**args)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "NoSuchVersion", "404"}:
                return
            raise
        raise ArchiveBlocked("Deleted version is still present; reconcile before retry")


class StorageBox:
    """OpenSSH SFTP, pinned host key, one request, bounded bandwidth and files.

    Never use a remote shell, trust-on-first-use, or a raw-data delete operation.
    Uploads use unique partial names; final-path bytes are read back by policy.py.
    """

    def __init__(
        self,
        *,
        host: str,
        user: str,
        key: Path,
        known_hosts: Path,
        root: str = "greenmind-raw",
        port: int = 23,
        kilobits: int = 8192,
    ):
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or not re.fullmatch(r"[a-zA-Z0-9_-]+", user):
            raise ArchiveBlocked("Invalid Storage Box account")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", root) or not 1 <= port <= 65535:
            raise ArchiveBlocked("Use one dedicated relative archive directory")
        if not key.is_file() or not known_hosts.is_file() or not 128 <= kilobits <= 8192:
            raise ArchiveBlocked("Pinned host keys, private key and bounded bandwidth required")
        self.host, self.user, self.root, self.port = host, user, root, port
        self.metrics = None
        self._session_root = self._master = self._socket = self._socket_dir = None
        self._ssh_command = [
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-p",
            str(port),
            "-i",
            str(key),
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oUpdateHostKeys=no",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={known_hosts}",
            "-oConnectTimeout=10",
            "-oServerAliveInterval=10",
            "-oServerAliveCountMax=2",
            "-oForwardAgent=no",
            "-oClearAllForwardings=yes",
        ]
        self.command = [
            "/usr/bin/sftp",
            "-F",
            "/dev/null",
            "-q",
            "-f",
            "-b",
            "-",
            "-P",
            str(port),
            "-l",
            str(kilobits),
            "-R",
            "1",
            "-B",
            "32768",
            "-i",
            str(key),
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oUpdateHostKeys=no",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={known_hosts}",
            "-oConnectTimeout=10",
            "-oServerAliveInterval=10",
            "-oServerAliveCountMax=2",
            f"{user}@{host}",
        ]

    @contextmanager
    def session(self, root, *, enabled=True, metrics=None):
        """One owned SSH transport; file operations retain their separate size limits."""
        if self._session_root is not None:
            raise ArchiveBlocked("Archive transport session already open")
        self.metrics = metrics
        self._session_root = root if enabled else None
        try:
            yield self
        finally:
            self.close()

    def close(self):
        if self._master is not None:
            if self._master.poll() is None:
                try:
                    os.killpg(self._master.pid, signal.SIGTERM)
                    self._master.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if self._master.poll() is None:
                        os.killpg(self._master.pid, signal.SIGKILL)
                        self._master.wait(timeout=3)
            self._master = None
        if self._socket_dir is not None:
            self._socket_dir.cleanup()
        self._socket_dir = self._socket = self._session_root = None

    def _transport_command(self):
        if self._session_root is None:
            if self.metrics is not None:
                self.metrics.counts["standalone_sftp_sessions"] += 1
            return self.command
        if self._master is None:
            self._socket_dir = tempfile.TemporaryDirectory(prefix="ssh-", dir=self._session_root)
            self._socket = str(Path(self._socket_dir.name) / "ctl")
            if len(os.fsencode(self._socket)) > 100:
                raise ArchiveBlocked("Archive SSH socket path too long")
            command = [
                *self._ssh_command,
                "-M",
                "-N",
                "-S",
                self._socket,
                "-oControlPersist=no",
                f"{self.user}@{self.host}",
            ]
            with measure(self.metrics, "ssh_connect"):
                # Fixed binary and flags; host/user are format-validated above.
                self._master = subprocess.Popen(  # noqa: S603
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if self._master.poll() is not None:
                        raise ArchiveBlocked("Archive SSH transport could not start")
                    if Path(self._socket).exists():
                        break
                    time.sleep(0.05)
                else:
                    raise ArchiveBlocked("Archive SSH transport startup timed out")
        if self._master.poll() is not None or not Path(self._socket).exists():
            raise ArchiveBlocked("Archive SSH transport lost; retry next run")
        if self.metrics is not None:
            self.metrics.counts["reused_sftp_sessions"] += 1
        # ProxyCommand=false prevents an implicit unpooled reconnect on master loss.
        return [
            *self.command[:-1],
            "-oControlMaster=no",
            "-oControlPath=" + self._socket,
            "-oProxyCommand=false",
            self.command[-1],
        ]

    @property
    def identity(self) -> str:
        return f"sftp://{self.user}@{self.host}:{self.port}/{self.root}"

    def path(self, key: str) -> str:
        legacy = r"(production|staging)/(gateway|direct)/[0-9a-f]{64}/[0-9a-f]{64}\.wav"
        grouped = (
            r"(production|staging)/[0-9]{4}-[0-9]{2}-[0-9]{2}/"
            r"(?:mac-(?:[0-9a-f]{2}-){5}[0-9a-f]{2}|(?:gateway|direct)-[0-9a-f-]{36})/"
            r"[0-9]{12}_(?:gateway|direct)_[0-9a-f]{64}_[0-9a-f]{64}\.wav"
        )
        if not re.fullmatch(legacy + "|" + grouped, key):
            raise ArchiveBlocked("Unsafe remote archive key")
        return self.root + "/" + key

    @staticmethod
    def quote(path: str | Path) -> str:
        value = str(path)
        if any(char in value for char in ("\n", "\r", "\x00", "*", "?", "[", "]")):
            raise ArchiveBlocked("Unsafe SFTP path")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def run(
        self,
        batch: str,
        *,
        output: Path | None = None,
        max_bytes: int = 0,
        missing_path: str | None = None,
    ) -> None:
        command = self._transport_command()
        if output is not None:
            command = [sys.executable, "-m", "app.raw_archive.sftp_limit", str(max_bytes), *command]
        # Fixed executable/argv; paths are validated above, no shell is involved.
        with subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "LC_ALL": "C"},
        ) as process:
            try:
                _, stderr = process.communicate(batch, timeout=SFTP_TIMEOUT_SECONDS)
            except BaseException:
                # Also stop the SSH descendant; it must not outlive a timed-out transfer.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
        if process.returncode:
            if (
                missing_path
                and process.returncode == 1
                and re.fullmatch(
                    r'File "(?:[^"\r\n]*/)?' + re.escape(missing_path) + r'" not found\.\s*', stderr
                )
            ):
                raise RemoteMissing("Remote archive file does not exist")
            raise ArchiveBlocked("SFTP operation failed; retain source and inspect worker logs")

    def publish(self, path: Path, key: str) -> None:
        from .policy import checksum

        remote = self.path(key)
        # A process may have died after rename but before journaling. Reuse only
        # an independently verified identical final object on retry.
        with tempfile.TemporaryDirectory(dir=path.parent, prefix="existing-") as folder:
            previous = Path(folder) / "remote.wav"
            try:
                self.download(key, previous, path.stat().st_size)
            except RemoteMissing:
                pass  # ONLY a proven missing final file permits creation.
            else:
                if previous.stat().st_size != path.stat().st_size or checksum(previous) != checksum(
                    path
                ):
                    raise ArchiveBlocked("Existing remote archive is corrupt; retain original")
                return
        temporary = remote + ".partial-" + uuid.uuid4().hex
        directories = remote.split("/")[:-1]
        commands = ["-mkdir " + "/".join(directories[:n]) for n in range(1, len(directories) + 1)]
        commands += [f"put {self.quote(path)} {temporary}", f"rename {temporary} {remote}"]
        self.run("\n".join(commands) + "\n")

    def download(self, key: str, path: Path, max_bytes: int) -> None:
        if path.exists() or not 0 < max_bytes <= 64 * 1024**2:
            raise ArchiveBlocked("Download needs an unused path and a bounded size")
        self.run(
            f"get {self.path(key)} {self.quote(path)}\n",
            output=path,
            max_bytes=max_bytes,
            missing_path=self.path(key),
        )
        if path.stat().st_size > max_bytes:
            raise ArchiveBlocked("Remote object exceeds allowed size")

    def restore(self, key: str, sha256: str, size: int, destination: Path) -> None:
        """Operator restore, verified before visibility; never overwrite an existing file."""
        from .policy import checksum

        if destination.exists() or not re.fullmatch("[0-9a-f]{64}", sha256):
            raise ArchiveBlocked("Restore requires a new path and verified receipt")
        temporary = destination.with_name(destination.name + ".partial-" + uuid.uuid4().hex)
        try:
            self.download(key, temporary, size)
            if temporary.stat().st_size != size or checksum(temporary) != sha256:
                raise ArchiveBlocked("Restore checksum mismatch")
            with temporary.open("rb") as restored:
                os.fsync(restored.fileno())
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
