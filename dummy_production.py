import paho.mqtt.client as mqtt
import json
import time
import random
import requests
from datetime import datetime

# =============================
# KONFIGURASI KONEKSI
# =============================
BROKER = "localhost" # Alamat IP Broker MQTT
PORT = 1883
DELTA_ESP32_URL = "http://deltarobot.local" # IP/Hostname ESP32 Delta Robot

# =============================
# GLOBAL STATE & WORKCENTER CONFIG
# =============================
production_target = 0
produced_count = 0
running = False

# Toggle mode: True = Hubungkan ke Hardware Asli, False = Bypass (Dummy UI)
enable_workcenters = {
    "Conveyor1": True,
    "ArmRobot": False,
    "AGV": True,
    "Conveyor2": True,
    "Delta": True
}

# Status balasan dari Hardware Fisik
real_arm_robot_done = False
real_arm_robot_result = "ok"

# =============================
# MQTT CLIENT SETUP
# =============================
client = mqtt.Client()

def publish(topic, payload):
    client.publish(topic, json.dumps(payload))
    print(f"[SEND] {topic} -> {payload}")

def on_message(client, userdata, msg):
    global production_target, produced_count, running
    global real_arm_robot_done, real_arm_robot_result
    global enable_workcenters

    try:
        payload = json.loads(msg.payload.decode())
    except:
        return
        
    topic = msg.topic

    # Topik Target & Kontrol Utama
    if topic == "mes/target":
        production_target = payload.get("target", 0)
        produced_count = 0
        print(f"🎯 Production Target Set: {production_target}")

    elif topic == "mes/control":
        command = payload.get("command")
        if command == "start":
            running = True
            print("▶ Production START")
        elif command == "stop":
            running = False
            print("⏹ Production STOP")

    elif topic == "mes/config":
        wc = payload.get("workcenter")
        enabled = payload.get("enabled")
        if wc in enable_workcenters and isinstance(enabled, bool):
            enable_workcenters[wc] = enabled
            print(f"⚙️ Status {wc} diubah menjadi: {'NORMAL' if enabled else 'BYPASS'}")

    # Topik Balasan Balik dari Hardware Arm Robot
    elif topic == "mes/hardware/arm_robot/reply":
        print(f"[HARDWARE RECEIVE] Arm Robot Update: {payload}")
        if payload.get("status") == "done":
            real_arm_robot_result = payload.get("result", "ok")
            real_arm_robot_done = True 

client.on_message = on_message
client.connect(BROKER, PORT, 60)
client.subscribe("mes/target")
client.subscribe("mes/control")
client.subscribe("mes/config")
client.subscribe("mes/hardware/arm_robot/reply") 
client.loop_start()

print("MQTT Connected. System Ready...")

# =============================
# WORKCENTER HELPER (DUMMY/BYPASS)
# =============================
def process_workcenter(product_id, workcenter, ideal_cycle=5.0, min_time=4.8, max_time=5.2, reject_rate=0):
    publish(f"mes/wc/{workcenter}", {
        "timestamp": datetime.now().isoformat(),
        "product_id": product_id,
        "workcenter": workcenter,
        "status": "start"
    })

    cycle_time = round(random.uniform(min_time, max_time), 2)
    time.sleep(cycle_time)

    result = "ok"
    if reject_rate > 0:
        result = random.choices(["ok", "ng"], weights=[100 - reject_rate, reject_rate])[0]

    publish(f"mes/wc/{workcenter}", {
        "timestamp": datetime.now().isoformat(),
        "product_id": product_id,
        "workcenter": workcenter,
        "status": "done",
        "result": result,
        "cycle_time": cycle_time,
        "ideal_cycle_time": ideal_cycle
    })
    return result

