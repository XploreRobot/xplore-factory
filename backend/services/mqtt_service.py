import json
import asyncio
import threading  # NEW
import time  # NEW
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

# NEW: heartbeat AGV -- deteksi koneksi ASLI, bukan tebakan timeout per-langkah produksi.
# Firmware AGV publish ke <prefix>/monitor/* tiap ~1.5 detik SELAMA dia hidup & terhubung
# (lihat komentar sebelumnya soal Ticker/HMI.ino). Kalau backend tidak terima APA PUN
# dari topic itu lebih dari AGV_HEARTBEAT_TIMEOUT detik, itu baru benar-benar berarti
# AGV/koneksinya mati -- independen dari sedang di tahap mana pun dalam siklus produksi.
#
# NEW: sekarang ada 2 unit AGV (agv1/agv2) yang harus dipantau INDEPENDEN -- AGV1 mati
# tidak boleh membuat AGV2 ikut ditandai DISCONNECTED atau sebaliknya. Makanya
# _agv_last_seen sekarang dict per-prefix, bukan 1 variabel global untuk 1 unit.
_agv_last_seen = {prefix: None for prefix in state.AGV_PREFIXES}
AGV_HEARTBEAT_TIMEOUT = 5  # detik -- beri margin dari interval publish asli (~1.5 detik)

