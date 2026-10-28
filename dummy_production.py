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

AGV_PREFIXES = ["agv1", "agv2"]

current_agv_idx = 0

agv_predispatched = {prefix: False for prefix in AGV_PREFIXES}

agv_pending_release = {prefix: False for prefix in AGV_PREFIXES}

AGV_CENTERING_DURATION = 5

ARM_RETURN_HOME_DURATION = 3

AGV_UNLOAD_DURATION = 7

CONVEYOR2_WARNA_TIMEOUT = 5

# FIX: CONVEYOR2_TO_DELTA_TRAVEL_DURATION (jeda tetap 15 detik antara warna
# terbaca dan proximity Delta dibuka) DIHAPUS -- sekarang SET_AUTO ON dikirim
# LANGSUNG begitu warna terdeteksi (lihat blok Conveyor2 di bawah), tanpa jeda.

AGV_TIMEOUT_TO_STATION1 = 30
AGV_TIMEOUT_TO_STATION2 = 90

ARM_ROBOT_TIMEOUT = 5

DELTA_TIMEOUT = 30

# Batas maksimal menunggu balasan Conveyor1 asli untuk 1 siklus penuh.
# Siklus fisiknya ~25-45 detik normal (5s + 2s + tunggu IR1 + 3s + 2s + 1s + 3.5s +
# ultrasonik + tunggu IR2 + 4s). Firmware punya batas sendiri 30s untuk IR1 dan
# 30s untuk IR2, jadi kasus terburuk ~85 detik -- angka ini sengaja LEBIH BESAR
# dari itu supaya laporan timeout dari firmware sempat sampai lebih dulu.
CONVEYOR1_TIMEOUT = 120

def agv_state_name(prefix, suffix):
    return f"{prefix.upper()}_{suffix}"

# =============================
# GLOBAL STATE & WORKCENTER CONFIG
# =============================
production_target = 0
produced_count = 0
running = False

enable_workcenters = {
    "Conveyor1": True,
    "ArmRobot": True,
    "AGV": False,
    "Conveyor2": False,
    "Delta": False
}

real_arm_robot_done = False
real_arm_robot_result = "ok"

conveyor2_warna = None

# Balasan terakhir dari Conveyor1 asli (dict dari topic
# mes/hardware/conveyor1/reply). Dicocokkan dengan product_id supaya balasan
# "basi" dari siklus lama (mis. setelah STOP di tengah siklus) tidak salah
# dianggap hasil produk yang baru.
conveyor1_reply = None

agv_states = {prefix: "UNKNOWN" for prefix in AGV_PREFIXES}

# =============================
# MQTT CLIENT SETUP
# =============================
client = mqtt.Client()


def publish(topic, payload):
    client.publish(topic, json.dumps(payload))
    print(f"[SEND] {topic} -> {payload}")

