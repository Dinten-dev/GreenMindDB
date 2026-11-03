"""Bounded three-minute history, cursor reset, stale rails and marker expiry."""

import copy
import uuid

import pytest

from app.direct.live_state import LiveStore
from app.direct.protocol import DirectError


def test_three_minute_history_markers_expire_and_sequences_cannot_rewrite():
    seconds = [0.0]
    epoch = 1800000000
    ram = LiveStore(clock=lambda: seconds[0], wall=lambda: epoch + seconds[0])
    d, lease, session = [str(uuid.uuid4()) for _ in range(3)]
    who = "a" * 64
    spec = {
        "session_id": session,
        "session_start_us": epoch * 1000000,
        "source_rate": 400,
        "raw_rate": 400,
        "preview_profile": "raw400-box32-v1",
        "firmware_version": "test",
    }
    generation = ram.lease(d, lease, who)["generation"]
    ram.control(d, spec)
    first_mark = None
    for batch in range(500):
        seconds[0] = (batch + 1) * 0.4
        if batch % 20 == 0:
            ram.lease(d, lease, who)
            ram.control(d, spec)
        end = (batch + 1) * 400000
        payload = {
            "generation": generation,
            "session_id": session,
            "sequence": batch,
            "points": [
                {"frame": (batch * 5 + i + 1) * 32 - 1, "samples": 32, "mv": 1650.0}
                for i in range(5)
            ],
            "rail": {
                "start_us": epoch * 1000000 + max(0, end - 10000000),
                "end_us": epoch * 1000000 + end,
                "raw_samples": min((batch + 1) * 160, 4000),
                "rail_samples": min((batch + 1) * 128, 3200),
            },
        }
        assert ram.push(d, payload)["status"] == "accepted"
        if batch == 0:
            frame = payload["points"][-1]["frame"]
            mark = {"generation": generation, "session_id": session, "frame": frame}
            first_mark = str(uuid.uuid4())
            assert ram.mark(d, lease, who, first_mark, mark)["frame"] == frame
            # Exact retry keeps the original creation time, not a renewed TTL.
            assert ram.mark(d, lease, who, first_mark, mark)["frame"] == frame
        assert len(ram.devices[d].points) <= 2251
    result = ram.preview(d, lease, who, seconds=180)
    assert 2248 <= len(result["points"]) <= 2251
    assert result["window_complete"] is True
    assert result["rail"]["complete"] is True
    assert result["rail"]["percent"] == 80
    assert result["markers"] == []
    assert first_mark not in ram.devices[d].markers
    assert ram.preview(d, lease, who, after=result["cursor"], generation=generation)["points"] == []
    assert ram.push(d, payload)["status"] == "duplicate"
    altered = copy.deepcopy(payload)
    altered["points"][0]["mv"] = 5.0
    with pytest.raises(DirectError) as conflict:
        ram.push(d, altered)
    assert conflict.value.status == 409
    seconds[0] += 2
    stale = ram.preview(d, lease, who)
    assert stale["stale"] is True and stale["rail"]["stale"] is True
    with pytest.raises(DirectError):
        ram.mark(
            d,
            lease,
            who,
            str(uuid.uuid4()),
            {
                "generation": generation,
                "session_id": session,
                "frame": payload["points"][-1]["frame"],
            },
        )


def test_all_read_windows_and_old_firmware_hint_without_inventing_zero():
    seconds = [100.0]
    ram = LiveStore(clock=lambda: seconds[0])
    d, lease = str(uuid.uuid4()), str(uuid.uuid4())
    who = "a" * 64
    ram.lease(d, lease, who)
    assert ram.preview(d, lease, who)["capability"] == "checking"
    seconds[0] += 4
    for window in (30, 60, 180):
        result = ram.preview(d, lease, who, seconds=window)
        assert result["capability"] == "upgrade_required"
        assert result["rail"] is None
        assert result["points"] == [] and result["window_complete"] is False


