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

# NEW: trigger AGV lewat MQTT, mengikuti firmware final (INTEGRASI_PLANT) yang
# butuh beberapa tahap handshake terpisah, BUKAN 1 trigger tunggal:
#   1) <prefix>/control/order_sta1  -> AGV berangkat dari HOME ke Station 1 (posisi Arm)
#   2) <prefix>/control/order_sta3_5 -> setelah Arm selesai menaruh barang, AGV
#      menggeser barang ke posisi TENGAH pakai conveyor kecil di badannya.
#      TIDAK ADA state eksternal terpisah untuk fase ini (AGV tetap terbaca
#      "..._LOADING_AT_STATION_1" sepanjang proses) -- firmware jalankan motor
#      selama durasi tetap (Ticker 100ms x 45 tick = ~4.5 detik), makanya
#      backend cuma bisa kasih delay tetap, tidak bisa nunggu konfirmasi.
#   3) <prefix>/control/order_sta3  -> izin AGV BENAR-BENAR berangkat ke Station 2,
#      dikirim SETELAH proses centering (order_sta3_5) selesai
#   4) <prefix>/control/order_sta2  -> NEW (semantik berubah): izin AGV menjalankan
#      conveyor kecil di badannya untuk MEMINDAHKAN barang ke Conveyor2. Setelah
#      perintah ini, AGV TIDAK otomatis jalan ke mana-mana lagi -- dia diam/stay
#      persis di Station 2 sampai dapat SALAH SATU dari 2 perintah lanjutan di
#      bawah (order_sta2_5 atau home), yang baru dikirim SETELAH tahu status akhir
#      1 cycle produksi (hasil Delta + target di dashboard tercapai atau belum).
#   5) <prefix>/control/order_sta2_5 -> NEW: AGV balik dari Station 2 LANGSUNG ke
#      Station 1/Arm (BUKAN dari home) -- dikirim kalau target produksi BELUM
#      tercapai, supaya AGV ini siap dipakai lagi di gilirannya berikutnya.
#   6) <prefix>/control/home        -> panggil AGV kembali ke posisi home awal,
#      dikirim kalau target produksi SUDAH tercapai (ke SEMUA AGV sekaligus)
# Payload isi bebas (firmware set STA_x_state=1 begitu topic diterima, tanpa
# mengecek isi pesan) -- kita tetap kirim "1" untuk konsistensi & mudah dibaca log.

# NEW: sekarang ada 2 unit AGV yang bekerja BERGANTIAN (round-robin) untuk
# menghindari tabrakan di jalur yang sama. Urutannya:
#   - AGV di AGV_PREFIXES[0] mengerjakan order 1, 3, 5, ...
#   - AGV di AGV_PREFIXES[1] mengerjakan order 2, 4, 6, ...
# Begitu AGV yang sedang bertugas diberi izin BERANGKAT ke Station 2 (order_sta3),
# AGV giliran berikutnya LANGSUNG dikirim ke Station 1 (order_sta1) untuk order
# selanjutnya -- supaya dia tidak menunggu AGV pertama benar-benar sampai dulu.
# Ini TIDAK butuh threading: karena AGV berikutnya butuh waktu fisik (~9 detik)
# untuk sampai Station 1, dan sisa proses order saat ini (unload, Conveyor2, Delta)
# juga makan waktu, saat program sampai ke iterasi berikutnya AGV itu biasanya
# sudah sampai atau hampir sampai -- makanya cukup dicek statusnya saja, tidak
# perlu trigger ulang.
AGV_PREFIXES = ["agv1", "agv2"]

# NEW: index AGV yang sedang bertugas untuk produk yang SEDANG diproses saat ini
current_agv_idx = 0

# NEW: flag per-AGV, True kalau AGV itu SUDAH di-pre-dispatch (order_sta1 sudah
# dikirim oleh siklus SEBELUMNYA) untuk order yang akan datang -- supaya siklus
# berikutnya tidak mengirim order_sta1 dua kali untuk AGV yang sama.
agv_predispatched = {prefix: False for prefix in AGV_PREFIXES}

