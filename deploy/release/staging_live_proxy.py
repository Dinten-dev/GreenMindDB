"""Isolate Staging device/control traffic using its existing management upstream.

No service, listener or resource limit is created. Raw uploads keep their route.
Production files and layouts without an existing distinct management API fail closed.
"""

import re

BEGIN = "    # BEGIN GREENMIND STAGING LIVE ISOLATION"
END = "    # END GREENMIND STAGING LIVE ISOLATION"
PREFIX = "/api/v1/direct-ingest/devices/"


def proposal(original: str) -> str:
    names = {
        name
        for group in re.findall(r"(?m)^\s*server_name\s+([^;]+);", original)
        for name in group.split()
    }
    if names != {"test.green-mind.ch"}:
        raise ValueError("Only the dedicated Staging site is supported")
    management = re.findall(
        r"(?m)^\s*location\s+=\s+/api/v1/direct-ingest/devices\s*\{([^{}]+)\}",
        original,
    )
    raw = re.findall(
        r"(?m)^([ \t]*location\s+\^~\s+/api/v1/direct-ingest/\s*\{([^{}]+)\})",
        original,
    )
    if len(management) != 1 or len(raw) != 1:
        raise ValueError("Expected one existing management route and raw route")
    pattern = r"proxy_pass\s+(http://127\.0\.0\.1:[0-9]+)\s*;"
    upstream = re.findall(pattern, management[0])
    raw_upstream = re.findall(pattern, raw[0][1])
    if len(upstream) != 1 or len(raw_upstream) != 1 or upstream == raw_upstream:
        raise ValueError("A distinct existing loopback management API is required")
    body = re.sub(pattern, lambda _: "proxy_pass " + upstream[0] + ";", raw[0][1])
    block = BEGIN + "\n    location ^~ " + PREFIX + " {" + body + "}\n" + END
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1:
            raise ValueError("Ambiguous existing isolation block")
        current = original[original.index(BEGIN) : original.index(END) + len(END)]
        if current != block:
            raise ValueError("Existing isolation differs; reconcile before changing it")
        return original
    if re.search(r"location\s+\^?~?\s*" + re.escape(PREFIX), original):
        raise ValueError("Existing device prefix requires explicit reconciliation")
    # Insert a longer prefix before the unchanged broad raw-ingest prefix.
    return original.replace(raw[0][0], block + "\n\n" + raw[0][0], 1)
