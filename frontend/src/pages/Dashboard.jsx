import { useEffect, useState } from "react"
import { useAuth } from "../context/AuthContext"
import { Play, Square, RotateCcw, ShieldAlert } from "lucide-react"
import WorkcenterCard from "../components/WorkcenterCard"
import KPICard from "../components/KPICard"
import ImgConveyor1 from "../assets/Robot Images/Conveyor1.png"
import ImgArmRobot from "../assets/Robot Images/ArmRobot.png"
import ImgAGV from "../assets/Robot Images/AGV.png"
import ImgConveyor2 from "../assets/Robot Images/Conveyor2.png"
import ImgDeltaRobot from "../assets/Robot Images/DeltaRobot.png"
import OEEChart from "../components/OEEChart"
import ProgressBar from "../components/ProgressBar"
import toast, { Toaster } from "react-hot-toast"

export default function Dashboard() {
  const host = import.meta.env.VITE_BACK_HOST
  const { user } = useAuth()

  const [targetReached, setTargetReached] = useState(false)
  const [showOEEChart, setShowOEEChart] = useState(false)
  const [isControlLoading, setIsControlLoading] = useState(false)

  const isViewer = user?.role === 'viewer'

  const handleControl = async (command) => {
    if (isViewer) {
      toast.error("You don't have permission to control the machine")
      return
    }

    setIsControlLoading(true)
    const token = localStorage.getItem('token')
    const backendUrl = host.includes(':') ? `http://${host}` : `http://${host}:8000`

    try {
      const response = await fetch(`${backendUrl}/control?command=${command}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })

      // PERBAIKAN: Tangkap pesan error spesifik dari backend FastAPI
      if (!response.ok) {
        let errorMsg = 'Failed to send command';
        try {
          const errorData = await response.json();
          errorMsg = errorData.detail || errorMsg;
        } catch (parseErr) {
          // Abaikan jika tidak bisa di-parse
        }
        throw new Error(errorMsg);
      }
      
      toast.success(`Machine command "${command}" sent`, {
        icon: '🚀',
        style: { borderRadius: '10px', background: '#001f51', color: '#fff' }
      })
    } catch (error) {
      toast.error(error.message) // Sekarang akan menampilkan error asli dari FastAPI
    } finally {
      setIsControlLoading(false)
    }
  }

  const [production, setProduction] = useState({
    total: 0,
    ok: 0,
    ng: 0,
    workcenters: {}
  })

  // NEW: status koneksi WebSocket real, dipakai untuk indikator visual di header
  // ("Optimal" vs "Reconnecting..."). Sebelumnya tidak ada state ini sama sekali --
  // badge status di header cuma teks statis, tidak benar-benar mencerminkan
  // apakah data yang ditampilkan masih live atau sudah beku karena koneksi putus.
  const [wsConnected, setWsConnected] = useState(false)

  const workcenters = production.workcenters || {}

  /* ---------- WebSocket ---------- */
  // NEW: seluruh blok ini diganti dari "buat 1 koneksi sekali, kalau putus ya sudah"
  // menjadi auto-reconnect dengan backoff. Kenapa: sebelumnya kalau WebSocket putus
  // (misal WiFi/hotspot glitch sedetik saat demo), dashboard diam PERMANEN sampai
  // di-refresh manual browser -- tidak ada indikasi apa pun ke yang sedang menonton.
  useEffect(() => {
    let ws = null
    let reconnectTimer = null
    let reconnectDelay = 1000 // NEW: mulai dari 1 detik
    const MAX_RECONNECT_DELAY = 10000 // NEW: naik bertahap sampai maksimal 10 detik (backoff)
    let isUnmounted = false // NEW: flag supaya tidak reconnect lagi setelah component di-unmount

    const connect = () => {
      ws = new WebSocket(`ws://${host}:8000/ws`)

      ws.onopen = () => {
        console.log("WebSocket CONNECTED")
        setWsConnected(true)
        reconnectDelay = 1000 // NEW: reset backoff begitu berhasil connect lagi
      }

      ws.onerror = (err) => console.error("WebSocket ERROR", err)

      ws.onclose = () => {
        console.log("WebSocket CLOSED")
        setWsConnected(false)
        if (isUnmounted) return
        // NEW: jadwalkan percobaan reconnect otomatis, dengan jeda yang makin lama
        // tiap gagal (exponential backoff) supaya tidak spam broker kalau memang mati total
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
          connect()
        }, reconnectDelay)
      }

      ws.onmessage = (event) => {
        try { setProduction(JSON.parse(event.data)) }
        catch (err) { console.error("Failed to parse WebSocket message", err) }
      }
    }

    connect()

    // NEW: cleanup yang benar -- matikan flag, batalkan timer reconnect yang
    // masih terjadwal, dan tutup koneksi aktif. Tanpa ini, kalau user pindah
    // halaman saat lagi reconnecting, timer akan tetap jalan di background
    // dan bikin koneksi WS menumpuk.
    return () => {
      isUnmounted = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [])

  /* ---------- Target toast ---------- */
  useEffect(() => {
    if (production.target > 0 && production.progress >= production.target && !targetReached) {
      toast.success("🎯 Target production achieved!", {
        duration: 4000,
        style: { background: "#001f51", color: "#fff", fontWeight: "600" }
      })
      setTargetReached(true)

      // Emit event for TopBar notification bell
      window.dispatchEvent(new CustomEvent('addNotification', {
        detail: {
          title: "Target Achieved",
          message: `Production target of ${production.target} units has been successfully reached.`,
          timestamp: new Date().toISOString()
        }
      }))
    }
  }, [production.progress, production.target, targetReached])

  useEffect(() => { setTargetReached(false) }, [production.target])

  /* ---------- Derived values ---------- */
  const oeeVal   = production.oee ? production.oee.oee : null
  const availWc  = Object.keys(workcenters).length
  const defectRate = production.total > 0
    ? ((production.ng / production.total) * 100).toFixed(1)
    : "0.0"

  const getWcStatus = (searchName) => {
    const wc = Object.entries(workcenters).find(
      ([key]) => key.toLowerCase() === searchName.toLowerCase()
    )
    return wc ? wc[1].status : "IDLE"
  }

  // NEW: klasifikasi state asli AGV (dari firmware, via production.agv.normal.agv)
  // jadi kategori warna (RUNNING/STANDBY/IDLE) + label yang ditampilkan.
  // Firmware final AGV mem-prefix semua state dengan "AGV1_", contoh state asli:
  // AGV1_HOME_IDLE, AGV1_HOME_TO_STATION_1, AGV1_LOADING_AT_STATION_1,
  // AGV1_MOVING_TO_STATION_2, AGV1_UNLOADING_AT_STATION_2, AGV1_MOVING_TO_STATION_1, dst.
  const classifyAgvState = (rawState) => {
    if (!rawState) return null
    const upper = rawState.toUpperCase()
    let category = "RUNNING" // default: sedang bergerak (mis. *_TO_STATION_*)
    if (upper.includes("IDLE")) category = "IDLE"
    else if (upper.includes("LOADING") || upper.includes("UNLOADING")) category = "STANDBY"
    // strip prefix "AGV1_" (atau "AGV<n>_" secara umum) biar label bersih di badge
    const cleanLabel = upper.replace(/^AGV\d*_/, "").replace(/_/g, " ")
    return { category, label: cleanLabel }
  }

  // NEW: sekarang ada 2 unit AGV fisik (agv1 & agv2) bekerja bergantian, dipantau
  // INDEPENDEN oleh backend (production.agv.agv1 / production.agv.agv2). Sebelumnya
  // cuma 1 slot "production.agv" untuk 1 unit -- sekarang dihitung per-prefix supaya
  // status AGV1 & AGV2 tidak saling menimpa/tercampur di UI.
  const AGV_PREFIXES = ["agv1", "agv2"]

  const classifyAgvUnit = (prefix) => {
    const agvRealState = production.agv?.[prefix]?.normal?.agv || null
    let classified = classifyAgvState(agvRealState)

    // NEW: status koneksi ASLI (heartbeat dari backend), bukan tebakan timeout per-langkah.
    // Ini PRIORITAS DI ATAS status gerak (LOADING/MOVING/dst) -- kalau AGV ini memang
    // terputus, tampilkan itu, bukan status gerak terakhir yang sudah basi/tidak update lagi.
    // Dicek per-unit -- AGV1 terputus tidak membuat AGV2 ikut ditandai terputus.
    const connectionStatus = production.agv?.[prefix]?.connection_status || null
    if (connectionStatus === "DISCONNECTED") {
      classified = { category: "WARNING", label: `${prefix.toUpperCase()} TERPUTUS (tidak ada data)` }
    }
    return { classified, connectionStatus }
  }

  const agvUnits = Object.fromEntries(AGV_PREFIXES.map(p => [p, classifyAgvUnit(p)]))

  /* ---------- Flow stations ---------- */
  // NEW: "AGV Mobile" tunggal dipecah jadi 2 stasiun terpisah (AGV1 & AGV2) yang
  // masing-masing membaca status dari kartu workcenter "AGV1"/"AGV2" (lihat backend:
  // mqtt_service.py memecah payload mes/wc/AGV berdasarkan field agv_prefix).
  const flowStations = [
    { name: "Conveyor 1",  src: ImgConveyor1,  statusKey: ["Conveyor1", "Conveyor 1"] },
    { name: "Arm Robot",   src: ImgArmRobot,   statusKey: ["ArmRobot", "Arm Robot"] },
    { name: "AGV1 Mobile", src: ImgAGV,        statusKey: ["AGV1"], agvPrefix: "agv1" },
    { name: "AGV2 Mobile", src: ImgAGV,        statusKey: ["AGV2"], agvPrefix: "agv2" },
    { name: "Conveyor 2",  src: ImgConveyor2,  statusKey: ["Conveyor2", "Conveyor 2"] },
    { name: "Robot Delta", src: ImgDeltaRobot, statusKey: ["Delta", "delta"] },
  ]

  return (
    <div className="p-4 sm:p-6 lg:p-8 bg-background min-h-full animate-fadeIn">
      <Toaster position="top-right" />

      {/* ── Page Header ── */}
      <div className="flex justify-between items-end mb-8">
        <div>
          <h2 className="text-headline-xl font-bold text-primary tracking-tight">Assembly Line</h2>
          <p className="text-on-surface-variant mt-1 text-sm">Real-time production metrics and component flow tracking.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[11px] uppercase tracking-widest font-semibold text-on-surface-variant">Status</span>
          {/* NEW: badge ini sebelumnya statis (selalu "Optimal" warna biru, tidak
              peduli koneksi WS beneran hidup atau tidak). Sekarang dihubungkan ke
              wsConnected -- kalau putus/reconnecting, badge berubah jadi "Reconnecting..."
              warna oranye supaya kelihatan jelas ada masalah, bukan diam kelihatan normal. */}
          <div className={`border px-3 py-1.5 rounded-full flex items-center gap-2 ${
            wsConnected
              ? "bg-surface-container-low border-outline-variant"
              : "bg-orange-50 border-orange-300"
          }`}>
            <span className={`w-2 h-2 rounded-full ${
              wsConnected ? "bg-secondary animate-pulse-dot" : "bg-orange-500 animate-pulse"
            }`} />
            <span className={`text-[11px] uppercase tracking-widest font-bold ${
              wsConnected ? "text-primary" : "text-orange-600"
            }`}>
              {wsConnected ? "Optimal" : "Reconnecting..."}
            </span>
          </div>
        </div>
      </div>

      {/* ── Target Banner ── */}
      {production.progress >= production.target && production.target > 0 && (
        <div className="mb-6 bg-primary-container text-on-primary px-6 py-4 rounded-xl flex items-center gap-3 animate-fadeIn">
          <span className="material-symbols-outlined text-on-primary-container text-[22px]">emoji_events</span>
          <span className="font-semibold tracking-wide text-on-primary">🎯 TARGET PRODUCTION ACHIEVED</span>
        </div>
      )}

      {/* ── Progress Bar ── */}
      <div className="bg-white border border-outline-variant rounded-xl p-6 mb-6">
        <ProgressBar
          value={production.progress || 0}
          target={production.target || 0}
          label="Collected"
        />
      </div>

      {/* ── Control Panel (Admin/Operator Only) ── */}
      <div className="bg-white border border-outline-variant rounded-xl p-6 mb-6">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div>
            <h3 className="text-base font-bold text-primary flex items-center gap-2 uppercase tracking-wide">
              Machine Control
            </h3>
            <p className="text-[11px] text-on-surface-variant font-medium mt-1">Manual override and station synchronization</p>
          </div>

          {isViewer ? (
            <div className="flex items-center gap-3 px-4 py-2 bg-surface-container-low border border-outline-variant rounded-lg text-on-surface-variant italic text-xs">
              <ShieldAlert size={16} className="text-outline" />
              Monitoring mode only (Read-only access)
            </div>
          ) : (
            <div className="flex flex-wrap gap-3 w-full sm:w-auto">
              <button 
                onClick={() => handleControl('start')}
                disabled={isControlLoading}
                className="flex-1 sm:flex-none flex items-center justify-center gap-2 bg-success text-white px-6 py-2.5 rounded-xl font-bold text-xs hover:bg-success/90 transition-all active:scale-95 disabled:opacity-50"
              >
                <Play size={14} fill="currentColor" /> START
              </button>
              <button 
                onClick={() => handleControl('stop')}
                disabled={isControlLoading}
                className="flex-1 sm:flex-none flex items-center justify-center gap-2 bg-error text-white px-6 py-2.5 rounded-xl font-bold text-xs hover:bg-error/90 transition-all active:scale-95 disabled:opacity-50"
              >
                <Square size={14} fill="currentColor" /> STOP
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4 md:gap-6 mb-6">
        <KPICard
          title="Production Today"
          value={production.total}
          icon="inventory"
          trend="up"
          trendLabel={`+${production.ok ?? 0} OK units`}
        />
        <KPICard
          title="Overall Equipment Effectiveness"
          value={oeeVal !== null ? oeeVal : "—"}
          unit={oeeVal !== null ? "%" : ""}
          icon="query_stats"
          trend={oeeVal !== null ? (oeeVal >= 85 ? "up" : "down") : null}
          trendLabel={oeeVal !== null ? (oeeVal >= 85 ? "Above target" : "Below target") : null}
          highlight
          onClick={() => setShowOEEChart(prev => !prev)}
        />
        <KPICard
          title="Good Units"
          value={production.ok}
          unit="units"
          icon="check_circle"
          trend="up"
          trendLabel="OK output"
        />
        <KPICard
          title="Defect Rate"
          value={defectRate}
          unit="%"
          icon="gpp_maybe"
          trend={parseFloat(defectRate) > 2 ? "down" : "up"}
          trendLabel={`${production.ng ?? 0} rejects today`}
        />
        <KPICard
          title="Active Workcenters"
          value={availWc}
          icon="hub"
          trendLabel={`${availWc} stations online`}
        />
      </div>

      {/* ── OEE Chart (toggle) ── */}
      {showOEEChart && (
        <div className="bg-white border border-outline-variant rounded-xl p-6 mb-6 animate-fadeIn">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-base font-bold text-primary">OEE Breakdown</h3>
            <button
              onClick={() => setShowOEEChart(false)}
              className="p-1.5 text-on-surface-variant hover:bg-surface-container rounded-lg transition-colors"
            >
              <span className="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <OEEChart production={production} />
        </div>
      )}

      {/* ── Workcenter Cards ── */}
      {availWc > 0 && (
        <div className="mb-6">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-base font-bold text-primary uppercase tracking-wide">Workcenters</h3>
            <span className="text-[11px] text-on-surface-variant uppercase tracking-wider font-semibold">{availWc} active</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 md:gap-6">
            {Object.entries(workcenters).map(([name, wc]) => {
              // NEW: khusus entry AGV1/AGV2 (dulu cuma 1 entry "AGV" gabungan), kalau
              // tidak lagi WARNING dari siklus produksi, pakai klasifikasi detail dari
              // state asli firmware unit itu (sama seperti kartu "AGV Mobile" di Live
              // Production Flow) supaya kartu WORKCENTERS ini juga bisa nunjukkin
              // STANDBY, bukan cuma RUNNING/IDLE generik dari backend.
              // TAPI kalau AGV ini benar-benar terputus (heartbeat), itu PALING prioritas.
              const agvPrefixForEntry = AGV_PREFIXES.find(p => p.toUpperCase() === name.toUpperCase())
              const isAgvEntry = Boolean(agvPrefixForEntry)
              const agvUnit = isAgvEntry ? agvUnits[agvPrefixForEntry] : null
              const displayStatus = (isAgvEntry && agvUnit?.classified && wc.status !== "WARNING")
                ? agvUnit.classified.category
                : wc.status

              // NEW: pesan warning gabungan -- prioritaskan status koneksi asli
              // (heartbeat) di atas pesan warning dari siklus produksi (timeout per-langkah)
              const displayWarningMessage = (isAgvEntry && agvUnit?.connectionStatus === "DISCONNECTED")
                ? `${name} tidak mengirim data sama sekali beberapa detik terakhir — kemungkinan mati atau koneksi terputus (heartbeat)`
                : wc.warning_message

              return (
                <WorkcenterCard
                  key={name}
                  name={name}
                  status={displayStatus}
                  cycle={`${wc.cycle}s`}
                  ok={wc.ok}
                  ng={wc.ng}
                  warningMessage={displayWarningMessage}
                />
              )
            })}
          </div>
        </div>
      )}

      {/* ── Live Production Flow ── */}
      <div className="bg-white border border-outline-variant rounded-xl overflow-hidden">
        <div className="flex justify-between items-center px-6 py-4 border-b border-outline-variant">
          <h3 className="text-base font-bold text-primary">Live Production Flow</h3>
          <div className="flex gap-5">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-secondary rounded-full" />
              <span className="text-xs text-on-surface-variant font-medium">Running</span>
            </div>
            {/* TAMBAHAN LEGENDA STANDBY */}
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-orange-500 rounded-full" />
              <span className="text-xs text-on-surface-variant font-medium">Standby</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-outline-variant rounded-full" />
              <span className="text-xs text-on-surface-variant font-medium">Idle</span>
            </div>
          </div>
        </div>

        <div className="p-8 bg-surface-bright relative">
          {/* connector line */}
          <div className="absolute top-1/2 left-16 right-16 h-[2px] border-t-2 border-dashed border-secondary/40 -translate-y-1/2 z-0" />

          <div className="flex justify-between items-center relative z-10 gap-4 overflow-x-auto">
            {flowStations.map((station, idx) => {
              const status = station.statusKey.reduce(
                (found, key) => found || getWcStatus(key),
                null
              )
              // NEW: khusus kartu AGV1/AGV2 Mobile, kalau data status asli firmware unit
              // itu ada, pakai itu (lebih detail: LOADING AT STATION 1, MOVING TO STATION 2,
              // dst) alih-alih status generik RUNNING/IDLE dari mes/wc/AGV. Tiap kartu AGV
              // baca klasifikasi unitnya sendiri lewat station.agvPrefix.
              const agvUnitForCard = station.agvPrefix ? agvUnits[station.agvPrefix] : null
              const overrideCategory = agvUnitForCard?.classified ? agvUnitForCard.classified.category : undefined
              const overrideLabel = agvUnitForCard?.classified ? agvUnitForCard.classified.label : undefined

              return (
                <FlowStation
                  key={idx}
                  name={station.name}
                  src={station.src}
                  status={status}
                  overrideCategory={overrideCategory}
                  overrideLabel={overrideLabel}
                />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Flow Station Component ── */
// NEW: tambah props overrideCategory ('RUNNING'|'STANDBY'|'IDLE') dan overrideLabel
// (teks status asli, mis. "LOADING AT STATION 1") supaya kartu AGV Mobile bisa
// menampilkan status detail dari firmware asli, bukan cuma RUNNING/IDLE generik.
function FlowStation({ name, src, status, overrideCategory, overrideLabel }) {
  const derivedStatus = status?.toUpperCase() || "IDLE"
  const currentStatus = overrideLabel || derivedStatus

  const category = overrideCategory
    || (derivedStatus === "RUNNING" || derivedStatus === "START" ? "RUNNING"
      : derivedStatus === "STANDBY" ? "STANDBY"
      : "IDLE")

  const isRunning = category === "RUNNING"
  const isStandby = category === "STANDBY"
  // NEW: kategori WARNING -- dipakai saat AGV terputus koneksi (heartbeat) atau
  // gangguan sistem lain, dibedakan dari Standby (oranye, kondisi normal loading/unloading)
  const isWarning = category === "WARNING"

  return (
    <div className="flex flex-col items-center min-w-[110px]">
      <div className={`relative p-3 rounded-xl border-2 transition-all duration-500 ${
        isRunning
          ? "border-secondary bg-secondary/5 shadow-[0_0_16px_rgba(55,85,195,0.25)] scale-105 grayscale-0"
          : isStandby
          ? "border-orange-500 bg-orange-500/10 shadow-[0_0_16px_rgba(249,115,22,0.25)] scale-105 grayscale-0"
          : isWarning
          ? "border-yellow-400 bg-yellow-400/10 shadow-[0_0_16px_rgba(234,179,8,0.3)] scale-105 grayscale-0"
          : "border-outline-variant bg-surface-container-low opacity-60 grayscale-[40%]"
      }`}>
        <img src={src} alt={name} className="w-20 h-20 object-contain" />

        {(isRunning || isStandby || isWarning) && (
          <span className="absolute -top-1.5 -right-1.5 flex h-3.5 w-3.5">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${isRunning ? 'bg-secondary' : isStandby ? 'bg-orange-500' : 'bg-yellow-500'}`} />
            <span className={`relative inline-flex rounded-full h-3.5 w-3.5 border-2 border-white ${isRunning ? 'bg-secondary' : isStandby ? 'bg-orange-500' : 'bg-yellow-500'}`} />
          </span>
        )}
      </div>
      <p className={`mt-3 text-xs font-bold text-center ${isRunning ? "text-primary" : isStandby ? "text-orange-600" : isWarning ? "text-yellow-700" : "text-on-surface-variant"}`}>
        {name}
      </p>
      <span className={`mt-1 text-[9px] uppercase tracking-widest font-bold px-2 py-0.5 rounded-full ${
        isRunning
          ? "bg-secondary/10 text-secondary"
          : isStandby
          ? "bg-orange-100 text-orange-600"
          : isWarning
          ? "bg-yellow-100 text-yellow-700"
          : "bg-outline-variant/30 text-outline"
      }`}>
        {currentStatus}
      </span>
    </div>
  )
}