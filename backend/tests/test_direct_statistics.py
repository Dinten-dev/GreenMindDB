"""Hot SQL and cold archive views must derive the same population statistics."""

import math
from types import SimpleNamespace

import pytest

from app.tiered_archive.reader import points
from app.visualization.direct import _query_local_series


@pytest.mark.parametrize(
    "values",
    [
        [0.1, 0.2, 0.3],
        [1.0, 2.0, 5.0],
        [1773.0, 1773.2, 1800.6],
        [-28.5, 800.15, 3300.0],
    ],
)
def test_sql_and_archive_standard_deviation_use_original_second_moment(values):
    row = dict(
        channel=0,
        unit="mV",
        bucket=1790006400,
        seconds=600,
        n=len(values),
        total=math.fsum(values),
        total2=math.fsum(v * v for v in values),
        minimum=min(values),
        maximum=max(values),
        duration=0.01,
        verified=True,
    )
    result = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row]))
    db = SimpleNamespace(execute=lambda *_args, **_kwargs: result)
    hot = _query_local_series(db, "fixture-device", 1790006400, 1790007000, 600)[0]["data"][0]
    cold = points([row], source="direct", step=600)[0]["data"][0]
    for field in (
        "value",
        "minimum",
        "maximum",
        "rms",
        "standard_deviation",
        "reading_count",
        "coverage_ratio",
    ):
        assert hot[field] == cold[field]