# NEW (REVISI): sebelumnya dipakai lock "station12_track_owner" yang BLOK sampai
# konfirmasi kedatangan AGV lain -- ternyata bikin sistem macet total kalau AGV itu
# tidak segera bereaksi ke order_sta2_5 (nunggu tanpa batas waktu). Sekarang diganti
# pendekatan lain: AGV yang di Station2 (baru selesai order_sta2, siklus produknya
# sendiri sudah selesai TAPI target produksi belum tercapai) TIDAK langsung disuruh
# berangkat balik/home di titik itu -- dia ditandai "pending" dulu di sini, dan baru
# betul-betul disuruh berangkat (balik ke Station1 via order_sta2_5, ATAU pulang kalau
# ternyata target sudah tercapai) TEPAT pada saat AGV LAIN (yang di Station1) selesai
# proses centering (order_sta3_5) untuk produk berikutnya -- di momen itu jugalah AGV
# yang di Station1 langsung berangkat (order_sta3). Jadi kedua AGV mulai bergerak di
# momen yang SAMA (bukan salah satu nunggu konfirmasi dulu), dipicu oleh event
# "centering selesai", BUKAN oleh "target tercapai atau tidak" -- status target cuma
# dipakai untuk MEMUTUSKAN ARAH (pulang home vs balik ke Station1), bukan KAPAN dia
# boleh berangkat.
agv_pending_release = {prefix: False for prefix in AGV_PREFIXES}

# NEW: durasi tetap (detik) menunggu proses centering barang (order_sta3_5) selesai.
# Berdasarkan firmware: Ticker timer5 interval 100ms, jalan sampai TimerState5 > 45,
# jadi ~45 x 100ms = 4.5 detik. Dikasih margin jadi 5 detik.
AGV_CENTERING_DURATION = 5

# NEW: durasi tetap (detik) menunggu Arm Robot BENAR-BENAR kembali ke posisi home
# setelah menaruh barang, SEBELUM order_sta3_5 (centering AGV) dikirim. Tidak ada
# state/reply MQTT terpisah untuk "Arm sudah sampai home" (mes/hardware/arm_robot/reply
# cuma menandai barang sudah ditaruh) -- jadi ini delay tetap sebagai margin aman,
# supaya AGV tidak mulai bergeser sementara lengan Arm masih ada di area kerja
# (mencegah tabrakan Arm<->AGV). Sesuaikan kalau waktu tempuh Arm ke home berbeda.
ARM_RETURN_HOME_DURATION = 5

# NEW: durasi tetap (detik) menunggu AGV BENAR-BENAR selesai menjalankan conveyor
# kecilnya (order_sta2) memindahkan barang ke Conveyor2, SEBELUM Conveyor2 sendiri
# mulai berjalan. Tidak ada state/reply MQTT terpisah untuk "AGV sudah selesai
# unload" -- jadi ini delay tetap sebagai margin aman, supaya Conveyor2 tidak mulai
# menarik barang sebelum barang itu benar-benar sampai di atasnya.
AGV_UNLOAD_DURATION = 7

# NEW: timeout (detik) menunggu konfirmasi status AGV lewat MQTT sebelum dianggap gagal/timeout.
# DIPISAH per tahap karena waktu tempuh fisiknya beda jauh (hasil observasi log real):
#   - ke Station 1 (dari home): cepat, ~9 detik di uji coba
#   - ke Station 2 (dari Station 1, bawa barang): jauh lebih lambat, >30 detik di uji coba
# Kalau nanti hasil tes lapangan beda, tinggal sesuaikan angka di sini.
AGV_TIMEOUT_TO_STATION1 = 30
AGV_TIMEOUT_TO_STATION2 = 90
# NEW: AGV_TIMEOUT_TO_STATION1 di atas sekarang dipakai untuk MENUNGGU KEDUA rute
# menuju Station 1 -- dari HOME (order_sta1) MAUPUN dari Station 2 (order_sta2_5) --
# karena keduanya berakhir di state final yang sama (..._LOADING_AT_STATION_1).
# Kalau di lapangan ternyata waktu tempuh 2 rute ini beda jauh, pisahkan jadi 2
# konstanta timeout sendiri-sendiri di sini.

