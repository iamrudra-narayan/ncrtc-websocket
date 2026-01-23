import asyncio
from datetime import datetime
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from script import SimulationManager 
import websockets

app = FastAPI()
from db_config import DbConfig
db_config_instance = DbConfig()

url = "ws://ncrtc-websocket.onrender.com"

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


# --------------------------------------------------------------------------------------------------------------------------------

# --- GLOBAL MEMORY CACHE ---
# Format: { "journey_uuid": { "train_no": "R1", "last_pos": 82150, "direction": "STOP", "mapped": True } }
active_train_cache = {}

# --- HELPER FUNCTIONS ---
async def get_train_details_from_db(current_km: float, current_time: str):
    """
    Corrected Logic: Uses both Location (KM) and Time to identify the train.
    """
    try:
        connection = await db_config_instance.get_db_connection_pool_async()
        async with connection.acquire() as conn:
            
            # --- STEP 1: Find Nearest Station based on KM ---
            # Hum check kar rahe hain ki train kis station ke 2km radius mein hai
            station_query = """
            SELECT station_code 
            FROM ncrtc.stations 
            ORDER BY ABS(position_km - $1) ASC 
            LIMIT 1;
            """
            station_row = await conn.fetchrow(station_query, current_km)
            
            if not station_row:
                return {"train_no": "Unknown", "name": "N/A"}
            
            nearest_station_code = station_row['station_code'] # e.g., 'A21'

            # --- STEP 2: Find Train scheduled at this Station + Time ---
            # Ab check karte hain: "Kaunsi train A21 se abhi (buffer time) nikalne wali hai?"
            train_query = """
            SELECT train_no, name, scheduled_station 
            FROM ncrtc.train_timetable_summary 
            WHERE 
                (start_station = $1 OR scheduled_station = $1 OR last_station = $1)
                AND 
                (start_time::time BETWEEN ($2)::time - interval '45 minutes' AND ($2)::time + interval '45 minutes')
            LIMIT 1;
            """
            
            row = await conn.fetchrow(train_query, nearest_station_code, current_time)
            
            if row:
                logger.info(f"Matched Train {row['train_no']} at {nearest_station_code}")
                return {"train_no": row['train_no'], "name": row['name'], "scheduled_station": row['scheduled_station']}
            else:
                # Agar exact timetable match nahi mila, toh generic return karo
                # (Real scenario mein hum shayad purani journeyId logic use karein)
                logger.warning(f"No train found in timetable for {nearest_station_code} at {current_time}")
                return {"train_no": "T-Unknown", "name": "N/A"}
                
    except Exception as e:
        logger.error(f"DB Lookup Error: {e}")
        return {"train_no": "N/A", "name": "N/A", "scheduled_station": "N/A"}


async def determine_direction(j_id, current_meters, head_meters, tail_meters):
    """
    Calculates direction based on Head/Tail or History.
    """
    # METHOD 1: Head vs Tail (Instant Check)
    if head_meters > tail_meters + 10:  # +10m buffer for noise
        return "FORWARD"
    elif head_meters < tail_meters - 10:
        return "BACKWARD"
    
    # METHOD 2: History Check (Fallback)
    if j_id in active_train_cache:
        last_pos = active_train_cache[j_id].get("last_pos")
        if last_pos is not None:
            if current_meters > last_pos + 0.5: # Moving FORWARD/UP
                return "FORWARD"
            elif current_meters < last_pos - 0.5: # Moving BACKWARD/DOWN
                return "BACKWARD"
            elif abs(current_meters - last_pos) <= 0.5:
                return "STOP"
    
    return "STOP"

async def process_payload(raw_payload):
    """
    Enriches the raw sensor data with DB details and direction.
    """
    processed_data = []
    
    # Check if 'trains' key exists, handle empty lists
    train_list = raw_payload.get("trains", [])
    if not train_list:
        return {"active_trains": []}

    for train in train_list:
        j_id = train.get("journeyId")
        if not j_id: continue

        current_meters = train.get("headLinePosition", 0)
        head_meters = train.get("headLinePosition", 0)
        tail_meters = train.get("tailLinePosition", 0)
        current_km = round(current_meters / 1000, 2)
        
        # 1. Calculate Direction
        direction_status = await determine_direction(j_id, current_meters, head_meters, tail_meters)
        is_moving_forward = "FORWARD" in direction_status

        # 2. Identify Train (DB Lookup if new)
        if j_id not in active_train_cache:
            # Fetch details from DB
            logger.info(f"New Journey {j_id} detected. Querying DB...")
            train_details = await get_train_details_from_db(
                current_km,
                datetime.now().time()
            )    
            active_train_cache[j_id] = {
                "train_no": train_details["train_no"],
                "name": train_details["name"],
                "last_pos": current_meters,
                "direction": direction_status,
                "scheduled_station": train_details.get("scheduled_station", "N/A"),
                "train_length": train.get("length", 0),
                "coach_count": 6 if train.get("length", 0) > 50 else 3
            }
        else:
            # Update cache with new position
            active_train_cache[j_id]["last_pos"] = current_meters
            active_train_cache[j_id]["direction"] = direction_status
        # 3. Construct Final Payload for Frontend
        cache_data = active_train_cache[j_id]
        
        enriched_train = {
            "train_id": cache_data["train_no"], # R1, R2, etc.
            "train_name": cache_data["name"],
            "journey_id": j_id,
            "live_status": {
                "speed": train.get("speed", 0),
                "position_km": current_km,
                "latitude": train.get("headGeoLocation", {}).get("latitude"),
                "longitude": train.get("headGeoLocation", {}).get("longitude"),
                "direction": direction_status,
                "is_moving_forward": is_moving_forward,
                "next_station": cache_data.get("scheduled_station", "N/A"),
                "train_length_meters": cache_data.get("train_length", 0),
                "coach_count": cache_data.get("coach_count", 0)
            },
            "visualization": {
                # Logic for progress bar can be calculated here or in FE
                "position_meters": current_meters
            }
        }
        processed_data.append(enriched_train)
        
    return {"active_trains": processed_data}


# --- WEBSOCKET PROXY ENDPOINT ---
@app.websocket("/ws/live-trains")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Frontend UI Connected to WebSocket Proxy")
    
    # THE SENSOR URL (Change this IP if your sensor IP changes)
    SENSOR_URL = f"{url}/trainJourneyEventPublisher/train-journey-events"
    # Note: If testing locally without sensor, you might need a mock server
    
    try:
        async with websockets.connect(SENSOR_URL) as sensor_ws:
            logger.info(f"Connected to Sensor at {SENSOR_URL}")
            
            while True:
                # A. Receive Raw Data from Sensor
                try:
                    raw_msg = await sensor_ws.recv()
                    raw_json = json.loads(raw_msg)
                except Exception as e:
                    logger.error(f"Error receiving from sensor: {e}")
                    break

                # B. Process/Enrich Data (Add DB details + Direction)
                final_response = await process_payload(raw_json)
                
                # C. Send to Frontend
                await websocket.send_json(final_response)
                
    except WebSocketDisconnect:
        logger.info("Frontend disconnected")
    except Exception as e:
        logger.error(f"WebSocket Proxy Error: {e}")
        await websocket.close()