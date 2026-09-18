# NEW: sekarang ada 2 unit AGV fisik yang bekerja bergantian (round-robin), lihat
# dummy_production.py. Dipakai bersama oleh mqtt_service.py supaya daftar prefix
# hanya didefinisikan sekali di sini.
AGV_PREFIXES = ["agv1", "agv2"]

# NEW: bentuk data monitoring 1 unit AGV -- dipakai sebagai template untuk MASING-MASING
# prefix di production_state["agv"], supaya tidak duplikasi struktur dict manual per unit.
def _make_agv_slot():
    return {
        "normal": {},     # dari <prefix>/monitor/normal (state "agv" ber-prefix AGV1_/AGV2_, sensor jarak, ip, rssi, dst)
        "livetrack": {},  # dari <prefix>/monitor/livetrack (posX, posY, thetha, RPM encoder)
        "alarm": {},      # dari <prefix>/monitor/alarm (obstacle detected, offtrack)
        "state": None,    # dari <prefix>/monitor/state (plain text, bukan JSON -- raw state AGV1_.../AGV2_...)
        # NEW: status koneksi ASLI berbasis heartbeat (bukan tebakan timeout per-langkah),
        # dihitung independen per unit AGV oleh mqtt_service.py.
        # "CONNECTED" begitu ada pesan apa pun masuk dari topic monitor AGV itu,
        # "DISCONNECTED" kalau tidak ada pesan sama sekali > AGV_HEARTBEAT_TIMEOUT detik.
        "connection_status": "UNKNOWN"
    }

production_state = {
    "total": 0,
    "ok": 0,
    "ng": 0,
    "workcenters": {},
    "oee": {},
    "oee_wc": {},
    "target": 0,
    "progress": 0,
    "mqtt_connected": True,  # NEW: status koneksi backend<->broker MQTT, untuk indikator global
    # NEW: data AGV asli dari topic agv1/monitor/* DAN agv2/monitor/*, key = prefix
    # ("agv1"/"agv2") supaya kedua unit dimonitor terpisah, bukan ditimpa satu sama lain
    # seperti sebelumnya. Diisi oleh mqtt_service.py, dibaca frontend lewat WebSocket
    # broadcast yang sudah ada.
    "agv": {prefix: _make_agv_slot() for prefix in AGV_PREFIXES}
}

production_target = 0
produced_count = 0
ok_count = 0
ng_count = 0

start_time = None
end_time = None

current_mo_id = None
odoo = None


def reset_state():
    """
    Reset all production counters and workcenter data when a new MO starts.
    Mutates in-place so every module that imported state sees the reset.

    NOTE (NEW): data "agv" SENGAJA TIDAK direset di sini. AGV adalah unit fisik
    yang jalan terus-menerus lintas-MO (tidak seperti workcenters production
    yang memang per-siklus produk) -- jadi status/posisi terakhirnya tetap
    relevan dipertahankan meski MO baru mulai.
    """
    import core.state as _s

    # Reset scalar counters
    _s.produced_count = 0
    _s.ok_count = 0
    _s.ng_count = 0
    _s.start_time = None
    _s.end_time = None

    # Reset production_state dict in-place
    _s.production_state["total"] = 0
    _s.production_state["ok"] = 0
    _s.production_state["ng"] = 0
    _s.production_state["workcenters"] = {}
    _s.production_state["oee"] = {}
    _s.production_state["oee_wc"] = {}
    _s.production_state["progress"] = 0
    # "target" will be updated by the caller right after reset

    print("[RESET] State reset for new MO")