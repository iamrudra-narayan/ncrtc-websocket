import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from script import SimulationManager 

app = FastAPI()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allowed origins for CORS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
) 

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = [] # List of active WebSocket connections

    # Add to active connections
    async def connect(self, websocket: WebSocket):
        await websocket.accept() # Accept the WebSocket connection
        self.active_connections.append(websocket) # Add to active connections

    # Remove from active connections
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    # Broadcast message to all active connections
    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        
        json_str = json.dumps(message)
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(json_str)
            except:
                self.disconnect(connection)

manager = ConnectionManager()
sim_manager = SimulationManager()

# --- BACKGROUND TASK ---
async def run_simulation_loop():
    print("🚀 NCRTC Simulation Loop Started...")
    while True:
        # Request new data from script.py
        train_data = sim_manager.tick()
        
        # If any train is on the track, broadcast data
        if train_data:
            # Payload wrapper
            final_payload = {"trains": train_data}
            await manager.broadcast(final_payload)
        
        # Wait for 1 second before next tick
        await asyncio.sleep(1)


@app.on_event("startup")
async def startup_event():
    # Background Task
    asyncio.create_task(run_simulation_loop())


# --- WEBSOCKET ENDPOINT ---
@app.websocket("/trainJourneyEventPublisher/train-journey-events")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Read messages from client (if any)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ------------------------------------------------------------------------------------------------------------------------------