"""Single-owner bounded RAM state, shared by all HTTP workers through a private socket."""

import hashlib
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from app.direct.live_protocol import RAW_PROFILE, CaptureSpec, MarkRequest, PreviewPacket
from app.direct.protocol import DirectError

HISTORY_SECONDS = 180
LEASE_SECONDS = 30
MAX_POINTS = 2251


@dataclass(slots=True)
class Point:
    cursor: int
    frame: int
    samples: int
    recorded_us: int
    mv: float


@dataclass
class SensorLive:
    created: float
    generation: str = field(default_factory=lambda: str(uuid.uuid4()))
    leases: dict = field(default_factory=dict)
    points: deque = field(default_factory=lambda: deque(maxlen=MAX_POINTS))
    markers: dict = field(default_factory=dict)
    spec: CaptureSpec | None = None
    control_seen: float = 0
    packet_seen: float | None = None
    packet_sequence: int = -1
    packet_digest: str = ""
    cursor: int = 0
    rail: dict | None = None


class LiveStore:
    def __init__(
        self,
        *,
        clock=time.monotonic,
        wall=time.time,
        max_devices=32,
        max_leases=128,
        per_device_leases=8,
        max_markers=64,
    ):
        self.clock, self.wall = clock, wall
        self.max_devices, self.max_leases = max_devices, max_leases
        self.per_device_leases, self.max_markers = per_device_leases, max_markers
        self.devices = {}
        self.lock = threading.RLock()

    def _prune(self):
        now, cutoff = self.clock(), int((self.wall() - HISTORY_SECONDS) * 1e6)
        for key, state in list(self.devices.items()):
            state.leases = {k: v for k, v in state.leases.items() if v[1] > now}
            while state.points and state.points[0].recorded_us < cutoff:
                state.points.popleft()
            state.markers = {k: v for k, v in state.markers.items() if v[0] > now}
            if not state.leases and not state.points and not state.markers:
                del self.devices[key]

    def _state(self, device):
        state = self.devices.get(device)
        if state is None:
            raise DirectError(404, "live_not_found")
        return state

    def _viewer(self, device, lease, principal):
        state = self._state(device)
        existing = state.leases.get(lease)
        if not existing or existing[0] != principal:
            raise DirectError(404, "live_lease_not_found")
        return state

    def lease(self, device, lease, principal):
        with self.lock:
            self._prune()
            state = self.devices.get(device)
            if state is None:
                if len(self.devices) >= self.max_devices:
                    raise DirectError(429, "live_device_limit")
                state = self.devices[device] = SensorLive(created=self.clock())
            previous = state.leases.get(lease)
            if previous and previous[0] != principal:
                raise DirectError(404, "live_lease_not_found")
            if not previous and (
                len(state.leases) >= self.per_device_leases
                or sum(len(s.leases) for s in self.devices.values()) >= self.max_leases
                or sum(v[0] == principal for s in self.devices.values() for v in s.leases.values())
                >= 16
            ):
                raise DirectError(429, "live_viewer_limit")
            if not state.leases:
                state.generation = str(uuid.uuid4())
                state.packet_sequence = -1
                state.packet_digest = ""
            state.leases[lease] = (principal, self.clock() + LEASE_SECONDS)
            return {
                "lease_id": lease,
                "expires_in_ms": LEASE_SECONDS * 1000,
                "generation": state.generation,
            }

    def release(self, device, lease, principal):
        with self.lock:
            self._prune()
            state = self.devices.get(device)
            if state and lease in state.leases:
                self._viewer(device, lease, principal)
                del state.leases[lease]
            return {"released": True}

    def control(self, device, spec):
        with self.lock:
            self._prune()
            state = self.devices.get(device)
            if not state or not state.leases:
                return {"enabled": False, "expires_in_ms": 0}
            parsed = CaptureSpec.model_validate(spec)
            if state.spec and state.spec != parsed:
                if state.spec.session_id == parsed.session_id:
                    raise DirectError(409, "live_session_configuration_conflict")
                state.points.clear()
                state.markers.clear()
                state.rail = None
                state.packet_sequence = -1
                state.packet_digest = ""
                state.packet_seen = None
                state.generation = str(uuid.uuid4())
            state.spec, state.control_seen = parsed, self.clock()
            milliseconds = max(
                0, int((max(v[1] for v in state.leases.values()) - self.clock()) * 1000)
            )
            return {
                "enabled": milliseconds > 0,
                "expires_in_ms": milliseconds,
                "generation": state.generation,
                "packet_interval_ms": 400,
            }

    def push(self, device, body):
        with self.lock:
            self._prune()
            state = self._state(device)
            packet = PreviewPacket.model_validate(body)
            if not state.leases:
                raise DirectError(409, "live_not_requested")
            if not state.spec or str(packet.session_id) != str(state.spec.session_id):
                raise DirectError(409, "live_session_mismatch")
            if str(packet.generation) != state.generation:
                raise DirectError(409, "live_generation_expired")
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if packet.sequence <= state.packet_sequence:
                if packet.sequence == state.packet_sequence and digest == state.packet_digest:
                    state.control_seen = self.clock()
                    return self._push_ack(state, "duplicate")
                raise DirectError(409, "live_sequence_conflict")
            if state.packet_seen is not None and self.clock() - state.packet_seen < 0.2:
                raise DirectError(429, "live_packet_rate")
            spec = state.spec
            now_us = int(self.wall() * 1e6)
            last_frame = state.points[-1].frame if state.points else -1
            prepared = []
            for point in packet.points:
                count_ok = (
                    point.samples == 32
                    if spec.preview_profile == RAW_PROFILE
                    else point.samples in (30, 31)
                )
                recorded = spec.session_start_us + point.frame * 1_000_000 // spec.source_rate
                if (
                    not count_ok
                    or point.frame < point.samples - 1
                    or point.frame - last_frame < point.samples
                    or not now_us - 5_000_000 <= recorded <= now_us + 100_000
                ):
                    raise DirectError(422, "invalid_live_sample_reference")
                prepared.append((point, recorded))
                last_frame = point.frame
            rail = packet.rail
            if (
                rail.end_us > now_us + 100_000
                or rail.end_us < now_us - 2_000_000
                or rail.start_us < spec.session_start_us
                or rail.raw_samples > spec.raw_rate * 10
                or rail.raw_samples
                > ((rail.end_us - rail.start_us) * spec.raw_rate + 999999) // 1000000
                or rail.end_us < prepared[-1][1]
            ):
                raise DirectError(422, "invalid_rail_window")
            # Commit the complete packet only after validating every point/window.
            for point, recorded in prepared:
                state.cursor += 1
                state.points.append(
                    Point(state.cursor, point.frame, point.samples, recorded, point.mv)
                )
            state.rail = rail.model_dump()
            state.packet_seen = self.clock()
            state.packet_sequence, state.packet_digest = packet.sequence, digest
            state.control_seen = self.clock()
            return self._push_ack(state, "accepted")

    def _push_ack(self, state, status):
        # A validated upload also confirms demand, avoiding a second round trip
        # on the device's single TLS connection. Old clients ignore these fields.
        remaining = max(v[1] for v in state.leases.values()) - self.clock()
        return {
            "status": status,
            "enabled": remaining > 0,
            "expires_in_ms": max(0, min(LEASE_SECONDS * 1000, int(remaining * 1000))),
            "generation": state.generation,
        }

    def preview(self, device, lease, principal, *, seconds=30, after=0, generation=""):
        with self.lock:
            self._prune()
            state = self._viewer(device, lease, principal)
            if seconds not in (30, 60, 180) or not 0 <= after <= 2**53 - 1:
                raise DirectError(422, "invalid_live_window")
            cutoff = int((self.wall() - seconds) * 1e6)
            reset = generation != state.generation
            visible = [p for p in state.points if p.recorded_us >= cutoff]
            points = visible if reset else [p for p in visible if p.cursor > after]
            stale = (
                state.packet_seen is None
                or self.clock() - state.packet_seen > 1.5
                or not state.points
                or int(self.wall() * 1e6) - state.points[-1].recorded_us > 1_500_000
            )
            capability = (
                "ready"
                if state.spec and self.clock() - state.control_seen <= 3
                else (
                    "offline"
                    if state.spec
                    else "checking"
                    if self.clock() - state.created < 3
                    else "upgrade_required"
                )
            )
            rail = None
            if state.rail is not None:
                r = state.rail
                rail = {
                    **r,
                    "percent": 100 * r["rail_samples"] / r["raw_samples"]
                    if r["raw_samples"]
                    else None,
                    "stale": stale or int(self.wall() * 1e6) - r["end_us"] > 1_500_000,
                    "complete": r["end_us"] - r["start_us"] == 10_000_000
                    and r["raw_samples"] == state.spec.raw_rate * 10,
                }
            complete = bool(visible and visible[0].recorded_us <= cutoff + 160_000 and not stale)
            if complete:
                complete = all(
                    b.frame - a.frame == b.samples
                    for a, b in zip(visible, visible[1:], strict=False)
                )
            return {
                "generation": state.generation,
                "session_id": str(state.spec.session_id) if state.spec else None,
                "preview_profile": state.spec.preview_profile if state.spec else None,
                "capability": capability,
                "stale": stale,
                "window_complete": complete,
                "cursor": state.cursor,
                "reset": reset,
                "server_us": int(self.wall() * 1e6),
                "points": [
                    {
                        "cursor": p.cursor,
                        "frame": p.frame,
                        "samples": p.samples,
                        "recorded_us": p.recorded_us,
                        "mv": p.mv,
                    }
                    for p in points
                ],
                "rail": rail,
                "markers": [
                    value[1] for value in state.markers.values() if value[1]["at_us"] >= cutoff
                ],
            }

    def mark(self, device, lease, principal, marker_id, body):
        with self.lock:
            self._prune()
            state = self._viewer(device, lease, principal)
            mark = MarkRequest.model_validate(body)
            if (
                not state.spec
                or str(mark.session_id) != str(state.spec.session_id)
                or str(mark.generation) != state.generation
            ):
                raise DirectError(409, "live_session_mismatch")
            existing = state.markers.get(marker_id)
            if existing:
                if existing[2] != principal or existing[1]["frame"] != mark.frame:
                    raise DirectError(409, "marker_identity_conflict")
                return existing[1]
            point = next((p for p in reversed(state.points) if p.frame == mark.frame), None)
            now_us = int(self.wall() * 1e6)
            if point is None or now_us - point.recorded_us > 1_500_000:
                raise DirectError(409, "marker_requires_fresh_sample")
            if len(state.markers) >= self.max_markers:
                raise DirectError(429, "live_marker_limit")
            if any(
                v[2] == principal and v[0] > self.clock() + HISTORY_SECONDS - 0.5
                for v in state.markers.values()
            ):
                raise DirectError(429, "live_marker_rate")
            result = {
                "id": marker_id,
                "session_id": str(mark.session_id),
                "frame": point.frame,
                "at_us": point.recorded_us,
                "clicked_at_us": now_us,
            }
            state.markers[marker_id] = (self.clock() + HISTORY_SECONDS, result, principal)
            return result

    def dispatch(self, request):
        namespace = request["namespace"]
        if (
            not isinstance(namespace, str)
            or len(namespace) != 64
            or any(c not in "0123456789abcdef" for c in namespace)
        ):
            raise DirectError(422, "invalid_live_namespace")
        op, device = request["op"], namespace + ":" + str(uuid.UUID(request["device"]))
        if op == "control":
            return self.control(device, request["spec"])
        if op == "push":
            return self.push(device, request["packet"])
        principal = request["principal"]
        if not isinstance(principal, str) or len(principal) != 64:
            raise DirectError(422, "invalid_live_principal")
        lease = str(uuid.UUID(request["lease"]))
        if op == "lease":
            return self.lease(device, lease, principal)
        if op == "release":
            return self.release(device, lease, principal)
        if op == "preview":
            return self.preview(
                device,
                lease,
                principal,
                seconds=request["seconds"],
                after=request["after"],
                generation=request["generation"],
            )
        if op == "mark":
            return self.mark(
                device, lease, principal, str(uuid.UUID(request["marker"])), request["mark"]
            )
        raise DirectError(422, "invalid_live_operation")
