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


def blocks(reader_port):
    if reader_port not in {8140, 8141}:
        raise ArchiveBlocked("Unreviewed archive reader port")
    return tuple(block.replace(":8140", f":{reader_port}") for block in (BEFORE, NESTED))


def draft(original, expected_sha256, *, reader_port=8140):
    if hashlib.sha256(original.encode()).hexdigest() != expected_sha256:
        raise ArchiveBlocked("Production proxy changed; inspect its new configuration first")
    if original.count(ANCHOR) != 1 or "BEGIN GREENMIND ARCHIVE COMPAT READS" in original:
        raise ArchiveBlocked("Unexpected proxy structure")
    before, nested = blocks(reader_port)
    candidate = original.replace(ANCHOR, before + ANCHOR + nested, 1)
    if rollback(candidate, reader_port=reader_port) != original:
        raise ArchiveBlocked("Proxy draft is not exactly reversible")
    return candidate


def rollback(candidate, *, reader_port=8140):
    before, nested = blocks(reader_port)
    if candidate.count(before) != 1 or candidate.count(nested) != 1:
        raise ArchiveBlocked("Compatibility proxy blocks changed")
    return candidate.replace(before, "", 1).replace(nested, "", 1)
