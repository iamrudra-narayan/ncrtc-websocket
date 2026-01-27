import uuid
import random
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- CONFIGURATION (NCRTC RRTS: Delhi <-> Meerut) ---
TRACK_LENGTH_METERS = 82150.0  
START_GEO = (28.5905, 77.2526)  # Sahibabad Side
END_GEO = (29.0772, 77.7169)    # Modipuram Side

class NCRTCTrain:
    def __init__(self):
        self.journey_id = str(uuid.uuid4())
        self.sensor_id = str(uuid.uuid4())
        self.length = 114.85715 # Exact length from your payload
        
        # Direction: 1 (Delhi -> Meerut), -1 (Meerut -> Delhi)
        self.direction = 1 if random.random() > 0.5 else -1
        
        if self.direction == 1:
            self.current_pos_meters = 0.0 
            self.target_speed = random.uniform(28.0, 42.0) # ~100-150 km/h
        else:
            self.current_pos_meters = TRACK_LENGTH_METERS
            self.target_speed = random.uniform(28.0, 42.0) * -1
            
        self.current_speed = 0.0 
        self.is_finished = False
        self.last_update_time = time.time()

    def interpolate_geo(self, meters):
        """Meters ke hisaab se Lat/Lon nikalna"""
        fraction = meters / TRACK_LENGTH_METERS
        fraction = max(0, min(1, fraction))
        lat = START_GEO[0] + (END_GEO[0] - START_GEO[0]) * fraction
        lon = START_GEO[1] + (END_GEO[1] - START_GEO[1]) * fraction
        return lat, lon

    def update_physics(self):
        """Train ko aage badhana"""
        now = time.time()
        time_delta = now - self.last_update_time
        self.last_update_time = now
        
        # Smooth Acceleration
        if abs(self.current_speed) < abs(self.target_speed):
            self.current_speed += (2.0 * self.direction) * time_delta
            
        # Add real-world noise
        live_speed = self.current_speed + random.uniform(-0.5, 0.5)
        
        # Move Train
        distance_moved = live_speed * time_delta
        self.current_pos_meters += distance_moved
        
        # Check if journey ended
        if self.direction == 1 and self.current_pos_meters >= TRACK_LENGTH_METERS:
            self.is_finished = True
        elif self.direction == -1 and self.current_pos_meters <= 0:
            self.is_finished = True
            
        return abs(live_speed)

    def get_payload(self):
        """Woh exact JSON structure generate karna jo aapne manga tha"""
        speed_abs = self.update_physics()
        
        # Optical Positions Calculation
        if self.direction == 1:
            head_opt = self.current_pos_meters
            tail_opt = self.current_pos_meters - self.length
        else:
            head_opt = self.current_pos_meters
            tail_opt = self.current_pos_meters + self.length

        # Clamp values (0 se neeche na jaye)
        head_opt = max(0, min(TRACK_LENGTH_METERS, head_opt))
        tail_opt = max(0, min(TRACK_LENGTH_METERS, tail_opt))

        # Left/Right logic (Left is always the smaller value on fiber)
        left_opt = min(head_opt, tail_opt)
        right_opt = max(head_opt, tail_opt)

        head_lat, head_lon = self.interpolate_geo(head_opt)
        tail_lat, tail_lon = self.interpolate_geo(tail_opt)
        
        # Raw Offset (simulating hardware calibration diff)
        raw_offset = 2105.71 

        # --- FINAL JSON STRUCTURE ---
        return {
            "journeyId": self.journey_id,
            "sensorId": self.sensor_id,
            "eventTime": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "length": self.length,
            "predicted": False,
            "speed": round(speed_abs, 4),
            
            # Optical Positions
            "leftOpticalPosition": round(left_opt, 2),
            "rightOpticalPosition": round(right_opt, 2),
            "signalLeftOpticalPosition": round(left_opt + random.uniform(-1, 1), 3),
            "signalRightOpticalPosition": round(right_opt + random.uniform(-1, 1), 3),
            
            # Head Data
            "headOpticalPosition": round(head_opt, 2),
            "headOpticalPositionRaw": round(head_opt + raw_offset, 3),
            "headGeoLocation": {
                "latitude": head_lat,
                "longitude": head_lon
            },
            "headChannel": int(head_opt // 6.4),
            "headLinePosition": round(head_opt, 3),
            "headLinePositionUnit": "KP",
            "headOpticalPositionConfidence": 0,
            
            # Tail Data
            "tailOpticalPosition": round(tail_opt, 2),
            "tailOpticalPositionRaw": round(tail_opt + raw_offset, 3),
            "tailGeoLocation": {
                "latitude": tail_lat,
                "longitude": tail_lon
            },
            "tailChannel": int(tail_opt // 6.4),
            "tailLinePosition": round(tail_opt, 3),
            "tailLinePositionUnit": "KP",
            
            "linePosition": round((head_opt + tail_opt) / 2, 3)
        }

# --- MANAGER CLASS ---
class SimulationManager:
    def __init__(self):
        self.active_trains = []

    def tick(self):
        """Har second call hoga: Nayi train layega aur purani hatayega"""
        
        # 1. Spawn New Train (Max 4 active)
        if len(self.active_trains) < 4 and random.random() < 0.05:
            self.active_trains.append(NCRTCTrain())

        # 2. Collect Data
        payloads = []
        finished_trains = []

        for train in self.active_trains:
            if train.is_finished:
                finished_trains.append(train)
                continue
            payloads.append(train.get_payload())

        # 3. Cleanup
        for t in finished_trains:
            self.active_trains.remove(t)
            
        return payloads