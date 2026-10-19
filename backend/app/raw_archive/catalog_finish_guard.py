"""Fresh resource checks during a native SQLite integrity walk, fail closed."""

import threading
import time

from .health import SafetyPause


class CatalogFinishGuard:
    def __init__(self, probe, *, deadline, clock=time.monotonic):
        self.probe, self.deadline, self.clock = probe, deadline, clock
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.state = {"ok": False, "checked": float("-inf"), "reason": "not_checked"}
        self.thread = None

    def _check(self):
        try:
            ok = self.probe()
            reason = self.probe.last.get("reason")
        except Exception:
            ok, reason = False, "probe_error"
        with self.lock:
            self.state = {"ok": bool(ok), "checked": self.clock(), "reason": reason}
        return bool(ok)

    def start(self):
        self._check()
        self()
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()
        return self

    def _monitor(self):
        try:
            while not self.stop.wait(2):
                if not self._check():
                    return  # A failed check stays latched for this attempt.
        finally:
            self.probe.close()

    def __call__(self):
        now = self.clock()
        with self.lock:
            state = dict(self.state)
        if now >= self.deadline:
            raise SafetyPause("catalog_part_deadline")
        if not state["ok"]:
            raise SafetyPause(state["reason"] or "headroom")
        if now - state["checked"] > 5:
            raise SafetyPause("health_monitor_stale")

    def close(self):
        self.stop.set()
        if self.thread is None:
            self.probe.close()
        else:
            self.thread.join(4)
            if self.thread.is_alive():
                raise SafetyPause("health_monitor_shutdown")
