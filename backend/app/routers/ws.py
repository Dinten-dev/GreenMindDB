"""WebSocket routes for live data streaming."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth import COOKIE_NAME, decode_token

router = APIRouter(prefix="/ws", tags=["websocket"])

MAX_CONNECTIONS = 500
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.sensor_connections: dict[str, list[WebSocket]] = {}
        self.total_connections = 0

    async def connect(self, websocket: WebSocket, zone_id: str) -> bool:
        if self.total_connections >= MAX_CONNECTIONS:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            return False

        await websocket.accept()
        if zone_id not in self.active_connections:
            self.active_connections[zone_id] = []
        self.active_connections[zone_id].append(websocket)
        self.total_connections += 1
        return True

    def disconnect(self, websocket: WebSocket, zone_id: str):
        if zone_id in self.active_connections:
            if websocket in self.active_connections[zone_id]:
                self.active_connections[zone_id].remove(websocket)
                self.total_connections -= 1
            if not self.active_connections[zone_id]:
                del self.active_connections[zone_id]

    async def connect_sensor(self, websocket: WebSocket, sensor_id: str) -> bool:
        if self.total_connections >= MAX_CONNECTIONS:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            return False

        await websocket.accept()
        if sensor_id not in self.sensor_connections:
            self.sensor_connections[sensor_id] = []
        self.sensor_connections[sensor_id].append(websocket)
        self.total_connections += 1
        return True

    def disconnect_sensor(self, websocket: WebSocket, sensor_id: str):
        if sensor_id in self.sensor_connections:
            if websocket in self.sensor_connections[sensor_id]:
                self.sensor_connections[sensor_id].remove(websocket)
                self.total_connections -= 1
            if not self.sensor_connections[sensor_id]:
                del self.sensor_connections[sensor_id]

    async def broadcast_to_zone(self, message: dict, zone_id: str):
        if zone_id in self.active_connections:
            for connection in list(self.active_connections[zone_id]):
                try:
                    await connection.send_json(message)
                except Exception:
                    self.disconnect(connection, zone_id)

    async def broadcast_to_sensor(self, message: dict, sensor_id: str):
        if sensor_id in self.sensor_connections:
            for connection in list(self.sensor_connections[sensor_id]):
                try:
                    await connection.send_json(message)
                except Exception:
                    self.disconnect_sensor(connection, sensor_id)


manager = ConnectionManager()


def _authenticate_websocket(websocket: WebSocket) -> bool:
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return False
    payload = decode_token(token)
    if not payload:
        return False
    return True


@router.websocket("/zone/{zone_id}")
async def websocket_endpoint(websocket: WebSocket, zone_id: str):
    if not _authenticate_websocket(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if not await manager.connect(websocket, zone_id):
        return

    try:
        while True:
            # Keep connection open waiting for ping/messages from client, with timeout
            await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        manager.disconnect(websocket, zone_id)


@router.websocket("/sensor/{sensor_id}")
async def sensor_websocket_endpoint(websocket: WebSocket, sensor_id: str):
    """Per-sensor WebSocket for real-time live view in the dashboard."""
    if not _authenticate_websocket(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if not await manager.connect_sensor(websocket, sensor_id):
        return

    try:
        while True:
            await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        manager.disconnect_sensor(websocket, sensor_id)

