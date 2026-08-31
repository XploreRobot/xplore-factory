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

  const workcenters = production.workcenters || {}

  /* ---------- WebSocket ---------- */
  useEffect(() => {
    const ws = new WebSocket(`ws://${host}:8000/ws`)
    ws.onopen  = () => console.log("WebSocket CONNECTED")
    ws.onerror = (err) => console.error("WebSocket ERROR", err)
    ws.onclose = () => console.log("WebSocket CLOSED")
    ws.onmessage = (event) => {
      try { setProduction(JSON.parse(event.data)) }
      catch (err) { console.error("Failed to parse WebSocket message", err) }
    }
    return () => ws.close()
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

  /* ---------- Flow stations ---------- */
  const flowStations = [
    { name: "Conveyor 1",  src: ImgConveyor1,  statusKey: ["Conveyor1", "Conveyor 1"] },
    { name: "Arm Robot",   src: ImgArmRobot,   statusKey: ["ArmRobot", "Arm Robot"] },
    { name: "AGV Mobile",  src: ImgAGV,        statusKey: ["AGV"] },
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
          <div className="bg-surface-container-low border border-outline-variant px-3 py-1.5 rounded-full flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-secondary animate-pulse-dot" />
            <span className="text-[11px] uppercase tracking-widest font-bold text-primary">Optimal</span>
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
            {Object.entries(workcenters).map(([name, wc]) => (
              <WorkcenterCard
                key={name}
                name={name}
                status={wc.status}
                cycle={`${wc.cycle}s`}
                ok={wc.ok}
                ng={wc.ng}
              />
            ))}
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
              return (
                <FlowStation key={idx} name={station.name} src={station.src} status={status} />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Flow Station Component ── */
function FlowStation({ name, src, status }) {
  const isRunning = status === "RUNNING"
  return (
    <div className="flex flex-col items-center min-w-[110px]">
      <div className={`relative p-3 rounded-xl border-2 transition-all duration-500 ${
        isRunning
          ? "border-secondary bg-secondary/5 shadow-[0_0_16px_rgba(55,85,195,0.25)] scale-105"
          : "border-outline-variant bg-surface-container-low opacity-60 grayscale-[40%]"
      }`}>
        <img src={src} alt={name} className="w-20 h-20 object-contain" />

        {/* Active dot */}
        {isRunning && (
          <span className="absolute -top-1.5 -right-1.5 flex h-3.5 w-3.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-secondary opacity-60" />
            <span className="relative inline-flex rounded-full h-3.5 w-3.5 bg-secondary border-2 border-white" />
          </span>
        )}
      </div>
      <p className={`mt-3 text-xs font-bold text-center ${isRunning ? "text-primary" : "text-on-surface-variant"}`}>
        {name}
      </p>
      <span className={`mt-1 text-[9px] uppercase tracking-widest font-bold px-2 py-0.5 rounded-full ${
        isRunning
          ? "bg-secondary/10 text-secondary"
          : "bg-outline-variant/30 text-outline"
      }`}>
        {status}
      </span>
    </div>
  )
}