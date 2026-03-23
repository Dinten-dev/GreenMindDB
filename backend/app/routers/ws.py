"""WebSocket routes for live data streaming."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, greenhouse_id: str):
        await websocket.accept()
        if greenhouse_id not in self.active_connections:
            self.active_connections[greenhouse_id] = []
        self.active_connections[greenhouse_id].append(websocket)

    def disconnect(self, websocket: WebSocket, greenhouse_id: str):
        if greenhouse_id in self.active_connections:
            if websocket in self.active_connections[greenhouse_id]:
                self.active_connections[greenhouse_id].remove(websocket)
            if not self.active_connections[greenhouse_id]:
                del self.active_connections[greenhouse_id]

    async def broadcast_to_greenhouse(self, message: dict, greenhouse_id: str):
        if greenhouse_id in self.active_connections:
            for connection in list(self.active_connections[greenhouse_id]):
                try:
                    await connection.send_json(message)
                except Exception:
                    self.disconnect(connection, greenhouse_id)


manager = ConnectionManager()


@router.websocket("/greenhouse/{greenhouse_id}")
async def websocket_endpoint(websocket: WebSocket, greenhouse_id: str):
    await manager.connect(websocket, greenhouse_id)
    try:
        while True:
            # Keep connection open waiting for ping/messages from client
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, greenhouse_id)
