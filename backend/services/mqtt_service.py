import json
import asyncio
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from datetime import datetime
import os
from dotenv import load_dotenv

# --- PERBAIKAN IMPORT ---
from services.db_service import (
    insert_mqtt_log, 
    insert_production, 
    finish_mo, 
    insert_mo_history_record # Pastikan fungsi ini sudah ada di services/db_service.py
)
from core import state
from core.state import *
from services.oee_service import calculate_oee
from ws.ws_manager import manager

load_dotenv()

BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))

mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
main_loop = None

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK] Connected to MQTT")
        client.subscribe("mes/#")
    else:
        print(f"[ERROR] MQTT Connection failed with code {rc}")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    topic = msg.topic
    
    server_time = datetime.now()

    # inject ke payload
    payload["timestamp"] = server_time.isoformat()
    
    if topic.startswith('mes/wc/'):
        
        if payload["status"] == "start" and payload["workcenter"] == "Conveyor1":
            if state.start_time is None:
                state.start_time = datetime.now()
                print("[TIMER] REAL Production Start:", state.start_time)
        
        try:
            insert_mqtt_log(topic, payload)
        except Exception as e:
            print("[ERROR] DB Log Error:", e)
        
        wc = payload.get("workcenter")
        
        if wc not in production_state["workcenters"]:
            production_state["workcenters"][wc] = {
                "status": "IDLE",
                "cycle": 0,
                "ok": 0,
                "ng": 0
            }
        
        if payload["status"] == "start":
            production_state["workcenters"][wc]["status"] = "RUNNING"

        elif payload["status"] == "done":
            production_state["workcenters"][wc]["status"] = "IDLE"
            production_state["workcenters"][wc]["cycle"] = payload.get("cycle_time", 0)

            if payload.get("result") == "ok":
                production_state["workcenters"][wc]["ok"] += 1
            elif payload.get("result") == "ng":
                production_state["workcenters"][wc]["ng"] += 1
    
        conveyor = production_state["workcenters"].get("Conveyor1")
        
        if conveyor:
            production_state["total"] = conveyor["ok"] + conveyor["ng"]
            production_state["ok"] = conveyor["ok"]
            production_state["ng"] = conveyor["ng"]
            
    calculate_oee()
    
    if topic == "mes/product":
        
        if payload.get("status") == "complete":
            
            try:
                insert_production(payload, state.current_mo_id)
                print(payload)
            except Exception as e:
                print("[ERROR] Insert Production Error:", e)

            if payload.get("result") == "ok":
                state.produced_count += 1

                # update progress ke Odoo
                if state.odoo:
                    state.odoo.update_production_qty(state.current_mo_id, state.produced_count)

                print(f"[PROGRESS] Progress sent to Odoo: {state.produced_count}")

            # cek apakah sudah selesai
            if state.produced_count >= state.production_target:
                print("[TARGET] Target reached -> closing MO")

                if state.odoo:
                    state.odoo.mark_mo_done(state.current_mo_id)
                
                # 1. Tandai MO selesai di tabel `mo`
                finish_mo(state.current_mo_id)
                
                # --- 2. TAMBAHAN: Simpan ke tabel `mo_history` ---
                try:
                    end_time_now = datetime.now()
                    
                    # Ambil angka aktual dari production_state
                    total_prod = production_state.get("total", 0)
                    ok_count = production_state.get("ok", 0)
                    ng_count = production_state.get("ng", 0)
                    
                    # Hitung yield (Persentase OK terhadap Total)
                    yield_rate = (ok_count / total_prod * 100) if total_prod > 0 else 0.0
                    
                    insert_mo_history_record(
                        mo_id=state.current_mo_id,
                        start_time=state.start_time,
                        end_time=end_time_now,
                        status="done",
                        total=total_prod,
                        ok=ok_count,
                        ng=ng_count,
                        yield_rate=round(yield_rate, 2)
                    )
                except Exception as e:
                    print(f"[ERROR] Gagal menyimpan ke mo_history: {e}")
                # -------------------------------------------------
                
    production_state["target"] = state.production_target
    production_state["progress"] = state.produced_count

    if main_loop:
        asyncio.run_coroutine_threadsafe(manager.broadcast(production_state), main_loop)
    
def start_mqtt():
    global main_loop
    try:
        main_loop = asyncio.get_event_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    
    try:
        mqtt_client.connect(BROKER, PORT, 60)
        mqtt_client.loop_start()
        print(f"[MQTT] Connecting to {BROKER}:{PORT}...")
    except Exception as e:
        print(f"[WARN] MQTT Broker not available ({BROKER}:{PORT}): {e}")
        print("   Backend will run without MQTT. Start broker and restart to enable.")
    
def publish(topic, payload):
    try:
        mqtt_client.publish(topic, json.dumps(payload))
        print(f"[MQTT SEND] {topic} -> {payload}")
    except Exception as e:
        print("[ERROR] MQTT Publish Error:", e)