# NEW: timeout dasar untuk Arm Robot menunggu balasan hardware asli via MQTT
# (topic mes/hardware/arm_robot/reply). Sebelumnya loop tunggu ini tidak punya
# batas waktu sama sekali -- angka 30 detik ini bisa disesuaikan nanti setelah
# ada data waktu tempuh real Arm Robot, sama seperti AGV_TIMEOUT_TO_STATION1/2.
ARM_ROBOT_TIMEOUT = 30

# NEW: timeout dasar untuk Delta menunggu laporan "selesai" via HTTP polling /getlog.
# Sama alasannya dengan ARM_ROBOT_TIMEOUT -- loop ini sebelumnya juga tanpa batas waktu.
DELTA_TIMEOUT = 30

# NEW: helper nama state asli firmware, ber-prefix sesuai AGV yang dimaksud
# (mis. agv_state_name("agv1","LOADING_AT_STATION_1") -> "AGV1_LOADING_AT_STATION_1")
def agv_state_name(prefix, suffix):
    return f"{prefix.upper()}_{suffix}"

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
    "Delta": False
}

# Status balasan dari Hardware Fisik
real_arm_robot_done = False
real_arm_robot_result = "ok"

# NEW: menyimpan hasil deteksi warna terakhir dari Conveyor2 (real hardware ATAU dummy),
# dipakai untuk menentukan Delta harus STARTA (hitam) atau STARTB (putih)
conveyor2_warna = None

# NEW: menyimpan state asli terakhir dari MASING-MASING AGV, key = prefix ("agv1"/"agv2").
# Nilai ber-prefix sesuai unitnya, contoh: "AGV1_HOME_IDLE", "AGV2_LOADING_AT_STATION_1", dst.
agv_states = {prefix: "UNKNOWN" for prefix in AGV_PREFIXES}

# =============================
# MQTT CLIENT SETUP
# =============================
client = mqtt.Client()


def publish(topic, payload):
    client.publish(topic, json.dumps(payload))
    print(f"[SEND] {topic} -> {payload}")