def trigger_agv_to_station1(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta1", "1")
    print(f"[SEND] {prefix}/control/order_sta1 -> 1 (AGV berangkat ke Station 1)")

def trigger_agv_center_item(prefix):
    client.publish(f"{prefix}/control/order_sta3_5", "1")
    print(f"[SEND] {prefix}/control/order_sta3_5 -> 1 (AGV geser barang ke posisi tengah)")

def trigger_agv_depart_to_station2(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta3", "1")
    print(f"[SEND] {prefix}/control/order_sta3 -> 1 (izin AGV berangkat ke Station 2)")

def trigger_agv_unload_at_station2(prefix):
    client.publish(f"{prefix}/control/order_sta2", "1")
    print(f"[SEND] {prefix}/control/order_sta2 -> 1 (AGV jalankan conveyor kecil, unload barang ke Conveyor2)")

def trigger_conveyor2_stop():
    # FIX BUG KRITIS: SEBELUMNYA pakai client.publish(..., "1") -- payload mentah
    # "1" itu VALID JSON (json.loads("1") = angka 1), TAPI BUKAN dict/objek. Topic
    # ini ("mes/wc/...") ikut kena wildcard subscribe "mes/#" di mqtt_service.py
    # (dashboard), yang mengharapkan payload berupa objek JSON -- begitu dapat "1"
    # (integer), baris "payload['timestamp'] = ..." di sana MELEDAK (TypeError),
    # MEMATIKAN THREAD MQTT dashboard SELURUHNYA. Fix: selalu pakai publish()
    # (helper yang sudah json.dumps dict), BUKAN client.publish() mentah, untuk
    # topic apa pun yang diawali "mes/".
    publish("mes/wc/conveyor2/stop", {"cmd": "stop"})
    print("🛑 Conveyor2 diberhentikan (Delta selesai memproses barang ini).")


def trigger_agv_return_to_station1(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta2_5", "1")
    print(f"[SEND] {prefix}/control/order_sta2_5 -> 1 ({prefix} balik dari Station 2 ke Station 1/Arm)")

def trigger_agv_home(prefix):
    client.publish(f"{prefix}/control/home", "1")
    print(f"[SEND] {prefix}/control/home -> 1 (AGV kembali ke home)")

def send_all_agv_home():
    print("🏠 Target tercapai, memanggil semua AGV kembali ke home...")
    for prefix in AGV_PREFIXES:
        trigger_agv_home(prefix)
        agv_predispatched[prefix] = False
        agv_pending_release[prefix] = False

def wait_for_agv_state(prefix, target_states, timeout):
    start = time.time()
    while time.time() - start < timeout:
        if agv_states.get(prefix) in target_states:
            return True
        if not running:
            return False
        time.sleep(0.3)
    print(f"[WARN] Timeout menunggu status {prefix} mencapai {target_states} (state terakhir: {agv_states.get(prefix)})")
    return False

def release_pending_agv_at_station2(departing_prefix):
    for other in AGV_PREFIXES:
        if other == departing_prefix or not agv_pending_release[other]:
            continue

        agv_pending_release[other] = False

        if produced_count >= production_target:
            print(f"🏠 Target sudah tercapai, {other} (pending di Station 2) dipanggil pulang (home) bersamaan dengan {departing_prefix} berangkat...")
            trigger_agv_home(other)
        else:
            print(f"↩️ Target belum tercapai ({produced_count}/{production_target}), {other} (pending di Station 2) balik ke Station 1/Arm (order_sta2_5) bersamaan dengan {departing_prefix} berangkat ke Station 2...")
            trigger_agv_return_to_station1(other)
            agv_predispatched[other] = True

def on_message(client, userdata, msg):
    global production_target, produced_count, running
    global real_arm_robot_done, real_arm_robot_result
    global enable_workcenters
    global conveyor2_warna
    global conveyor1_reply

    topic = msg.topic

    try:
        payload = json.loads(msg.payload.decode())
    except:
        return

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

    elif topic == "mes/hardware/arm_robot/reply":
        print(f"[HARDWARE RECEIVE] Arm Robot Update: {payload}")
        if payload.get("status") == "done":
            real_arm_robot_result = payload.get("result", "ok")
            real_arm_robot_done = True

    elif topic == "mes/hardware/conveyor1/reply":
        print(f"[HARDWARE RECEIVE] Conveyor1 Update: {payload}")
        if payload.get("status") == "done":
            conveyor1_reply = payload

    elif topic == "mes/wc/conveyor1/status":
        if payload.get("status") == "connected":
            print("✅ Conveyor1 sudah terhubung ke broker MQTT!")
        elif payload.get("status") == "disconnected":
            print("⚠️ Conveyor1 TERPUTUS dari broker MQTT!")

    elif topic == "mes/wc/conveyor2/warna":
        warna = payload.get("warna")
        if warna in ("biru", "hitam"):
            conveyor2_warna = warna
            print(f"[COLOR] Conveyor2 mendeteksi warna: {warna}")

    elif topic == "mes/wc/conveyor2/status":
        if payload.get("status") == "connected":
            print("✅ Conveyor2 sudah terhubung ke broker MQTT!")

    elif topic in (f"{p}/monitor/normal" for p in AGV_PREFIXES):
        prefix = topic.split("/")[0]
        state = payload.get("agv")
        if state:
            if state != agv_states.get(prefix):
                print(f"[AGV STATE] {prefix}: {agv_states.get(prefix)} -> {state}")
            agv_states[prefix] = state

client.on_message = on_message
client.connect(BROKER, PORT, 60)
client.subscribe("mes/target")
client.subscribe("mes/control")
client.subscribe("mes/config")
client.subscribe("mes/hardware/arm_robot/reply")
client.subscribe("mes/hardware/conveyor1/reply")
client.subscribe("mes/wc/conveyor1/status")
client.subscribe("mes/wc/conveyor2/warna")
client.subscribe("mes/wc/conveyor2/status")
for _prefix in AGV_PREFIXES:
    client.subscribe(f"{_prefix}/monitor/normal")
client.loop_start()

print("MQTT Connected. System Ready...")

# Delta default-nya SELALU di MODE MANUAL (proximity Mega diblokir total) --
# sensor proximity-nya HANYA "dibuka" (SET_AUTO ON) sesaat, TEPAT saat
# Conveyor2 selesai untuk barang yang sedang diproses (lihat blok Conveyor2 di
# bawah), dan langsung "dikunci lagi" (SET_AUTO OFF) begitu siklus Delta itu
# selesai. Jadi proximity TIDAK pernah aktif di luar jendela waktu itu --
# mencegah Delta ke-trigger oleh barang lain / noise sensor kapan pun selain
# persis saat barang memang sudah selesai diproses Conveyor2.
if enable_workcenters["Delta"]:
    try:
        requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "SET_AUTO OFF"}, timeout=10)
        print("🕷️ Delta di-set ke MODE MANUAL (sensor proximity terkunci, menunggu jendela aktivasi per-barang).")
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Gagal set Delta ke MODE MANUAL saat startup: {e}")

# =============================
# WORKCENTER HELPER
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

def simulate_conveyor2_warna(product_id):
    global conveyor2_warna
    warna = random.choice(["hitam", "biru"])
    conveyor2_warna = warna
    publish("mes/wc/conveyor2/warna", {
        "timestamp": datetime.now().isoformat(),
        "product_id": product_id,
        "warna": warna
    })
    print(f"[COLOR-SIM] Conveyor2 (dummy) mendeteksi warna: {warna}")
    return warna

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
    # FIX: True setelah AGV benar-benar berangkat dari Station 1 (order_sta3 terkirim).
    # Dipakai di akhir siklus untuk membedakan: produk gagal SEBELUM AGV berangkat
    # (AGV masih diam di Station 1) vs SESUDAH berangkat (AGV sudah di Station 2).
    agv_departed = False

    agv_prefix = AGV_PREFIXES[current_agv_idx % 2]

    if running:
        if enable_workcenters["AGV"]:
            if agv_predispatched[agv_prefix]:
                print(f"🚚 {agv_prefix} sudah di-pre-dispatch dari siklus sebelumnya, tinggal menunggu sampai Station 1...")
                agv_predispatched[agv_prefix] = False
            else:
                print(f"🚚 Mengirim trigger MQTT (order_sta1) ke {agv_prefix} untuk menuju Station 1 (posisi Arm)...")
                publish("mes/wc/AGV", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "AGV",
                    "agv_prefix": agv_prefix,
                    "status": "standby"
                })
                trigger_agv_to_station1(agv_prefix)

            arrived = wait_for_agv_state(agv_prefix, [agv_state_name(agv_prefix, "LOADING_AT_STATION_1")], timeout=AGV_TIMEOUT_TO_STATION1)
            if not arrived:
                print(f"⚠️ {agv_prefix} tidak konfirmasi sampai Station 1 dalam waktu yang ditentukan, lanjut tetap (fallback).")
                publish("mes/wc/AGV", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "AGV",
                    "agv_prefix": agv_prefix,
                    "status": "warning",
                    "result": "ng",
                    "cycle_time": AGV_TIMEOUT_TO_STATION1,
                    "ideal_cycle_time": 5.0,
                    "message": f"Timeout menunggu {agv_prefix} sampai Station 1 (>{AGV_TIMEOUT_TO_STATION1} detik) — AGV mungkin belum bergerak, macet, atau koneksi MQTT terputus"
                })
        else:
            print("🚚 AGV keluar garasi menuju posisi standby (Conveyor 1)... (dummy)")
            publish("mes/wc/AGV", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "AGV",
                "agv_prefix": agv_prefix,
                "status": "standby"
            })
            time.sleep(1.5)

    if not enable_workcenters["Conveyor1"]:
        result = process_workcenter(product_id, "Conveyor1", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
    elif running:
        # Conveyor1 ASLI. Firmware-nya DIAM sampai diperintah dan hanya
        # menjalankan TEPAT 1 siklus per perintah (dispense -> sortir koin ->
        # posisi ambil Arm), lalu membalas hasilnya. Karena loop produksi ini
        # memproses 1 produk sampai tuntas sebelum produk berikutnya dimulai,
        # otomatis "1 barang dulu -> selesai (1/2) -> baru barang ke-2".
        conveyor1_reply = None  # buang balasan basi dari siklus/produk sebelumnya
        publish("mes/wc/Conveyor1", {
            "timestamp": datetime.now().isoformat(),
            "product_id": product_id,
            "workcenter": "Conveyor1",
            "status": "start"
        })
        start_time_conv1 = time.time()

        print(f"🟦 Mengirim perintah START ke Conveyor1 asli (product_id={product_id})...")
        publish("mes/hardware/conveyor1/cmd", {
            "command": "start",
            "product_id": product_id
        })

        conv1_got_reply = False
        while running:
            if conveyor1_reply is not None and conveyor1_reply.get("product_id") == product_id:
                conv1_got_reply = True
                break
            if time.time() - start_time_conv1 > CONVEYOR1_TIMEOUT:
                print(f"⚠️ Conveyor1 tidak membalas dalam {CONVEYOR1_TIMEOUT} detik.")
                break
            time.sleep(0.3)

        result = "ok"
        if running:
            cycle_time_conv1 = round(time.time() - start_time_conv1, 2)

            if conv1_got_reply:
                result = conveyor1_reply.get("result", "ng")
                reason = conveyor1_reply.get("reason", "")

                if reason in ("no_object", "ir1_timeout", "ir2_timeout"):
                    # Gangguan mekanis/pasokan barang (bukan cacat produk asli) --
                    # dilaporkan sebagai "warning" dengan pesan spesifik.
                    conv1_messages = {
                        "no_object": "Conveyor1 tidak mendeteksi benda di sensor ultrasonik (magazin kosong / benda tidak sampai)",
                        "ir1_timeout": "Benda tidak terdeteksi di IR1 (magazin kosong / macet saat dispense)",
                        "ir2_timeout": "Benda tidak sampai IR2 / posisi ambil Arm (macet di conveyor)"
                    }
                    publish("mes/wc/Conveyor1", {
                        "timestamp": datetime.now().isoformat(),
                        "product_id": product_id,
                        "workcenter": "Conveyor1",
                        "status": "warning",
                        "result": "ng",
                        "cycle_time": cycle_time_conv1,
                        "ideal_cycle_time": 30.0,
                        "message": conv1_messages[reason]
                    })
                else:
                    # ok = benda dengan koin lolos; ng (no_coin) = benda tanpa koin
                    # dibuang -- keduanya hasil sortir NORMAL, jadi tetap "done".
                    publish("mes/wc/Conveyor1", {
                        "timestamp": datetime.now().isoformat(),
                        "product_id": product_id,
                        "workcenter": "Conveyor1",
                        "status": "done",
                        "result": result,
                        "cycle_time": cycle_time_conv1,
                        "ideal_cycle_time": 30.0
                    })
            else:
                result = "ng"
                publish("mes/wc/Conveyor1", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "Conveyor1",
                    "status": "warning",
                    "result": "ng",
                    "cycle_time": cycle_time_conv1,
                    "ideal_cycle_time": 30.0,
                    "message": f"Conveyor1 tidak membalas dalam {CONVEYOR1_TIMEOUT} detik — kemungkinan mati, macet, atau koneksi MQTT terputus"
                })
    else:
        result = "ok"  # produksi sudah di-STOP sebelum sempat mulai -- jangan kirim apa pun ke hardware

    if result == "ng":
        failed_at = "Conveyor1"

    if failed_at is None and running:
        if enable_workcenters["ArmRobot"]:
            print("🤖 Mengirim perintah START ke Real Arm Robot via MQTT...")
            real_arm_robot_done = False
            start_time_arm = time.time()

            publish("mes/wc/ArmRobot", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "ArmRobot",
                "status": "start"
            })

            publish("mes/hardware/arm_robot/cmd", {
                "command": "start",
                "product_id": product_id
            })

            while not real_arm_robot_done:
                if not running:
                    break
                if time.time() - start_time_arm > ARM_ROBOT_TIMEOUT:
                    print(f"⚠️ Arm Robot tidak membalas dalam {ARM_ROBOT_TIMEOUT} detik.")
                    break
                time.sleep(0.5)

            if running:
                cycle_time_arm = round(time.time() - start_time_arm, 2)

                if real_arm_robot_done:
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
                    publish("mes/wc/ArmRobot", {
                        "timestamp": datetime.now().isoformat(),
                        "product_id": product_id,
                        "workcenter": "ArmRobot",
                        "status": "warning",
                        "result": "ng",
                        "cycle_time": cycle_time_arm,
                        "ideal_cycle_time": 5.0,
                        "message": f"Arm Robot tidak membalas dalam {ARM_ROBOT_TIMEOUT} detik — kemungkinan mati, macet, atau koneksi MQTT terputus"
                    })
                    failed_at = "ArmRobot"
        else:
            result = process_workcenter(product_id, "ArmRobot", ideal_cycle=5, min_time=5, max_time=5, reject_rate=0)
            if result == "ng":
                failed_at = "ArmRobot"

    if failed_at is None and running:
        if enable_workcenters["AGV"]:
            print(f"🦾 Menunggu Arm Robot kembali ke home ({ARM_RETURN_HOME_DURATION} detik) sebelum AGV mulai centering...")
            time.sleep(ARM_RETURN_HOME_DURATION)

            print(f"📦 Barang sudah dimuat, {agv_prefix} menggeser barang ke posisi tengah (order_sta3_5)...")
            publish("mes/wc/AGV", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "AGV",
                "agv_prefix": agv_prefix,
                "status": "start"
            })
            start_time_agv = time.time()
            trigger_agv_center_item(agv_prefix)
            time.sleep(AGV_CENTERING_DURATION)

            print(f"🚚 Barang sudah center, {agv_prefix} berangkat ke Station 2 (order_sta3)...")
            release_pending_agv_at_station2(agv_prefix)
            trigger_agv_depart_to_station2(agv_prefix)
            agv_departed = True  # FIX: AGV ini sudah meninggalkan Station 1

            next_agv_prefix = AGV_PREFIXES[(current_agv_idx + 1) % 2]
            if not agv_predispatched[next_agv_prefix]:
                print(f"🚚 Mem-pre-dispatch {next_agv_prefix} ke Station 1 untuk order berikutnya (hindari tabrakan)...")
                trigger_agv_to_station1(next_agv_prefix)
                agv_predispatched[next_agv_prefix] = True
            else:
                print(f"🚚 {next_agv_prefix} sudah dalam perjalanan balik ke Station 1 (order_sta2_5 dari siklus sebelumnya), tidak perlu trigger ulang.")

            arrived_st2 = wait_for_agv_state(agv_prefix, [agv_state_name(agv_prefix, "UNLOADING_AT_STATION_2")], timeout=AGV_TIMEOUT_TO_STATION2)
            cycle_time_agv = round(time.time() - start_time_agv, 2)

            agv_result = "ok" if arrived_st2 else "ng"
            agv_status = "done" if arrived_st2 else "warning"
            publish("mes/wc/AGV", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "AGV",
                "agv_prefix": agv_prefix,
                "status": agv_status,
                "result": agv_result,
                "cycle_time": cycle_time_agv,
                "ideal_cycle_time": 5.0,
                **({"message": f"Timeout menunggu {agv_prefix} sampai Station 2 (>{AGV_TIMEOUT_TO_STATION2} detik) — AGV mungkin macet di jalan atau koneksi MQTT terputus"} if not arrived_st2 else {})
            })

            if agv_result == "ng":
                failed_at = "AGV"
        else:
            result = process_workcenter(product_id, "AGV", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
            if result == "ng":
                failed_at = "AGV"

    if failed_at is None and running:
        if enable_workcenters["AGV"]:
            print("📥 Conveyor2 siap menerima, memberi izin AGV unload (order_sta2)...")
            trigger_agv_unload_at_station2(agv_prefix)
            print(f"⏳ Menunggu {agv_prefix} selesai unload ke Conveyor2 ({AGV_UNLOAD_DURATION} detik)...")
            time.sleep(AGV_UNLOAD_DURATION)

        if enable_workcenters["Conveyor2"]:
            publish("mes/wc/Conveyor2", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "Conveyor2",
                "status": "start"
            })
            start_time_conv2 = time.time()

            result = "ok"
            # Tunggu hasil warna dulu (posisi sensor di tengah jalur) -- ini
            # HANYA untuk data histori produk, tidak dipakai untuk memilih
            # STARTA/STARTB (lihat blok Delta di bawah).
            print(f"🎨 Menunggu hasil deteksi warna asli dari Conveyor2 (maks {CONVEYOR2_WARNA_TIMEOUT} detik)...")
            start_time_warna = time.time()
            while conveyor2_warna is None and running:
                if time.time() - start_time_warna > CONVEYOR2_WARNA_TIMEOUT:
                    print(f"⚠️ Timeout menunggu warna dari Conveyor2 -- lanjut tanpa warna.")
                    break
                time.sleep(0.2)

            # FIX: SET_AUTO ON sekarang dikirim TEPAT SAAT warna terdeteksi (atau
            # begitu timeout warna terlewati), TANPA jeda tempuh lagi -- sebelumnya
            # ada CONVEYOR2_TO_DELTA_TRAVEL_DURATION (15 detik) di antara warna
            # terbaca dan sensor proximity Delta dibuka. Sekarang keduanya jadi
            # satu momen: begitu barang kena sensor warna Conveyor2, proximity
            # Delta ikut aktif (mode AUTO) saat itu juga.
            if running and enable_workcenters["Delta"]:
                try:
                    requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "SET_AUTO ON"}, timeout=10)
                    print("🔓 Sensor proximity Delta DIBUKA (SET_AUTO ON) -- menunggu Delta terpicu barang ini...")
                except requests.exceptions.RequestException as e:
                    print(f"⚠️ Gagal membuka proximity Delta (SET_AUTO ON): {e}")

            if running:
                cycle_time_conv2 = round(time.time() - start_time_conv2, 2)
                publish("mes/wc/Conveyor2", {
                    "timestamp": datetime.now().isoformat(),
                    "product_id": product_id,
                    "workcenter": "Conveyor2",
                    "status": "done",
                    "result": "ok",
                    "cycle_time": cycle_time_conv2,
                    "ideal_cycle_time": 5.0
                })
        else:
            result = process_workcenter(product_id, "Conveyor2", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
            if failed_at is None and running:
                simulate_conveyor2_warna(product_id)

        if result == "ng":
            failed_at = "Conveyor2"

    if failed_at is None and running:
        if enable_workcenters["Delta"]:
            # Proximity Delta SUDAH dibuka (SET_AUTO ON) tepat saat Conveyor2
            # selesai (blok di atas). Delta mulai bergerak SENDIRI begitu barang
            # lewat di depan salah satu sensor proximity-nya, dan MEMILIH SENDIRI
            # START_A/START_B tergantung sensor mana yang kena. Di sini backend
            # TINGGAL MENUNGGU laporan selesainya lewat /getlog.
            print("🕷️ Menunggu Delta terpicu sensor proximity & menyelesaikan siklus otomatisnya...")
            start_time_delta = time.time()

            publish("mes/wc/Delta", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "Delta",
                "status": "start"
            })

            real_delta_robot_result = "ok"
            delta_connection_error = None
            try:
                delta_is_done = False
                delta_wait_start = time.time()
                while running and not delta_is_done:
                    if time.time() - delta_wait_start > DELTA_TIMEOUT:
                        delta_connection_error = f"Delta tidak melaporkan selesai dalam {DELTA_TIMEOUT} detik (proximity mungkin belum terpicu, macet, atau getlog tidak update)"
                        print(f"⚠️ {delta_connection_error}")
                        break

                    resp = requests.get(f"{DELTA_ESP32_URL}/getlog", timeout=10)
                    if resp.status_code == 200:
                        try:
                            last_log = resp.json().get("log", "")
                            if "Urutan otomatis SELESAI" in last_log or "[HOMING] Selesai" in last_log:
                                delta_is_done = True
                                requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "STATUS"}, timeout=10)
                        except ValueError:
                            print("⚠️ Gagal membaca JSON dari ESP32 Delta")
                    time.sleep(1)

            except requests.exceptions.RequestException as e:
                delta_connection_error = f"Koneksi ke Delta ESP32 gagal: {e}"
                print(f"❌ {delta_connection_error}")
                real_delta_robot_result = "ng"

            # Apa pun hasilnya (selesai normal ATAU timeout/gagal), KUNCI LAGI
            # proximity Delta (SET_AUTO OFF) supaya tidak tetap terbuka menunggu
            # barang lain di luar jendela waktu siklus ini.
            if enable_workcenters["Delta"]:
                try:
                    requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": "SET_AUTO OFF"}, timeout=10)
                    print("🔒 Sensor proximity Delta DIKUNCI lagi (SET_AUTO OFF).")
                except requests.exceptions.RequestException as e:
                    print(f"⚠️ Gagal mengunci kembali proximity Delta (SET_AUTO OFF): {e}")

            if running:
                cycle_time_delta = round(time.time() - start_time_delta, 2)

                if delta_connection_error:
                    publish("mes/wc/Delta", {
                        "timestamp": datetime.now().isoformat(),
                        "product_id": product_id,
                        "workcenter": "Delta",
                        "status": "warning",
                        "result": "ng",
                        "cycle_time": cycle_time_delta,
                        "ideal_cycle_time": 6.0,
                        "message": delta_connection_error
                    })
                    failed_at = "Delta"
                else:
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

        else:
            result = process_workcenter(product_id, "Delta", ideal_cycle=5, min_time=5, max_time=5, reject_rate=0)
            if result == "ng":
                failed_at = "Delta"

    if running:
        if enable_workcenters["Conveyor2"]:
            trigger_conveyor2_stop()

        final_result = "ng" if failed_at else "ok"
        payload = {
            "timestamp": datetime.now().isoformat(),
            "product_id": product_id,
            "status": "complete",
            "result": final_result,
            "warna": conveyor2_warna
        }

        if failed_at:
            payload["terminated_at"] = failed_at

        publish("mes/product", payload)

        conveyor2_warna = None

        if final_result == "ok":
            produced_count += 1
            print(f"✅ === PRODUCT {product_id} COMPLETE (OK) ===")
        else:
            print(f"❌ === PRODUCT {product_id} REJECTED at {failed_at} (NG) ===")

        print(f"Progress Good Units: {produced_count}/{production_target}")

        if enable_workcenters["AGV"]:
            if produced_count >= production_target:
                send_all_agv_home()
            elif not agv_departed:
                # FIX: produk gagal SEBELUM AGV berangkat (mis. Conveyor1 NG tanpa
                # koin). AGV masih diam di Station 1 -- JANGAN ditandai pending di
                # Station 2 dan JANGAN ganti giliran. AGV yang sama dipakai lagi
                # untuk produk berikutnya (flag predispatched membuat Step0 tidak
                # mengirim order_sta1 lagi, cukup konfirmasi AGV memang sudah di
                # Station 1). Tanpa ini, AGV lain dikirim ke Station 1 yang masih
                # ditempati AGV ini dan keduanya bertabrakan.
                print(f"⏸️ {agv_prefix} belum berangkat (produk gagal di {failed_at}), tetap menunggu di Station 1 untuk produk berikutnya.")
                agv_predispatched[agv_prefix] = True
            else:
                print(f"⏸️ Target belum tercapai ({produced_count}/{production_target}), {agv_prefix} ditandai pending di Station 2 -- akan diberi tahu arah (order_sta2_5) begitu AGV lain mulai berangkat ke Station 2.")
                agv_pending_release[agv_prefix] = True

        # FIX: giliran hanya pindah ke AGV lain kalau AGV tadi memang sudah berangkat
        if agv_departed or not enable_workcenters["AGV"]:
            current_agv_idx = (current_agv_idx + 1) % 2

        time.sleep(3)