def _agv_heartbeat_watcher():
    """Jalan terus di background thread terpisah, cek berkala apakah MASING-MASING AGV masih 'hidup'."""
    while True:
        time.sleep(2)
        changed = False

        for prefix in state.AGV_PREFIXES:
            last_seen = _agv_last_seen[prefix]
            if last_seen is None:
                continue  # belum pernah terima pesan AGV ini sama sekali sejak backend nyala

            elapsed = time.time() - last_seen
            current_status = production_state["agv"][prefix].get("connection_status")

            if elapsed > AGV_HEARTBEAT_TIMEOUT and current_status != "DISCONNECTED":
                production_state["agv"][prefix]["connection_status"] = "DISCONNECTED"
                print(f"[WARN] {prefix} tidak mengirim data lebih dari {AGV_HEARTBEAT_TIMEOUT} detik -- dianggap terputus")
                changed = True

        if changed and main_loop:
            asyncio.run_coroutine_threadsafe(manager.broadcast(production_state), main_loop)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK] Connected to MQTT")
        production_state["mqtt_connected"] = True  # NEW
        client.subscribe("mes/#")
        # NEW: dengarkan status & live tracking KEDUA unit AGV asli (agv1 & agv2),
        # bukan cuma agv2 seperti sebelumnya -- sekarang keduanya aktif bergantian.
        for _prefix in state.AGV_PREFIXES:
            client.subscribe(f"{_prefix}/monitor/#")
    else:
        print(f"[ERROR] MQTT Connection failed with code {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic

    # NEW: tangani topic status/live-tracking AGV asli (<prefix>/monitor/*, prefix =
    # "agv1" ATAU "agv2") PALING AWAL, SEBELUM json.loads() dipanggil. Ini penting
    # karena "<prefix>/monitor/state" adalah PLAIN TEXT (bukan JSON) -- kalau dipaksa
    # json.loads() akan gagal parse / silent-drop.
    #
    # NEW: dulu hanya "agv2/monitor/" yang ditangani (1 unit). Sekarang cek KEDUA
    # prefix dan ambil prefix aktual dari topic itu sendiri, supaya 1 handler ini
    # otomatis cover agv1 maupun agv2 tanpa duplikasi kode, dan data masing-masing
    # unit disimpan ke slotnya sendiri (production_state["agv"][prefix]) -- tidak
    # saling menimpa seperti sebelumnya.
    agv_prefix = next((p for p in state.AGV_PREFIXES if topic.startswith(f"{p}/monitor/")), None)
    if agv_prefix:
        raw_text = msg.payload.decode(errors="ignore")
        agv_slot = production_state["agv"][agv_prefix]

        # NEW: pesan apa pun dari topic monitor AGV ini = bukti dia masih hidup & terhubung.
        # Update heartbeat & status CONNECTED di sini, SEBELUM parsing spesifik per-topic
        # di bawah -- supaya recovery ("AGV kembali online") langsung terdeteksi instan
        # begitu ada 1 pesan masuk lagi, tanpa perlu nunggu watcher thread. Heartbeat
        # AGV ini TIDAK memengaruhi status AGV lain (masing-masing independen).
        _agv_last_seen[agv_prefix] = time.time()
        if agv_slot.get("connection_status") != "CONNECTED":
            agv_slot["connection_status"] = "CONNECTED"
            print(f"[OK] {agv_prefix} kembali mengirim data (connected)")

        if topic == f"{agv_prefix}/monitor/state":
            # plain text, contoh isi: "AGV1_LOADING_AT_STATION_1" / "AGV2_..."
            agv_slot["state"] = raw_text
        else:
            # <prefix>/monitor/normal, livetrack, alarm -> JSON
            try:
                payload = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                print(f"[WARN] Payload AGV tidak valid JSON di topic {topic}: {raw_text}")
                return

            if topic == f"{agv_prefix}/monitor/normal":
                agv_slot["normal"] = payload
            elif topic == f"{agv_prefix}/monitor/livetrack":
                agv_slot["livetrack"] = payload
            elif topic == f"{agv_prefix}/monitor/alarm":
                agv_slot["alarm"] = payload
            # topic <prefix>/monitor/pid sengaja tidak disimpan (data debug PID, belum dipakai dashboard)

        # broadcast langsung ke frontend (tanpa lewat logic OEE/mes/wc di bawah,
        # karena tidak relevan untuk data AGV) lalu selesai di sini.
        if main_loop:
            asyncio.run_coroutine_threadsafe(manager.broadcast(production_state), main_loop)
        return

    # --- Topic lain (mes/#) tetap JSON seperti semula ---
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, ValueError):
        print(f"[WARN] Payload tidak valid JSON di topic {topic}, diabaikan.")
        return

    server_time = datetime.now()

    # inject ke payload
    payload["timestamp"] = server_time.isoformat()

    # FIX BUG KRITIS: sebelumnya "if topic.startswith('mes/wc/')" ikut menangkap
    # topic turunan seperti "mes/wc/conveyor2/warna" (payload cuma {warna, product_id},
    # TANPA field "status"/"workcenter"). Baris "payload['status']" di bawah meledak
    # jadi KeyError untuk topic seperti itu, dan exception ini TIDAK tertangkap --
    # akibatnya proses MQTT macet diam-diam persis di titik itu, dan semua pesan
    # SETELAHNYA (termasuk mes/wc/Delta) tidak pernah diproses/di-broadcast.
    # Fix: hanya masuk blok ini kalau topic PERSIS "mes/wc/<nama>" (3 segmen),
    # bukan turunan yang lebih dalam seperti ".../conveyor2/warna" (4 segmen).
    topic_parts = topic.split("/")
    is_workcenter_status_topic = topic.startswith('mes/wc/') and len(topic_parts) == 3

    if is_workcenter_status_topic:
        
        if payload.get("status") == "start" and payload.get("workcenter") == "Conveyor1":
            if state.start_time is None:
                state.start_time = datetime.now()
                print("[TIMER] REAL Production Start:", state.start_time)
        
        try:
            insert_mqtt_log(topic, payload)
        except Exception as e:
            print("[ERROR] DB Log Error:", e)
        
        wc = payload.get("workcenter")

        # NEW: sekarang ada 2 unit AGV bergantian. dummy_production.py mengirim field
        # tambahan "agv_prefix" ("agv1"/"agv2") di setiap payload mes/wc/AGV supaya
        # backend tahu unit MANA yang lagi dilaporkan. Kalau ada, pecah jadi kartu
        # workcenter terpisah "AGV1"/"AGV2" -- BUKAN 1 kartu "AGV" gabungan seperti
        # sebelumnya (yang bikin status AGV1 & AGV2 saling menimpa/membingungkan).
        # Fallback ke nama asli ("AGV") kalau field belum ada, supaya tetap kompatibel
        # dengan payload lama / topic non-AGV yang tidak punya field ini.
        if wc == "AGV" and payload.get("agv_prefix"):
            wc = payload["agv_prefix"].upper()

        if wc not in production_state["workcenters"]:
            production_state["workcenters"][wc] = {
                "status": "IDLE",
                "cycle": 0,
                "ok": 0,
                "ng": 0,
                "warning_message": None  # NEW: pesan detail kalau status jadi WARNING
            }
        
        if payload.get("status") == "start":
            production_state["workcenters"][wc]["status"] = "RUNNING"
            # NEW: bersihkan warning lama begitu siklus baru mulai lagi
            production_state["workcenters"][wc]["warning_message"] = None

        elif payload.get("status") == "done":
            production_state["workcenters"][wc]["status"] = "IDLE"
            production_state["workcenters"][wc]["warning_message"] = None
            production_state["workcenters"][wc]["cycle"] = payload.get("cycle_time", 0)

            if payload.get("result") == "ok":
                production_state["workcenters"][wc]["ok"] += 1
            elif payload.get("result") == "ng":
                production_state["workcenters"][wc]["ng"] += 1

        # NEW: status "warning" -- dipakai untuk gangguan sistem/koneksi (mis. AGV
        # timeout tidak sampai tujuan), DIBEDAKAN dari status "done" biasa supaya
        # kartu di frontend bisa tampil beda (kuning + pesan) daripada IDLE polos.
        # Hasil produksi (ok/ng) tetap dihitung seperti biasa karena produk memang
        # gagal selesai di siklus ini -- yang beda cuma cara ditampilkannya.
        elif payload.get("status") == "warning":
            production_state["workcenters"][wc]["status"] = "WARNING"
            production_state["workcenters"][wc]["warning_message"] = payload.get("message", "Terjadi gangguan tidak diketahui")
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

                    # NEW: ambil hasil terakhir calculate_oee() (sudah dipanggil di atas
                    # tiap pesan MQTT masuk, jadi production_state["oee"] sudah up-to-date
                    # untuk MO yang baru saja selesai ini)
                    oee_data = production_state.get("oee") or {}
                    oee_rate = oee_data.get("oee")
                    availability_rate = oee_data.get("availability")
                    performance_rate = oee_data.get("performance")
                    quality_rate = oee_data.get("quality")
                    
                    insert_mo_history_record(
                        mo_id=state.current_mo_id,
                        start_time=state.start_time,
                        end_time=end_time_now,
                        status="done",
                        total=total_prod,
                        ok=ok_count,
                        ng=ng_count,
                        yield_rate=round(yield_rate, 2),
                        oee_rate=oee_rate,
                        availability_rate=availability_rate,
                        performance_rate=performance_rate,
                        quality_rate=quality_rate,
                    )
                except Exception as e:
                    print(f"[ERROR] Gagal menyimpan ke mo_history: {e}")
                # -------------------------------------------------
                
    production_state["target"] = state.production_target
    production_state["progress"] = state.produced_count

    if main_loop:
        asyncio.run_coroutine_threadsafe(manager.broadcast(production_state), main_loop)
    
# NEW: kalau koneksi MQTT ke broker putus (bukan cuma satu workcenter yang gangguan,
# tapi backend kehilangan koneksi sepenuhnya), broadcast status global supaya frontend
# tahu SEMUA data yang ditampilkan sedang beku, bukan cuma satu kartu.
def on_disconnect(client, userdata, rc):
    print(f"[WARN] MQTT terputus dari broker (rc={rc}), mencoba reconnect otomatis...")
    production_state["mqtt_connected"] = False
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
    mqtt_client.on_disconnect = on_disconnect  # NEW

    # NEW: nyalakan heartbeat watcher AGV di background thread terpisah
    threading.Thread(target=_agv_heartbeat_watcher, daemon=True).start()
    
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