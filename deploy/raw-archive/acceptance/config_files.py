"""Preserve configuration across separate filesystems; never unlink a file."""

import hashlib
import os
import uuid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_exact(source, target, expected, mode=0o600):
    assert not source.is_symlink() and source.stat().st_size <= 64 * 1024
    assert digest(source) == expected
    with target.open("xb") as body:
        os.fchmod(body.fileno(), mode)
        body.write(source.read_bytes())
        body.flush()
        os.fsync(body.fileno())
    assert digest(target) == expected


def withdraw(path, evidence, expected):
    """Copy private evidence first; rename within the configuration filesystem."""
    copy_exact(path, evidence, expected)
    assert digest(path) == expected
    inactive = path.with_name("." + path.name + ".inactive-" + uuid.uuid4().hex)
    path.rename(inactive)
    return inactive


def publish_new(source, path, expected):
    """Same-filesystem atomic link, rejecting concurrent target creation."""
    pending = path.with_name("." + path.name + ".pending-" + uuid.uuid4().hex)
    copy_exact(source, pending, expected, mode=0o644)
    os.link(pending, path)
    return pending