# NEW: semua fungsi trigger sekarang menerima parameter "prefix" (agv1/agv2) supaya
# bisa memerintah AGV yang mana saja, bukan cuma 1 unit tetap seperti sebelumnya.
# Semua publish RAW (bukan lewat helper publish() yang membungkus payload dengan
# json.dumps() -- itu akan menambah tanda kutip yang tidak perlu, meski firmware
# final ini sebenarnya tidak baca isi payload sama sekali untuk topic order_sta*/home).
#
# PENTING (fix bug stale-state): trigger yang menunggu state RESET agv_states[prefix]
# ke None SEBELUM publish. Tanpa ini, kalau agv_states[prefix] kebetulan MASIH
# menyimpan nilai dari siklus sebelumnya yang sama dengan target yang mau ditunggu,
# wait_for_agv_state() akan langsung return True instan padahal AGV belum benar-benar
# bergerak untuk permintaan yang baru ini.
def trigger_agv_to_station1(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta1", "1")
    print(f"[SEND] {prefix}/control/order_sta1 -> 1 (AGV berangkat ke Station 1)")

# NEW: trigger centering barang. Tidak reset state karena tidak ada state
# eksternal terpisah untuk fase ini -- AGV tetap di state LOADING_AT_STATION_1
# yang sama sepanjang proses, jadi tidak ada apa pun untuk "ditunggu" via MQTT di sini.
def trigger_agv_center_item(prefix):
    client.publish(f"{prefix}/control/order_sta3_5", "1")
    print(f"[SEND] {prefix}/control/order_sta3_5 -> 1 (AGV geser barang ke posisi tengah)")

def trigger_agv_depart_to_station2(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta3", "1")
    print(f"[SEND] {prefix}/control/order_sta3 -> 1 (izin AGV berangkat ke Station 2)")

def trigger_agv_unload_at_station2(prefix):
    # NEW: sekarang perintah ini HANYA menjalankan conveyor kecil di badan AGV untuk
    # memindahkan barang ke Conveyor2 -- BUKAN "izin AGV pergi/lanjut" seperti dulu.
    # Setelah ini AGV diam/stay di Station 2; ke mana dia lanjut (home atau balik ke
    # Station 1/Arm) baru diputuskan belakangan lewat trigger_agv_home() atau
    # trigger_agv_return_to_station1(), SETELAH tahu status akhir 1 cycle produksi.
    client.publish(f"{prefix}/control/order_sta2", "1")
    print(f"[SEND] {prefix}/control/order_sta2 -> 1 (AGV jalankan conveyor kecil, unload barang ke Conveyor2)")

# NEW: AGV balik dari Station 2 LANGSUNG ke Station 1/Arm (bukan dari home) --
# dipakai saat 1 cycle produksi selesai TAPI target produksi belum tercapai,
# supaya AGV ini siap dipakai lagi di gilirannya berikutnya tanpa perlu mampir home.
# Reset state ke None dulu (fix bug stale-state, sama seperti trigger_agv_to_station1)
# supaya wait_for_agv_state() nanti benar-benar menunggu kedatangan yang baru,
# bukan langsung return True karena kebetulan masih menyimpan state lama yang sama.
def trigger_agv_return_to_station1(prefix):
    agv_states[prefix] = None
    client.publish(f"{prefix}/control/order_sta2_5", "1")
    print(f"[SEND] {prefix}/control/order_sta2_5 -> 1 ({prefix} balik dari Station 2 ke Station 1/Arm)")

# NEW: panggil 1 AGV pulang ke posisi home awal
def trigger_agv_home(prefix):
    client.publish(f"{prefix}/control/home", "1")
    print(f"[SEND] {prefix}/control/home -> 1 (AGV kembali ke home)")

# NEW: panggil SEMUA AGV pulang sekaligus -- dipakai begitu target produksi tercapai
def send_all_agv_home():
    print("🏠 Target tercapai, memanggil semua AGV kembali ke home...")
    for prefix in AGV_PREFIXES:
        trigger_agv_home(prefix)
        agv_predispatched[prefix] = False  # reset flag, tidak ada lagi order menunggu
        agv_pending_release[prefix] = False  # reset flag, MO baru nanti mulai dari kondisi bersih

# NEW: tunggu sampai agv_states[prefix] mencapai salah satu target_states, dengan timeout.
# Return True kalau tercapai, False kalau timeout.
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

# NEW (REVISI): dipanggil TEPAT saat AGV `departing_prefix` baru selesai centering
# (order_sta3_5) dan SIAP berangkat ke Station 2 (order_sta3). Di momen INI JUGA kita
# cek: apakah ada AGV LAIN yang sedang "pending" (sudah selesai siklus produknya
# sendiri di Station 2, tapi belum diputuskan mau kemana)? Kalau ada, putuskan
# SEKARANG (baca status target produksi) dan langsung suruh dia berangkat --
# BERSAMAAN dengan `departing_prefix` yang juga langsung berangkat ke Station 2.
# Jadi tidak ada yang saling menunggu konfirmasi kedatangan siapa pun.
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
    global conveyor2_warna  # NEW

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

    # NEW: dengarkan hasil deteksi warna dari Conveyor2 (dipublish oleh firmware
    # conveyor2.ino via TOPIC_WARNA saat sudah pakai hardware asli).
    # Selama Conveyor2 masih dummy, nilai ini akan kita isi manual di process_workcenter().
    elif topic == "mes/wc/conveyor2/warna":
        warna = payload.get("warna")
        if warna in ("putih", "hitam"):
            conveyor2_warna = warna
            print(f"[COLOR] Conveyor2 mendeteksi warna: {warna}")

    # NEW: dengarkan status KEDUA AGV asli sekaligus, dipublish tiap ~1.5 detik oleh
    # firmware masing-masing (lihat JSONdata2() -> monitoring["agv"] -> mqttClient.publish).
    # Topic-nya "agv1/monitor/normal" ATAU "agv2/monitor/normal" -- ambil prefix dari topic
    # itu sendiri supaya 1 handler ini otomatis cover kedua unit tanpa duplikasi kode.
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
client.subscribe("mes/wc/conveyor2/warna")  # NEW: subscribe topic warna dari Conveyor2
for _prefix in AGV_PREFIXES:
    client.subscribe(f"{_prefix}/monitor/normal")  # NEW: subscribe status KEDUA AGV asli
client.loop_start()

print("MQTT Connected. System Ready...")

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

# NEW: simulasi deteksi warna di ujung Conveyor2, dipakai selama Conveyor2 masih dummy.
# Payload & topic sengaja disamakan persis dengan firmware conveyor2.ino
# (TOPIC_WARNA = "mes/wc/conveyor2/warna", value "putih"/"hitam") supaya begitu
# Conveyor2 asli diintegrasikan, logic pemilihan Delta di bawah tidak perlu diubah lagi.
def simulate_conveyor2_warna(product_id):
    global conveyor2_warna
    warna = random.choice(["hitam", "putih"])
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

    # NEW: tentukan AGV yang bertugas untuk produk INI, sesuai giliran round-robin
    agv_prefix = AGV_PREFIXES[current_agv_idx % 2]

    # 0. PRE-DISPATCH AGV (STANDBY DI STATION 1 / POSISI ARM)
    # NEW: kalau AGV asli aktif, kirim trigger order_sta1 dan TUNGGU konfirmasi AGV benar-benar
    # sudah sampai (state == ..._LOADING_AT_STATION_1) SEBELUM lanjut ke Arm Robot.
    # Ini memastikan AGV sudah siap menunggu sebelum Arm menjatuhkan benda,
    # bukan baru bergerak setelah Arm selesai.
    # NEW: KALAU AGV ini sudah di-pre-dispatch oleh siklus SEBELUMNYA (giliran AGV lain
    # yang baru saja berangkat ke Station 2), TIDAK perlu kirim order_sta1 lagi --
    # cukup langsung tunggu statusnya, karena dia mungkin sudah dalam perjalanan/sampai.
    if running:
        # NEW: mulai dari sini, SETIAP payload publish("mes/wc/AGV", {...}) di siklus ini
        # disisipi field "agv_prefix": agv_prefix -- supaya backend (mqtt_service.py) tahu
        # unit AGV MANA yang lagi dilaporkan, dan bisa memisahkannya jadi kartu "AGV1"/"AGV2"
        # di dashboard alih-alih 1 kartu "AGV" gabungan yang isinya saling menimpa antara
        # kedua unit (yang sebelumnya bikin status AGV1 & AGV2 tidak bisa dibedakan).
        if enable_workcenters["AGV"]:
            if agv_predispatched[agv_prefix]:
                print(f"🚚 {agv_prefix} sudah di-pre-dispatch dari siklus sebelumnya, tinggal menunggu sampai Station 1...")
                agv_predispatched[agv_prefix] = False  # sudah dipakai, reset flag
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
                # NEW: publish status "warning" (bukan diam saja) supaya kartu AGV di
                # dashboard menunjukkan ada gangguan spesifik, bukan cuma diam di status lama.
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

    # 1. CONVEYOR 1
    if enable_workcenters["Conveyor1"]:
        result = process_workcenter(product_id, "Conveyor1", ideal_cycle=5.0, min_time=4.8, max_time=5.2, reject_rate=0)
    else:
        result = process_workcenter(product_id, "Conveyor1", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)
        
    if result == "ng":
        failed_at = "Conveyor1"

    # 2. REAL ARM ROBOT
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

            # Timeout dasar -- kalau Arm Robot tidak pernah membalas (mati/nyangkut/
            # kabel putus), program tidak akan beku selamanya di sini.
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
                    # Balasan diterima seperti biasa -- laporkan hasil asli (ok/ng kualitas)
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
                    # Timeout tercapai, tidak ada balasan sama sekali dari Arm Robot --
                    # ini gangguan KOMUNIKASI/HARDWARE, bukan cacat produk asli, jadi
                    # dilaporkan sebagai "warning" dengan pesan spesifik, bukan NG polos.
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

    # 3. AGV (Memuat & Membawa Barang ke Station 2 / Conveyor 2)
    if failed_at is None and running:
        if enable_workcenters["AGV"]:
            # NEW: Arm Robot baru saja selesai menaruh barang (baik hasil real hardware
            # maupun dummy di atas) -- tapi lengannya BELUM TENTU sudah kembali ke posisi
            # home fisiknya. Tunggu dulu ARM_RETURN_HOME_DURATION detik di sini SEBELUM
            # order_sta3_5 (centering AGV) dikirim, supaya AGV benar-benar baru mulai
            # bergeser SETELAH Arm keluar dari area kerja -- mencegah tabrakan Arm<->AGV.
            print(f"🦾 Menunggu Arm Robot kembali ke home ({ARM_RETURN_HOME_DURATION} detik) sebelum AGV mulai centering...")
            time.sleep(ARM_RETURN_HOME_DURATION)

            # NEW: AGV asli mendeteksi barang masuk sendiri lewat sensor jarak di badannya
            # (lihat AGV_Loading() di firmware). Arm robot sudah selesai menjatuhkan barang
            # di titik ini. Sekarang firmware butuh 2 langkah berurutan sebelum benar-benar
            # berangkat:
            #   a) order_sta3_5 -> geser barang ke posisi tengah (delay tetap, tidak ada
            #      state untuk dikonfirmasi -- lihat komentar AGV_CENTERING_DURATION di atas)
            #   b) order_sta3 -> baru izin berangkat sungguhan ke Station 2
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
            # NEW (REVISI): centering ({agv_prefix}) baru saja selesai -- momen INI JUGA
            # dipakai untuk melepas AGV LAIN yang mungkin sedang "pending" di Station 2
            # (sudah selesai siklus produknya sendiri, menunggu keputusan). Keduanya
            # jadi berangkat BERSAMAAN: {agv_prefix} maju ke Station 2, AGV lain (kalau
            # ada yang pending) balik ke Station 1 atau pulang -- BUKAN saling menunggu
            # konfirmasi kedatangan satu sama lain.
            release_pending_agv_at_station2(agv_prefix)
            trigger_agv_depart_to_station2(agv_prefix)

            # NEW: begitu AGV ini berangkat, pre-dispatch AGV GILIRAN BERIKUTNYA ke
            # Station 1 untuk order selanjutnya -- supaya tidak saling menunggu atau
            # tabrakan di jalur yang sama. Kirim tanpa menunggu (fire-and-forget); nanti
            # dicek statusnya di iterasi produk berikutnya. TAPI kirim order_sta1 HANYA
            # kalau AGV berikutnya itu belum di-predispatch -- kalau dia TADI (baris di
            # atas, lewat release_pending_agv_at_station2()) baru saja dikirim
            # order_sta2_5 karena sedang pending di Station 2, flag predispatched-nya
            # SUDAH True duluan di sini -- jangan dikirim order_sta1 lagi (itu perintah
            # berangkat dari HOME, bukan dari Station 2, AGV secara fisik tidak di home).
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
            # NEW: status "warning" (bukan "done") kalau gagal karena timeout,
            # supaya kartu AGV tampil kuning + pesan, bukan diam kembali ke IDLE
            # seolah semua baik-baik saja.
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

    # 4. CONVEYOR 2
    if failed_at is None and running:
        # NEW: kalau AGV asli aktif, kasih izin unload SEBELUM Conveyor2 mulai memproses --
        # AGV yang bertugas sudah standby di Station 2 dari step sebelumnya, tapi TIDAK
        # akan menurunkan barang sampai dapat sinyal order_sta2 (konfirmasi Conveyor2
        # "siap menerima"). Tanpa ini, conveyor kecil di badan AGV tetap idle.
        if enable_workcenters["AGV"]:
            print("📥 Conveyor2 siap menerima, memberi izin AGV unload (order_sta2)...")
            trigger_agv_unload_at_station2(agv_prefix)
            # NEW: order_sta2 cuma MEMICU conveyor kecil AGV, tidak ada state/reply MQTT
            # terpisah untuk "AGV sudah selesai unload". Tunggu dulu AGV_UNLOAD_DURATION
            # detik di sini SEBELUM Conveyor2 mulai berjalan, supaya Conveyor2 tidak
            # menarik barang sebelum barang itu benar-benar sampai di atasnya.
            print(f"⏳ Menunggu {agv_prefix} selesai unload ke Conveyor2 ({AGV_UNLOAD_DURATION} detik)...")
            time.sleep(AGV_UNLOAD_DURATION)

        if enable_workcenters["Conveyor2"]:
            result = process_workcenter(product_id, "Conveyor2", ideal_cycle=5.0, min_time=4.8, max_time=5.2, reject_rate=0)
        else:
            result = process_workcenter(product_id, "Conveyor2", ideal_cycle=0.5, min_time=0.5, max_time=0.5, reject_rate=0)

        if result == "ng":
            failed_at = "Conveyor2"

        # NEW: setelah Conveyor2 selesai (produk sampai di ujung, siap dipilah),
        # simulasikan sensor warna Conveyor2 untuk menentukan STARTA/STARTB Delta.
        # Hanya dilakukan kalau Conveyor2 belum gagal (NG) dan produksi masih berjalan.
        if failed_at is None and running:
            simulate_conveyor2_warna(product_id)

    # 5. REAL DELTA ROBOT
    if failed_at is None and running:
        if enable_workcenters["Delta"]:
            # NEW: tentukan command Delta berdasarkan warna dari Conveyor2.
            # hitam -> STARTA, putih -> STARTB. Default STARTB kalau warna belum terbaca
            # (safety fallback, seharusnya tidak terjadi karena selalu di-set di step Conveyor2).
            delta_command = "STARTA" if conveyor2_warna == "hitam" else "STARTB"
            print(f"🕷️ Mengirim perintah {delta_command} ke Real Delta Robot via HTTP (warna: {conveyor2_warna})...")
            start_time_delta = time.time()
            
            publish("mes/wc/Delta", {
                "timestamp": datetime.now().isoformat(),
                "product_id": product_id,
                "workcenter": "Delta",
                "status": "start"
            })

            real_delta_robot_result = "ok"
            delta_connection_error = None  # simpan pesan spesifik kalau ada gangguan koneksi/timeout
            try:
                requests.get(f"{DELTA_ESP32_URL}/cmd", params={"val": delta_command}, timeout=10)
                time.sleep(1) 
                
                delta_is_done = False
                delta_wait_start = time.time()
                while running and not delta_is_done:
                    # Timeout -- loop ini sebelumnya juga tidak punya batas waktu,
                    # risiko hang selamanya sama persis dengan Arm Robot di atas
                    # kalau Delta tidak pernah lapor "selesai" lewat /getlog.
                    if time.time() - delta_wait_start > DELTA_TIMEOUT:
                        delta_connection_error = f"Delta tidak melaporkan selesai dalam {DELTA_TIMEOUT} detik (macet atau getlog tidak update)"
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
                # Pesan spesifik disimpan untuk dilaporkan sebagai "warning",
                # bukan cuma langsung ng seperti sebelumnya
                delta_connection_error = f"Koneksi ke Delta ESP32 gagal: {e}"
                print(f"❌ {delta_connection_error}")
                real_delta_robot_result = "ng"

            if running:
                cycle_time_delta = round(time.time() - start_time_delta, 2)

                if delta_connection_error:
                    # Gangguan koneksi/timeout -> status "warning" + pesan spesifik,
                    # BUKAN "done" dengan result ng polos seperti sebelumnya. Ini beda dari
                    # cacat produk fisik asli (misal Delta gagal ambil barang tapi tetap
                    # bisa lapor status) yang tetap harus tercatat sebagai "done" + ng biasa.
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

    # ====================================================
    # FINAL PRODUCT EVENT
    # ====================================================
    if running:
        final_result = "ng" if failed_at else "ok"
        payload = {
            "timestamp": datetime.now().isoformat(),
            "product_id": product_id,
            "status": "complete",
            "result": final_result,
            # sertakan warna yang terdeteksi Conveyor2 ke payload final produk,
            # supaya bisa disimpan ke database (production_history) dan muncul di History.
            "warna": conveyor2_warna
        }

        if failed_at:
            payload["terminated_at"] = failed_at

        publish("mes/product", payload)

        # reset SELALU di sini (bukan cuma di dalam branch Delta asli) --
        # sebelumnya kalau Delta masih dummy (False), conveyor2_warna TIDAK PERNAH
        # di-reset, jadi bisa nyangkut/salah dipakai di siklus produk berikutnya.
        conveyor2_warna = None

        if final_result == "ok":
            produced_count += 1
            print(f"✅ === PRODUCT {product_id} COMPLETE (OK) ===")
        else:
            print(f"❌ === PRODUCT {product_id} REJECTED at {failed_at} (NG) ===")

        print(f"Progress Good Units: {produced_count}/{production_target}")

        # NEW: order_sta2 sekarang cuma menjalankan conveyor kecil AGV (unload ke
        # Conveyor2) -- AGV yang barusan dipakai (agv_prefix) TETAP DIAM di Station 2
        # sampai TITIK INI, setelah tahu status akhir 1 cycle produksi (termasuk hasil
        # Delta yang baru saja disimpan/failed di atas). Baru di sini diputuskan:
        #   - target SUDAH tercapai (mis. dashboard nunjukkin 1/1) -> semua AGV
        #     (termasuk yang barusan dipakai ini) dipanggil PULANG (home).
        #   - target BELUM tercapai (mis. masih 1/3) -> AGV yang barusan dipakai balik
        #     ke Station 1/Arm lewat order_sta2_5 (BUKAN order_sta1, karena dia berangkat
        #     dari Station 2, bukan dari home), supaya siap dipakai lagi di gilirannya
        #     nanti (2 produk lagi, karena bergantian dengan AGV lainnya).
        # NEW (REVISI): order_sta2 cuma menjalankan conveyor kecil AGV (unload ke
        # Conveyor2) -- AGV yang barusan dipakai (agv_prefix) TETAP DIAM di Station 2
        # sampai TITIK INI, setelah tahu status akhir 1 cycle produksi (termasuk hasil
        # Delta yang baru saja disimpan/failed di atas). Di sini KEPUTUSAN ARAHNYA
        # (bukan KAPAN dia berangkat) ditentukan oleh status target:
        #   - target SUDAH tercapai (mis. dashboard nunjukkin 1/1) -> semua AGV
        #     (termasuk yang barusan dipakai ini) LANGSUNG dipanggil PULANG (home)
        #     SEKARANG JUGA -- ini kondisi akhir, tidak perlu ditunda.
        #   - target BELUM tercapai (mis. masih 1/3) -> {agv_prefix} JANGAN langsung
        #     disuruh berangkat balik di sini. Cukup tandai "pending" -- baru betul2
        #     disuruh berangkat (order_sta2_5) TEPAT saat AGV LAIN selesai centering
        #     untuk produk berikutnya (lihat release_pending_agv_at_station2()), supaya
        #     keduanya berangkat BERSAMAAN, bukan salah satu menunggu konfirmasi dulu.
        if enable_workcenters["AGV"]:
            if produced_count >= production_target:
                send_all_agv_home()
            else:
                print(f"⏸️ Target belum tercapai ({produced_count}/{production_target}), {agv_prefix} ditandai pending di Station 2 -- akan diberi tahu arah (order_sta2_5) begitu AGV lain mulai berangkat ke Station 2.")
                agv_pending_release[agv_prefix] = True

        # NEW: giliran berikutnya pindah ke AGV lain, apa pun hasil produk ini --
        # karena AGV yang baru selesai sudah diberi perintah lanjutannya sendiri
        # (home atau order_sta2_5) tepat di atas.
        current_agv_idx = (current_agv_idx + 1) % 2

        time.sleep(3)