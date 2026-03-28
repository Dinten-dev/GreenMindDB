"""WebSocket routes for live data streaming."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, zone_id: str):
        await websocket.accept()
        if zone_id not in self.active_connections:
            self.active_connections[zone_id] = []
        self.active_connections[zone_id].append(websocket)

    def disconnect(self, websocket: WebSocket, zone_id: str):
        if zone_id in self.active_connections:
            if websocket in self.active_connections[zone_id]:
                self.active_connections[zone_id].remove(websocket)
            if not self.active_connections[zone_id]:
                del self.active_connections[zone_id]

    async def broadcast_to_zone(self, message: dict, zone_id: str):
        if zone_id in self.active_connections:
            for connection in list(self.active_connections[zone_id]):
                try:
                    await connection.send_json(message)
                except Exception:
                    self.disconnect(connection, zone_id)


manager = ConnectionManager()


@router.websocket("/zone/{zone_id}")
async def websocket_endpoint(websocket: WebSocket, zone_id: str):
    await manager.connect(websocket, zone_id)
    try:
        while True:
            # Keep connection open waiting for ping/messages from client
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, zone_id)
