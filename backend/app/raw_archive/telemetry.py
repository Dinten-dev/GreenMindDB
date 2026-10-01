"""Credential-free timings and bounded pause diagnostics for the isolated copier."""

from collections import defaultdict
from contextlib import contextmanager, nullcontext
from time import monotonic


class Metrics:
    def __init__(self):
        self.seconds = defaultdict(float)
        self.counts = defaultdict(int)

    @contextmanager
    def measure(self, stage):
        started = monotonic()
        try:
            yield
        finally:
            self.seconds[stage] += monotonic() - started
            self.counts[stage] += 1

    def snapshot(self):
        return {
            "seconds": {k: round(v, 6) for k, v in self.seconds.items()},
            "counts": dict(self.counts),
        }


def measure(metrics, stage):
    return metrics.measure(stage) if metrics is not None else nullcontext()
