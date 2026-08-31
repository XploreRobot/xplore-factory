import asyncio
from datetime import datetime
import os
import threading
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from core import state
from dotenv import load_dotenv

from routes.api import router as api_router
from routes.auth import router as auth_router
from ws.ws_manager import manager
from services.mqtt_service import start_mqtt, publish
from services.odoo_service import OdooService
from services.db_service import create_mo, finish_mo

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # sementara bebas (dev)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(auth_router, prefix="/auth")

# start MQTT (graceful - won't crash if broker is down)
start_mqtt()

# Connect to Odoo (graceful)
try:
    state.odoo = OdooService(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    if not state.odoo.uid:
        print("[WARN] Odoo not available. Backend will run without Odoo integration.")
        state.odoo = None
except Exception as e:
    print(f"[WARN] Odoo connection failed: {e}")
    print("   Backend will run without Odoo integration.")
    state.odoo = None

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await asyncio.sleep(1)  # keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def odoo_listener():
    while True:
        if not state.odoo:
            time.sleep(10)
            continue
            
        try:
            # 1. Cari MO yang statusnya 'Dikonfirmasi'
            mo = state.odoo.get_confirmed_mo()

            if mo and mo["id"] != state.current_mo_id:
                print(f"[TRIGGER] New Confirmed MO detected: {mo['name']}")

                # 2. Otomatis klik tombol "Mulai" di Odoo
                state.odoo.start_mo(mo["id"])

                # 3. Reset state backend
                state.reset_state()

                # 4. Set target baru
                state.current_mo_id = mo["id"]
                state.production_target = int(mo.get("product_qty", 10))
                state.production_state["target"] = state.production_target

                print(f"[START] Broadcasting start signal for {mo['name']} (Target: {state.production_target})")

                # 5. Otomatis nyalakan mesin via MQTT
                publish("mes/target", {"target": state.production_target})
                publish("mes/control", {"command": "start"})

                create_mo(mo["id"])
        except Exception as e:
            print(f"[WARN] Odoo listener error: {e}")

        time.sleep(5)
        
threading.Thread(target=odoo_listener, daemon=True).start()