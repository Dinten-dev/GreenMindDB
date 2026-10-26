"""Require recent evidence that the encryption key was recovered off host."""

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path


def verify(path, fingerprint, *, now=None):
    if not re.fullmatch("[a-f0-9]{64}", fingerprint):
        raise ValueError("Pinned backup key fingerprint required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o027
        ):
            raise ValueError("Recovery receipt must be protected against modification")
        raw = body.read(8193)
    if len(raw) > 8192:
        raise ValueError("Recovery receipt exceeds budget")
    proof = json.loads(raw)
    now = now or datetime.now(UTC)
    at = datetime.fromisoformat(proof["verified_at"])
    if at.tzinfo is None or not timedelta(0) <= now - at <= timedelta(days=7):
        raise ValueError("Recent key recovery evidence required")
    if (
        proof.get("schema") != 1
        or proof.get("key_sha256") != fingerprint
        or proof.get("roundtrip") is not True
        or not proof.get("escrow_reference")
    ):
        raise ValueError("Complete recovered-key evidence required")
    return {
        "key_recovery_verified": True,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--fingerprint", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.receipt, args.fingerprint), sort_keys=True))
