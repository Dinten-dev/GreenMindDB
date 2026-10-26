"""Compare a separately recovered key and decrypt a fresh encrypted challenge."""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.tiered_archive.backup import decrypt, encrypt, key_from_file


def prove(primary, recovered, escrow_reference, output):
    if primary.samefile(recovered):
        raise ValueError("Independently recovered key file required")
    if not escrow_reference or len(escrow_reference) > 512:
        raise ValueError("Off-host escrow reference required")
    key = key_from_file(primary)
    restored = key_from_file(recovered)
    if not hmac.compare_digest(key, restored):
        raise ValueError("Recovered key differs")
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        folder = Path(folder)
        challenge = folder / "challenge"
        challenge.write_bytes(secrets.token_bytes(4096))
        challenge.chmod(0o600)
        metadata = encrypt(challenge, folder / "sealed", key, checkpoint=lambda: None)
        decrypt(
            folder / "sealed",
            folder / "decoded",
            restored,
            metadata["plaintext_sha256"],
            checkpoint=lambda: None,
        )
    proof = {
        "schema": 1,
        "verified_at": datetime.now(UTC).isoformat(),
        "key_sha256": hashlib.sha256(key).hexdigest(),
        "roundtrip": True,
        "escrow_reference": escrow_reference,
    }
    with output.open("x") as out:
        os.chmod(output, 0o600)
        json.dump(proof, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    return {"roundtrip": True, "key_sha256": proof["key_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--recovered", type=Path, required=True)
    parser.add_argument("--escrow-reference", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prove(args.primary, args.recovered, args.escrow_reference, args.output),
            sort_keys=True,
        )
    )
