"""Finite migration ordering; rollback never starts a second writable source.

The adapter performs host-specific, hash-guarded actions. This engine cannot
delete files, enable retention, or operate outside the prepared Staging cohort.
"""

import json
import os
import re
import time
from pathlib import Path

from .policy import ArchiveBlocked


class Cutover:
    def __init__(self, adapter, state, cohort, *, clock=time.monotonic, budget=720):
        if not re.fullmatch(r"[a-f0-9]{24}", cohort):
            raise ArchiveBlocked("An exact fresh Staging cohort is required")
        if not 60 <= budget <= 720:
            raise ArchiveBlocked("Migration budget must remain bounded")
        self.adapter, self.state, self.cohort = adapter, Path(state), cohort
        self.clock, self.budget = clock, budget
        self.started = clock()
        self.paused = self.target_started = self.accepted = False
        self.rollback_attempted = False
        self.baseline = None

    def record(self, name, **details):
        # Each phase is additional evidence; previous attempts are never replaced.
        path = self.state / (name + ".json")
        data = {"cohort": self.cohort, "phase": name, "deleted_files": 0, **details}
        with path.open("x") as body:
            os.fchmod(body.fileno(), 0o600)
            body.write(json.dumps(data, sort_keys=True) + "\n")
            body.flush()
            os.fsync(body.fileno())
        directory = os.open(self.state, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def check(self):
        if self.clock() - self.started >= self.budget:
            raise ArchiveBlocked("Migration time budget exceeded")
        self.adapter.receiver_check(self.baseline)

    def run(self, *, publish=False):
        if not self.state.is_absolute() or self.state.exists():
            raise ArchiveBlocked("Preserve existing evidence; use a new absolute state")
        self.state.mkdir(mode=0o700, parents=True)
        self.baseline = self.adapter.preflight(self.cohort)
        self.check()
        # A separately scheduled, bounded rollback must exist before pausing.
        self.adapter.arm_watchdog(self.cohort)
        self.record("prepared")
        try:
            # Mark possible side effects before calling an operation: a timeout
            # does not prove that the remote stop/start did not take effect.
            self.paused = True
            self.adapter.pause_source(self.cohort)
            self.check()
            snapshot = self.adapter.cold_snapshot(self.cohort)
            self.record("source-snapshot", snapshot=snapshot)
            self.check()
            restored = self.adapter.transfer_restore(self.cohort, snapshot)
            if restored.get("cohort") != self.cohort or restored.get("snapshot") != snapshot:
                raise ArchiveBlocked("Target restored a different source snapshot")
            self.record("target-restored")
            self.check()
            self.target_started = True
            self.adapter.start_target(self.cohort)
            self.check()
            # Rehearsal must reject public writes until the source owner changes.
            proof = self.adapter.rehearse(self.cohort)
            if proof != {
                "cohort": self.cohort,
                "receiver_continuity": True,
                "proxy_rollback_verified": True,
                "public_writes_fenced": True,
                "source_snapshot_reconciled": True,
            }:
                raise ArchiveBlocked("Bounded real rehearsal proof is incomplete")
            self.record("rehearsed", proof=proof)
            self.check()
            if publish:
                self.adapter.publish(self.cohort)
                self.check()
                # Adapter persists acceptance and the single owner together.
                # If acknowledgment is lost, rollback must discover this state
                # before starting the old source; it must not infer failure.
                self.adapter.commit_owner(self.cohort, snapshot)
                self.accepted = True
                self.record("accepted", snapshot=snapshot)
            else:
                self.rollback()
                self.record("rehearsal-complete")
        except BaseException:
            if not self.accepted and not self.rollback_attempted:
                self.rollback()
            raise
        return {"cohort": self.cohort, "published": self.accepted, "deleted_files": 0}

    def rollback(self):
        self.rollback_attempted = True
        if self.adapter.accepted_owner(self.cohort):
            # Durable remote acceptance may have succeeded before acknowledgment.
            # Preserve that sole writer; operator reconciliation follows.
            self.record("rollback-withheld", reason="accepted_target_owner")
            return
        # Always stop and independently confirm the target first, even when its
        # startup timed out or we never received a successful startup response.
        self.adapter.stop_target(self.cohort)
        if self.adapter.target_writers_stopped(self.cohort) is not True:
            self.record("rollback-blocked", reason="target_stop_unconfirmed")
            raise ArchiveBlocked("Do not start source while target writer state is unknown")
        self.adapter.restore_proxy(self.cohort)
        if self.paused:
            self.adapter.resume_source(self.cohort)
            self.paused = False
        self.adapter.receiver_check(self.baseline)
