"""Opt-in pytest evidence: every selection exception and its exact reason."""

import json
import os
from pathlib import Path

selected_out = []
reports = []


def pytest_deselected(items):
    for item in items:
        selected_out.append(
            {
                "nodeid": item.nodeid,
                "reason": "Excluded by -m not integration",
                "markers": sorted({mark.name for mark in item.iter_markers()}),
            }
        )


def pytest_runtest_logreport(report):
    if report.when == "call" or report.skipped or report.failed:
        reason = None
        if report.skipped:
            reason = (
                report.longrepr[2]
                if isinstance(report.longrepr, tuple)
                else str(report.longrepr)
            )
        reports.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "reason": reason,
            }
        )


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get("GREENMIND_TEST_EVIDENCE")
    if target:
        Path(target).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "exitstatus": int(exitstatus),
                    "deselected": selected_out,
                    "reports": reports,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
