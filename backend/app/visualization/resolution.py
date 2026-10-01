"""Choose a bounded display resolution without inventing missing samples."""

from datetime import timedelta

STEPS = (1, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400, 604800)
MAX_WINDOW = timedelta(days=3650)


def display_step(start, end, *, requested=None, max_points=1200):
    span = end - start
    if span <= timedelta(0) or span > MAX_WINDOW:
        raise ValueError("Select a positive window of at most ten years")
    if requested is not None:
        if requested not in STEPS:
            raise ValueError("Unsupported resolution")
        return requested
    minimum = span.total_seconds() / max_points
    return next((step for step in STEPS if step >= minimum), STEPS[-1])
