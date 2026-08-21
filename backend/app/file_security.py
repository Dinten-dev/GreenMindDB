"""Helpers for files and release signatures crossing trust boundaries."""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def resolve_contained_path(root: str | Path, relative_path: str) -> Path:
    """Resolve a relative path and reject absolute paths or root escapes."""
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    resolved_root = Path(root).resolve()
    candidate = (resolved_root / relative).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("Path escapes configured storage root")
    return candidate


def validate_ed25519_signature(signature: str | None) -> str:
    """Require a canonical base64-encoded 64-byte Ed25519 signature."""
    if not signature:
        raise ValueError("A release signature is required")
    try:
        decoded = base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Release signature must be valid base64") from exc
    if len(decoded) != 64:
        raise ValueError("Release signature must contain 64 bytes")
    return signature


def verify_ed25519_sha256(
    signature: str | None,
    sha256_hex: str,
    public_key_path: str | Path | None,
) -> None:
    """Verify the gateway agent's Ed25519 signature over ASCII SHA-256 hex."""
    validate_ed25519_signature(signature)
    if not public_key_path:
        raise ValueError("Gateway release signing public key is not configured")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        signing_key = load_pem_public_key(Path(public_key_path).read_bytes())
    except (binascii.Error, OSError, ValueError) as exc:
        raise ValueError("Gateway release signing public key is invalid") from exc
    if not isinstance(signing_key, Ed25519PublicKey):
        raise ValueError("Gateway release signing public key is not Ed25519")
    if len(sha256_hex) != 64 or any(char not in "0123456789abcdef" for char in sha256_hex):
        raise ValueError("Release SHA-256 is invalid")
    try:
        signing_key.verify(signature_bytes, sha256_hex.encode("utf-8"))
    except InvalidSignature as exc:
        raise ValueError("Gateway release signature verification failed") from exc


def verify_signed_file(
    path: str | Path,
    expected_sha256: str,
    signature: str | None,
    public_key_path: str | Path | None,
) -> None:
    """Verify a file's digest and its Ed25519 signature without loading it in memory."""
    actual_sha256 = verify_file_sha256(path, expected_sha256)
    verify_ed25519_sha256(signature, actual_sha256, public_key_path)


def verify_file_sha256(path: str | Path, expected_sha256: str) -> str:
    """Verify a regular file against a canonical SHA-256 digest and return the digest."""
    if len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256.lower()
    ):
        raise ValueError("Expected SHA-256 is invalid")
    hasher = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual_sha256 = hasher.hexdigest()
    if actual_sha256 != expected_sha256.lower():
        raise ValueError("Release artifact checksum mismatch")
    return actual_sha256
