"""Pin the candidate's authorization calls to the preserved production receiver."""

from .policy import ArchiveBlocked

RECEIVER = "gm-zones-production-2e687bec010a-application-1"


def dashboard_target(networks, target):
    if target.get("name") != RECEIVER or target.get("running") is not True:
        raise ArchiveBlocked("Preserved production authorization target is unavailable")
    shared = {value.get("name", name) for name, value in networks.items()}
    if not shared.intersection(target.get("networks", [])):
        raise ArchiveBlocked("Candidate cannot reach the preserved authorization target")
    # The generic 'application' alias belongs to multiple retained releases.
    return f"http://{RECEIVER}:8000"
