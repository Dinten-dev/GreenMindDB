"""Minimal GET-only proxy draft. No file writes, reloads or activation."""

import hashlib

from .policy import ArchiveBlocked

ANCHOR = "    location ^~ /api/v1/direct-ingest/ {\n"
HEADERS = """        if ($request_method != GET) { return 405; }
        proxy_pass http://127.0.0.1:8140;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_connect_timeout 3s;
        proxy_read_timeout 300s;
        add_header Cache-Control "private, no-store" always;
"""
BEFORE = (
    "    # BEGIN GREENMIND ARCHIVE COMPAT READS\n"
    "    location ~ ^/api/v1/wav/(files|count|features|download-bundle|download/[0-9a-fA-F-]+)$ {\n"
    + HEADERS
    + "    }\n    location = /api/v1/direct-ingest/segments {\n"
    + HEADERS
    + "    }\n    # END GREENMIND ARCHIVE COMPAT READS\n"
)
NESTED = (
    "        # BEGIN GREENMIND ARCHIVE DIRECT RUN READS\n"
    "        location ~ ^/api/v1/direct-ingest/segments/[0-9a-fA-F-]+/runs/[0-9]+$ {\n"
    + "\n".join("    " + line for line in HEADERS.rstrip().splitlines())
    + "\n        }\n        # END GREENMIND ARCHIVE DIRECT RUN READS\n"
)


def draft(original, expected_sha256):
    if hashlib.sha256(original.encode()).hexdigest() != expected_sha256:
        raise ArchiveBlocked("Production proxy changed; inspect its new configuration first")
    if original.count(ANCHOR) != 1 or "BEGIN GREENMIND ARCHIVE COMPAT READS" in original:
        raise ArchiveBlocked("Unexpected proxy structure")
    candidate = original.replace(ANCHOR, BEFORE + ANCHOR + NESTED, 1)
    if rollback(candidate) != original:
        raise ArchiveBlocked("Proxy draft is not exactly reversible")
    return candidate


def rollback(candidate):
    if candidate.count(BEFORE) != 1 or candidate.count(NESTED) != 1:
        raise ArchiveBlocked("Compatibility proxy blocks changed")
    return candidate.replace(BEFORE, "", 1).replace(NESTED, "", 1)