def test_same_device_uuid_in_another_environment_or_organization_cannot_share_live_state():
    ram = LiveStore()
    device, lease = str(uuid.uuid4()), str(uuid.uuid4())
    request = {
        "op": "lease",
        "device": device,
        "lease": lease,
        "principal": "a" * 64,
        "namespace": "a" * 64,
    }
    ram.dispatch(request)
    read = {**request, "op": "preview", "seconds": 30, "after": 0, "generation": ""}
    assert ram.dispatch(read)["points"] == []
    with pytest.raises(DirectError):
        ram.dispatch({**read, "namespace": "b" * 64})


def test_batched_acks_refresh_capability_without_renewing_viewer_leases():
    seconds = [0.0]
    epoch = 1800000000
    ram = LiveStore(clock=lambda: seconds[0], wall=lambda: epoch + seconds[0])
    device, lease, session = [str(uuid.uuid4()) for _ in range(3)]
    principal = "a" * 64
    generation = ram.lease(device, lease, principal)["generation"]
    ram.control(
        device,
        {
            "session_id": session,
            "session_start_us": epoch * 1000000,
            "source_rate": 400,
            "raw_rate": 400,
            "preview_profile": "raw400-box32-v1",
            "firmware_version": "test",
        },
    )
    for batch in range(20):
        seconds[0] = (batch + 1) * 1.28
        end = (batch + 1) * 1280000
        body = {
            "generation": generation,
            "session_id": session,
            "sequence": batch,
            "points": [
                {"frame": (batch * 16 + i + 1) * 32 - 1, "samples": 32, "mv": 1650.0}
                for i in range(16)
            ],
            "rail": {
                "start_us": epoch * 1000000 + max(0, end - 10000000),
                "end_us": epoch * 1000000 + end,
                "raw_samples": min((batch + 1) * 512, 4000),
                "rail_samples": 0,
            },
        }
        ack = ram.push(device, body)
        assert ack["status"] == "accepted" and ack["enabled"] is True
        assert ack["generation"] == generation
        assert abs(ack["expires_in_ms"] - int((30 - seconds[0]) * 1000)) <= 1
        assert ram.preview(device, lease, principal)["capability"] == "ready"
    # An ACK lost on the wire can be retried without inserting the points twice.
    cursor = ram.preview(device, lease, principal)["cursor"]
    seconds[0] += 0.4
    retry = ram.push(device, body)
    assert retry["status"] == "duplicate" and retry["expires_in_ms"] < ack["expires_in_ms"]
    assert ram.preview(device, lease, principal)["cursor"] == cursor == 320
    seconds[0] = 30.01
    with pytest.raises(DirectError) as expired:
        ram.push(device, body)
    assert expired.value.code == "live_not_requested"


def test_push_ack_observes_remaining_viewers_and_never_revives_released_demand():
    seconds = [0.0]
    epoch = 1800000000
    ram = LiveStore(clock=lambda: seconds[0], wall=lambda: epoch + seconds[0])
    device, lease, other, session = [str(uuid.uuid4()) for _ in range(4)]
    principal = "a" * 64
    generation = ram.lease(device, lease, principal)["generation"]
    ram.lease(device, other, principal)
    ram.control(
        device,
        dict(
            session_id=session,
            session_start_us=epoch * 1000000,
            source_rate=400,
            raw_rate=400,
            preview_profile="raw400-box32-v1",
            firmware_version="test",
        ),
    )
    seconds[0] = 0.4
    body = dict(
        generation=generation,
        session_id=session,
        sequence=0,
        points=[dict(frame=159, samples=32, mv=1650.0)],
        rail=dict(
            start_us=epoch * 1000000,
            end_us=epoch * 1000000 + 400000,
            raw_samples=160,
            rail_samples=0,
        ),
    )
    ram.release(device, lease, principal)
    assert ram.push(device, body)["enabled"] is True
    ram.release(device, other, principal)
    with pytest.raises(DirectError) as closed:
        ram.push(device, body)
    assert closed.value.code == "live_not_requested"
