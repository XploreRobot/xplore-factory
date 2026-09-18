import asyncio
from datetime import datetime
import os
import threading
import time
import xmlrpc.client

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
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

# Endpoint Cancel MO untuk Manufacturing Orders
@app.post("/cancel_mo/{mo_id}")
async def cancel_manufacturing_order(mo_id: str):
    if not state.odoo:
        raise HTTPException(status_code=503, detail="Odoo service is not connected")
        
    try:
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(ODOO_URL))
        
        # Cari Odoo ID berdasarkan angka atau string referensi MO
        odoo_id = None
        if mo_id.isdigit():
            odoo_id = int(mo_id)
        else:
            mo_records = models.execute_kw(
                ODOO_DB, state.odoo.uid, ODOO_PASSWORD,
                'mrp.production', 'search',
                [[('name', '=', mo_id)]]
            )
            if not mo_records:
                raise HTTPException(status_code=404, detail=f"MO '{mo_id}' not found in Odoo")
            odoo_id = mo_records[0]

        # Eksekusi fungsi pembatalan bawaan Odoo (action_cancel)
        success = models.execute_kw(
            ODOO_DB, state.odoo.uid, ODOO_PASSWORD,
            'mrp.production', 'action_cancel',
            [[odoo_id]]
        )

        if success:
            # Jika MO yang dicancel adalah yang sedang aktif, stop mesin via MQTT
            if str(state.current_mo_id) == str(odoo_id) or str(state.current_mo_id) == mo_id:
                publish("mes/control", {"command": "stop"})
                print(f"[STOP] MO {mo_id} cancelled. Stopping machines.")
                state.reset_state()
                
            return {"status": "success", "message": f"MO {mo_id} cancelled successfully"}
        else:
            raise HTTPException(status_code=400, detail="Failed to cancel MO in Odoo. It might already be Done or Cancelled.")

    except Exception as e:
        print(f"[ERROR] Cancel MO: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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