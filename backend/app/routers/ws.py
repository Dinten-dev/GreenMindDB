"""Authenticated, tenant-scoped WebSocket routes for live data streaming."""

import asyncio
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, decode_token
from app.config import settings
from app.database import get_db
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    """Track subscriptions and enforce process-local connection safeguards."""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.sensor_connections: dict[str, list[WebSocket]] = {}
        self._metadata: dict[WebSocket, tuple[str, str, str, str]] = {}
        self._user_counts: Counter[str] = Counter()
        self._ip_counts: Counter[str] = Counter()

    @property
    def total_connections(self) -> int:
        return len(self._metadata)

    async def _connect(
        self,
        websocket: WebSocket,
        subscription_type: str,
        subscription_id: str,
        user_id: str,
        client_ip: str,
    ) -> bool:
        # Accept before checking the counters so concurrent handshakes cannot all
        # pass a pre-await capacity check and oversubscribe the process.
        await websocket.accept()
        if (
            self.total_connections >= settings.websocket_max_connections
            or self._user_counts[user_id] >= settings.websocket_max_connections_per_user
            or self._ip_counts[client_ip] >= settings.websocket_max_connections_per_ip
        ):
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            return False

        subscriptions = (
            self.active_connections if subscription_type == "zone" else self.sensor_connections
        )
        subscriptions.setdefault(subscription_id, []).append(websocket)
        self._metadata[websocket] = (
            subscription_type,
            subscription_id,
            user_id,
            client_ip,
        )
        self._user_counts[user_id] += 1
        self._ip_counts[client_ip] += 1
        return True

    async def connect(
        self,
        websocket: WebSocket,
        zone_id: str,
        user_id: str,
        client_ip: str,
    ) -> bool:
        return await self._connect(websocket, "zone", zone_id, user_id, client_ip)

    async def connect_sensor(
        self,
        websocket: WebSocket,
        sensor_id: str,
        user_id: str,
        client_ip: str,
    ) -> bool:
        return await self._connect(websocket, "sensor", sensor_id, user_id, client_ip)

    def disconnect(self, websocket: WebSocket, zone_id: str | None = None) -> None:
        """Remove a connection once; ``zone_id`` is retained for caller compatibility."""
        metadata = self._metadata.pop(websocket, None)
        if not metadata:
            return
        subscription_type, subscription_id, user_id, client_ip = metadata
        subscriptions = (
            self.active_connections if subscription_type == "zone" else self.sensor_connections
        )
        connections = subscriptions.get(subscription_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            subscriptions.pop(subscription_id, None)

        self._user_counts[user_id] -= 1
        self._ip_counts[client_ip] -= 1
        if self._user_counts[user_id] <= 0:
            del self._user_counts[user_id]
        if self._ip_counts[client_ip] <= 0:
            del self._ip_counts[client_ip]

    def disconnect_sensor(self, websocket: WebSocket, sensor_id: str | None = None) -> None:
        self.disconnect(websocket, sensor_id)

    async def _send(self, connection: WebSocket, message: dict) -> None:
        try:
            await asyncio.wait_for(
                connection.send_json(message),
                timeout=settings.websocket_send_timeout_seconds,
            )
        except Exception:
            self.disconnect(connection)
            try:
                await connection.close()
            except Exception:
                return

    async def _broadcast(
        self,
        subscriptions: dict[str, list[WebSocket]],
        message: dict,
        subscription_id: str,
    ) -> None:
        connections = list(subscriptions.get(subscription_id, []))
        if connections:
            await asyncio.gather(
                *(self._send(connection, message) for connection in connections),
                return_exceptions=True,
            )

    async def broadcast_to_zone(self, message: dict, zone_id: str) -> None:
        await self._broadcast(self.active_connections, message, zone_id)

    async def broadcast_to_sensor(self, message: dict, sensor_id: str) -> None:
        await self._broadcast(self.sensor_connections, message, sensor_id)


manager = ConnectionManager()


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return False
    allowed_origins = settings.cors_origins
    if isinstance(allowed_origins, str):
        allowed_origins = [allowed_origins]
    return origin.rstrip("/") in {item.rstrip("/") for item in allowed_origins}


def _authenticate_websocket(websocket: WebSocket, db: Session) -> User | None:
    """Resolve the cookie to a current active and verified database user."""
    if not _origin_allowed(websocket):
        return None
    token = websocket.cookies.get(COOKIE_NAME)
    payload = decode_token(token) if token else None
    user_id = payload.get("sub") if payload else None
    try:
        user_uuid = uuid.UUID(user_id) if user_id else None
    except (TypeError, ValueError, AttributeError):
        return None
    if not user_uuid:
        return None
    return (
        db.query(User)
        .filter(
            User.id == user_uuid,
            User.is_active.is_(True),
            User.is_verified.is_(True),
        )
        .first()
    )


def _client_ip(websocket: WebSocket) -> str:
    return websocket.client.host if websocket.client else "unknown"


async def _reject(websocket: WebSocket) -> None:
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


@router.websocket("/zone/{zone_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    zone_id: str,
    db: Session = Depends(get_db),
):
    user = _authenticate_websocket(websocket, db)
    try:
        zone_uuid = uuid.UUID(zone_id)
    except (ValueError, AttributeError):
        await _reject(websocket)
        return
    zone = (
        db.query(Zone)
        .filter(Zone.id == zone_uuid, Zone.organization_id == user.organization_id)
        .first()
        if user and user.organization_id
        else None
    )
    if not user or not zone:
        await _reject(websocket)
        return

    if not await manager.connect(websocket, str(zone.id), str(user.id), _client_ip(websocket)):
        return

    try:
        while True:
            await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.websocket_idle_timeout_seconds,
            )
    except (TimeoutError, WebSocketDisconnect):
        pass
    finally:
        manager.disconnect(websocket)


@router.websocket("/sensor/{sensor_id}")
async def sensor_websocket_endpoint(
    websocket: WebSocket,
    sensor_id: str,
    db: Session = Depends(get_db),
):
    """Per-sensor WebSocket scoped to the authenticated user's organization."""
    user = _authenticate_websocket(websocket, db)
    try:
        sensor_uuid = uuid.UUID(sensor_id)
    except (ValueError, AttributeError):
        await _reject(websocket)
        return
    sensor = (
        db.query(Sensor)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Sensor.id == sensor_uuid,
            Zone.organization_id == user.organization_id,
        )
        .first()
        if user and user.organization_id
        else None
    )
    if not user or not sensor:
        await _reject(websocket)
        return

    if not await manager.connect_sensor(
        websocket, str(sensor.id), str(user.id), _client_ip(websocket)
    ):
        return

    try:
        while True:
            await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.websocket_idle_timeout_seconds,
            )
    except (TimeoutError, WebSocketDisconnect):
        pass
    finally:
        manager.disconnect(websocket)