# =============================
# MAIN PRODUCTION LOOP
# =============================
while True:
    if not running or production_target == 0:
        time.sleep(1)
        continue

    if production_target > 0 and produced_count >= production_target:
        print("🎯 Target Produksi Tercapai!")
        running = False
        production_target = 0
        produced_count = 0
        continue

    product_id = random.randint(1000, 9999)
    print(f"\n=== START PRODUCT {product_id} ===")
    failed_at = None

    # 1. CONVEYOR 1
    if enable_workcenters["Conveyor1"]:
        result = process_workcenter(product_id, "Conveyor1", ideal_cycle=5.0, min_time=4.8, max_time=5.2, reject_rate=0)
    else:
        result = process_workcenter(product_id, "Conveyor1", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
        
    if result == "ng":
        failed_at = "Conveyor1"

    # 2. REAL ARM ROBOT (MQTT INTEGRATION)
    if failed_at is None and running:
        if enable_workcenters["ArmRobot"]:
            print("🤖 Mengirim perintah START ke Real Arm Robot via MQTT...")
            
            # KUNCI 1: Reset flag DULUAN sebelum kirim trigger
            real_arm_robot_done = False
            start_time_arm = time.time()

            # Update status UI Dashboard
            publish("mes/wc/ArmRobot", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "ArmRobot",
                "status": "start"
            })

            # KUNCI 2: Send trigger command ke Hardware Controller Arm Robot
            publish("mes/hardware/arm_robot/cmd", {
                "command": "start",
                "product_id": product_id
            })

            # Tunggu respon dari Arm Robot via MQTT reply
            while not real_arm_robot_done:
                if not running:
                    break
                time.sleep(0.5)

            if running:
                cycle_time_arm = round(time.time() - start_time_arm, 2)
                publish("mes/wc/ArmRobot", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "ArmRobot",
                    "status": "done",
                    "result": real_arm_robot_result,
                    "cycle_time": cycle_time_arm,
                    "ideal_cycle_time": 5.0
                })

                if real_arm_robot_result == "ng":
                    failed_at = "ArmRobot"
        else:
            result = process_workcenter(product_id, "ArmRobot", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
            if result == "ng": 
                failed_at = "ArmRobot"

    # 3. AGV & CONVEYOR 2
    if failed_at is None and running:
        for wc in ["AGV", "Conveyor2"]:
            if enable_workcenters[wc]:
                result = process_workcenter(product_id, wc, ideal_cycle=5.0, min_time=4.8, max_time=5.2, reject_rate=0)
            else:
                result = process_workcenter(product_id, wc, ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
                
            if result == "ng":
                failed_at = wc
                break

   # 4. REAL DELTA ROBOT (HTTP REST INTEGRATION)
    if failed_at is None and running:
        if enable_workcenters["Delta"]:
            print("🕷️ Mengirim perintah ke Real Delta Robot via HTTP...")
            start_time_delta = time.time()
            
            publish("mes/wc/Delta", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "Delta",
                "status": "start"
            })

            real_delta_robot_result = "ok"
            try:
                # Naikkan timeout menjadi 10 detik
                requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "STARTA"}, timeout=10)
                time.sleep(1) 
                
                delta_is_done = False
                while running and not delta_is_done:
                    # Naikkan timeout menjadi 10 detik
                    resp = requests.get(f"{DELTA_ESP32_URL}/getlog", timeout=10)
                    if resp.status_code == 200:
                        try:
                            last_log = resp.json().get("log", "")
                           # Deteksi teks penutup dari fungsi runAutoSequence() atau homing
                            if "Urutan otomatis SELESAI" in last_log or "[HOMING] Selesai" in last_log:
                                delta_is_done = True
                                requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "STATUS"}, timeout=10) 
                        except ValueError:
                            print("⚠️ Gagal membaca JSON dari ESP32 Delta")
                    time.sleep(1)
                    
            except requests.exceptions.RequestException as e:
                print(f"❌ Koneksi ke ESP32 Delta Gagal (Timeout/Offline): {e}")
                real_delta_robot_result = "ng"

            if running:
                cycle_time_delta = round(time.time() - start_time_delta, 2)
                publish("mes/wc/Delta", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "Delta",
                    "status": "done",
                    "result": real_delta_robot_result,
                    "cycle_time": cycle_time_delta,
                    "ideal_cycle_time": 6.0
                })
                
                if real_delta_robot_result == "ng":
                    failed_at = "Delta"

    # ====================================================
    # FINAL PRODUCT EVENT (Laporan ke UI Dashboard)
    # ====================================================
    if running:
        final_result = "ng" if failed_at else "ok"
        payload = {
            "timestamp": datetime.now().isoformat(),
            "product_id": product_id,
            "status": "complete",
            "result": final_result
        }

        # Cantumkan lokasi kegagalan jika ada cacat produksi
        if failed_at:
            payload["terminated_at"] = failed_at

        publish("mes/product", payload)

        if final_result == "ok":
            produced_count += 1
            print(f"✅ === PRODUCT {product_id} COMPLETE (OK) ===")
        else:
            print(f"❌ === PRODUCT {product_id} REJECTED at {failed_at} (NG) ===")

        print(f"Progress Good Units: {produced_count}/{production_target}")
        time.sleep(3)
        