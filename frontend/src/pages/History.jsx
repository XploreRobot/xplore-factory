import { useEffect, useState } from "react"
import OEEChart from "../components/OEEChart"

export default function History() {
  const [moData,     setMoData]     = useState([])
  const [loading,    setLoading]    = useState(false)
  const [detailData, setDetailData] = useState(null)
  const [open,       setOpen]       = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [showOEEChart, setShowOEEChart] = useState(false) // NEW: toggle OEE chart di modal

  const host = import.meta.env.VITE_BACK_HOST

  /* ---------- Fetch MO list ---------- */
  const fetchMO = () => {
    setLoading(true)
    fetch(`http://${host}:8000/mo_history`)
      .then(r => r.json())
      .then(r => setMoData(r.data ?? []))
      .catch(err => console.error("MO History Error:", err))
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchMO() }, [])

  /* ---------- Open detail modal ---------- */
  const openDetail = async (mo_id) => {
    setDetailLoading(true)
    setOpen(true)
    setDetailData(null)
    setShowOEEChart(false) // reset toggle tiap buka MO baru
    try {
      const res  = await fetch(`http://${host}:8000/mo_detail/${mo_id}`)
      const json = await res.json()
      setDetailData(json.data)
    } catch (err) {
      console.error("MO Detail Error:", err)
    } finally {
      setDetailLoading(false)
    }
  }

  const closeModal = () => { setOpen(false); setDetailData(null) }

  /* ---------- Helpers ---------- */
  const fmtTime = (t) => {
    if (!t) return "—"
    const d = new Date(t)
    return isNaN(d) ? t : d.toLocaleString()
  }

  /* ---------- Derived stats from moData ---------- */
  const totalMOs   = moData.length
  const totalOK    = moData.reduce((s, m) => s + (m.ok_count  ?? 0), 0)
  const totalNG    = moData.reduce((s, m) => s + (m.ng_count  ?? 0), 0)
  const totalUnits = moData.reduce((s, m) => s + (m.total_production ?? 0), 0)
  const avgYield   = totalUnits > 0 ? ((totalOK / totalUnits) * 100).toFixed(1) : "—"

  // NEW: rata-rata OEE seluruh MO (mengasumsikan tiap item punya field oee_rate 0-100)
  const moWithOee = moData.filter(m => m.oee_rate != null)
  const avgOEE = moWithOee.length > 0
    ? (moWithOee.reduce((s, m) => s + m.oee_rate, 0) / moWithOee.length).toFixed(1)
    : "—"

  // NEW: nilai OEE untuk MO yang sedang dibuka di modal
  const detailOeeVal = detailData?.summary?.oee ? detailData.summary.oee.oee : null

  return (
    <div className="p-4 sm:p-6 lg:p-8 bg-background min-h-full animate-fadeIn">

      {/* ── Page Header ── */}
      <div className="flex flex-wrap gap-4 justify-between items-start mb-6 md:mb-8">
        <div>
          <h2 className="text-2xl md:text-headline-xl font-bold text-primary tracking-tight">
            Production History (MO)
          </h2>
          <p className="text-on-surface-variant mt-1 text-sm">
            Manufacturing order summary — click a row to see full detail.
          </p>
        </div>
        <button
          onClick={fetchMO}
          className="shrink-0 flex items-center gap-2 px-3 py-2 bg-white border border-outline-variant text-on-surface rounded-lg text-xs font-bold uppercase tracking-widest hover:bg-surface-container-low transition-colors"
        >
          <span className="material-symbols-outlined text-[16px]">refresh</span>
          <span className="hidden sm:inline">Refresh</span>
        </button>
      </div>

      {/* ── KPI strip ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-6">
        <SummaryCard label="MOs Completed" value={totalMOs}   icon="assignment_turned_in" />
        <SummaryCard label="Total Units"   value={totalUnits} icon="inventory"  />
        <SummaryCard label="Total OK"      value={totalOK}    icon="check_circle" color="text-secondary" />
        <SummaryCard label="Avg Yield"     value={avgYield === "—" ? "—" : `${avgYield}%`} icon="percent" color={parseFloat(avgYield) >= 95 ? "text-secondary" : "text-error"} />
        {/* NEW: KPI Avg OEE, sama gaya seperti KPICard highlight di Dashboard */}
        <SummaryCard label="Avg OEE" value={avgOEE === "—" ? "—" : `${avgOEE}%`} icon="query_stats" color={avgOEE !== "—" && parseFloat(avgOEE) >= 85 ? "text-secondary" : "text-error"} />
      </div>

      {/* ── MO Table ── */}
      <div className="bg-white border border-outline-variant rounded-xl overflow-hidden">
        {/* Table header bar */}
        <div className="flex justify-between items-center px-4 md:px-6 py-3 border-b border-outline-variant">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-[18px]">table_view</span>
            <h3 className="text-sm md:text-base font-bold text-primary">Manufacturing Orders</h3>
          </div>
          <span className="text-xs text-on-surface-variant font-medium">{totalMOs} records</span>
        </div>

        <div className="overflow-x-auto w-full">
          <table className="w-full text-left border-collapse text-sm">
            <thead>
              <tr className="bg-primary text-on-primary">
                {/* NEW: tambah kolom OEE */}
                {["MO", "Total", "OK", "NG", "Yield", "OEE", "Start", "End"].map(h => (
                  <th key={h} className="px-4 py-3 text-[10px] uppercase tracking-widest font-semibold border-b border-primary-container whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-on-surface-variant text-sm">
                    <span className="material-symbols-outlined text-[20px] animate-spin mr-2">progress_activity</span>
                    Loading…
                  </td>
                </tr>
              ) : moData.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-on-surface-variant text-sm">
                    No manufacturing orders found.
                  </td>
                </tr>
              ) : moData.map((item, i) => (
                <tr
                  key={i}
                  onClick={() => openDetail(item.mo_id)}
                  className={`border-b border-outline-variant hover:bg-surface-container-low cursor-pointer transition-colors group ${i % 2 === 1 ? "bg-surface-bright" : ""}`}
                >
                  <td className="px-4 py-3 font-mono font-semibold text-primary group-hover:underline whitespace-nowrap">
                    {item.mo_id}
                  </td>
                  <td className="px-4 py-3 font-medium text-on-surface">{item.total_production ?? "—"}</td>
                  <td className="px-4 py-3 font-semibold text-secondary">{item.ok_count ?? "—"}</td>
                  <td className="px-4 py-3 font-semibold text-error">{item.ng_count ?? "—"}</td>
                  <td className="px-4 py-3">
                    <YieldBadge value={item.yield_rate} />
                  </td>
                  {/* NEW: badge OEE per MO */}
                  <td className="px-4 py-3">
                    <OEEBadge value={item.oee_rate} />
                  </td>
                  <td className="px-4 py-3 text-on-surface-variant whitespace-nowrap">{fmtTime(item.start_time)}</td>
                  <td className="px-4 py-3 text-on-surface-variant whitespace-nowrap">{fmtTime(item.end_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Detail Modal ── */}
      {open && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4 backdrop-blur-sm"
          onClick={e => { if (e.target === e.currentTarget) closeModal() }}
        >
          <div className="bg-white w-full max-w-5xl rounded-xl shadow-2xl max-h-[90vh] flex flex-col overflow-hidden animate-fadeIn">

            {/* Modal header */}
            <div className="flex justify-between items-center px-6 py-4 border-b border-outline-variant shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-primary-container rounded-lg flex items-center justify-center">
                  <span className="material-symbols-outlined text-on-primary text-[16px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                    factory
                  </span>
                </div>
                <div>
                  <h2 className="text-base font-bold text-primary">
                    {detailData ? `MO #${detailData.mo_id}` : "Loading detail…"}
                  </h2>
                  <p className="text-[11px] text-on-surface-variant">Manufacturing Order Detail</p>
                </div>
              </div>
              <button
                onClick={closeModal}
                className="p-2 text-on-surface-variant hover:bg-surface-container rounded-lg transition-colors"
              >
                <span className="material-symbols-outlined text-[20px]">close</span>
              </button>
            </div>

            {/* Modal body — scrollable */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">

              {detailLoading ? (
                <div className="flex items-center justify-center py-16 text-on-surface-variant">
                  <span className="material-symbols-outlined animate-spin text-[24px] mr-3">progress_activity</span>
                  Loading detail…
                </div>
              ) : detailData ? (
                <>
                  {/* Summary cards */}
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
                    <ModalCard label="Total"  value={detailData.summary.total} />
                    <ModalCard label="OK"     value={detailData.summary.ok}    color="text-secondary" />
                    <ModalCard label="NG"     value={detailData.summary.ng}    color="text-error" />
                    <ModalCard label="Yield"  value={`${detailData.summary.yield_rate}%`}
                      color={detailData.summary.yield_rate >= 95 ? "text-secondary" : "text-error"} />
                    {/* NEW: card OEE, klik untuk toggle breakdown chart seperti di Dashboard */}
                    <ModalCard
                      label="OEE"
                      value={detailOeeVal !== null ? `${detailOeeVal}%` : "—"}
                      color={detailOeeVal !== null ? (detailOeeVal >= 85 ? "text-secondary" : "text-error") : "text-on-surface"}
                      onClick={detailOeeVal !== null ? () => setShowOEEChart(prev => !prev) : undefined}
                    />
                  </div>

                  {/* NEW: OEE Breakdown chart (toggle), reuse komponen OEEChart dari Dashboard */}
                  {showOEEChart && detailData.summary.oee && (
                    <div className="bg-white border border-outline-variant rounded-xl p-6 animate-fadeIn">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="text-base font-bold text-primary">OEE Breakdown</h3>
                        <button
                          onClick={() => setShowOEEChart(false)}
                          className="p-1.5 text-on-surface-variant hover:bg-surface-container rounded-lg transition-colors"
                        >
                          <span className="material-symbols-outlined text-[18px]">close</span>
                        </button>
                      </div>
                      {/* OEEChart mengharapkan prop `production` dengan shape { oee: {...} } sama seperti di Dashboard */}
                      <OEEChart production={{ oee: detailData.summary.oee }} />
                    </div>
                  )}

                  {/* Production history sub-table */}
                  <ModalTable
                    title="Production History"
                    icon="inventory_2"
                    headers={["Product ID", "Result", "Warna", "Start", "End"]}
                    rows={detailData.production_history}
                    renderRow={(p, i) => (
                      <tr key={i} className={`border-b border-outline-variant hover:bg-surface-container-low transition-colors ${i % 2 === 1 ? "bg-surface-bright" : ""}`}>
                        <td className="px-4 py-2.5 font-mono text-sm font-medium text-primary">{p.product_id}</td>
                        <td className="px-4 py-2.5"><ResultBadge value={p.result} /></td>
                        {/* NEW: kolom warna hasil deteksi Conveyor2 (hitam/putih), sebelumnya
                            data ini cuma live broadcast dan tidak pernah tersimpan/ditampilkan di History */}
                        <td className="px-4 py-2.5">
                          {p.warna ? (
                            <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                              p.warna === "hitam" ? "bg-gray-800 text-white" : "bg-gray-100 text-gray-700 border border-gray-300"
                            }`}>
                              <span className={`w-2 h-2 rounded-full border border-gray-400 ${p.warna === "hitam" ? "bg-black" : "bg-white"}`} />
                              {p.warna}
                            </span>
                          ) : (
                            <span className="text-on-surface-variant">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-sm text-on-surface-variant whitespace-nowrap">{fmtTime(p.start_time)}</td>
                        <td className="px-4 py-2.5 text-sm text-on-surface-variant whitespace-nowrap">{fmtTime(p.end_time)}</td>
                      </tr>
                    )}
                  />

                  {/* Workcenter log sub-table */}
                  <ModalTable
                    title="Workcenter Log"
                    icon="timeline"
                    headers={["Time", "Product ID", "Workcenter", "Status", "Result", "Cycle"]}
                    rows={detailData.workcenter_log}
                    renderRow={(l, i) => (
                      <tr key={i} className={`border-b border-outline-variant hover:bg-surface-container-low transition-colors ${i % 2 === 1 ? "bg-surface-bright" : ""}`}>
                        <td className="px-4 py-2.5 text-sm text-on-surface-variant whitespace-nowrap">{fmtTime(l.timestamp)}</td>
                        <td className="px-4 py-2.5 font-mono text-sm font-medium text-primary">{l.product_id}</td>
                        <td className="px-4 py-2.5 text-sm text-on-surface">{l.workcenter}</td>
                        <td className="px-4 py-2.5">
                          <StatusBadge value={l.status} />
                        </td>
                        <td className="px-4 py-2.5"><ResultBadge value={l.result} /></td>
                        <td className="px-4 py-2.5 text-sm font-mono font-medium text-secondary text-right">
                          {l.cycle_time ? `${l.cycle_time}s` : "—"}
                        </td>
                      </tr>
                    )}
                  />
                </>
              ) : (
                <div className="py-16 text-center text-on-surface-variant text-sm">
                  Failed to load detail.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Sub-components ── */

function SummaryCard({ label, value, icon, color = "text-primary" }) {
  return (
    <div className="relative overflow-hidden bg-white border border-outline-variant rounded-xl p-4">
      <div className="flex justify-between items-start mb-2">
        <span className="text-[10px] uppercase tracking-widest font-semibold text-on-surface-variant">{label}</span>
        <span className={`material-symbols-outlined text-[18px] ${color}`}>{icon}</span>
      </div>
      <span className={`text-2xl font-medium font-mono ${color}`}>{value}</span>
      <div className="absolute bottom-0 left-0 w-full h-0.5 bg-surface-container-highest" />
    </div>
  )
}

// NEW: tambah prop onClick agar ModalCard bisa jadi tombol toggle (dipakai untuk card OEE)
function ModalCard({ label, value, color = "text-on-surface", onClick }) {
  return (
    <div
      onClick={onClick}
      className={`bg-surface-container-low border border-outline-variant rounded-xl p-4 ${onClick ? "cursor-pointer hover:bg-surface-container transition-colors" : ""}`}
    >
      <div className="text-[10px] uppercase tracking-widest font-semibold text-on-surface-variant mb-1">{label}</div>
      <div className={`text-2xl font-medium font-mono ${color}`}>{value}</div>
    </div>
  )
}

function ModalTable({ title, icon, headers, rows, renderRow }) {
  return (
    <div className="bg-white border border-outline-variant rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant">
        <span className="material-symbols-outlined text-primary text-[16px]">{icon}</span>
        <h3 className="text-sm font-bold text-primary">{title}</h3>
        <span className="ml-auto text-xs text-on-surface-variant">{rows.length} rows</span>
      </div>
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left border-collapse text-sm">
          <thead>
            <tr className="bg-primary text-on-primary">
              {headers.map(h => (
                <th key={h} className="px-4 py-2.5 text-[10px] uppercase tracking-widest font-semibold border-b border-primary-container whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={headers.length} className="px-4 py-6 text-center text-on-surface-variant">No records.</td></tr>
            ) : rows.map(renderRow)}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function YieldBadge({ value }) {
  if (value == null) return <span className="text-on-surface-variant">—</span>
  const pct = parseFloat(value)
  const cls = pct >= 95
    ? "bg-secondary-fixed text-on-secondary-fixed"
    : pct >= 85
    ? "bg-yellow-100 text-yellow-700"
    : "bg-error-container text-on-error-container"
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold ${cls}`}>
      {value}%
    </span>
  )
}

// NEW: badge OEE per baris MO, dengan skema warna sama seperti YieldBadge
function OEEBadge({ value }) {
  if (value == null) return <span className="text-on-surface-variant">—</span>
  const pct = parseFloat(value)
  const cls = pct >= 85
    ? "bg-secondary-fixed text-on-secondary-fixed"
    : pct >= 60
    ? "bg-yellow-100 text-yellow-700"
    : "bg-error-container text-on-error-container"
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold ${cls}`}>
      {value}%
    </span>
  )
}

function ResultBadge({ value }) {
  if (!value) return <span className="text-on-surface-variant">—</span>
  const ok = value.toLowerCase() === "ok"
  const ng = value.toLowerCase() === "ng"
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
      ok ? "bg-secondary-fixed text-on-secondary-fixed"
        : ng ? "bg-error-container text-on-error-container"
        : "bg-surface-container text-on-surface-variant"
    }`}>
      {value.toUpperCase()}
    </span>
  )
}

function StatusBadge({ value }) {
  if (!value) return <span className="text-on-surface-variant">—</span>
  const cls = value.toLowerCase() === "start"
    ? "bg-blue-100 text-blue-700"
    : "bg-green-100 text-green-700"
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${cls}`}>
      {value}
    </span>
  )
}
