export default function WorkcenterCard({ name, status, cycle, ok, ng, warningMessage }) {
  const currentStatus = status?.toUpperCase() || "UNKNOWN"

  const isRunning = currentStatus === "RUNNING" || currentStatus === "START"
  const isStandby = currentStatus === "STANDBY"
  // NEW: status WARNING eksplisit -- dipakai saat ada gangguan sistem/koneksi
  // (mis. AGV timeout tidak sampai tujuan), BUKAN sekadar hasil NG produk biasa.
  const isWarning = currentStatus === "WARNING"
  const isIdle = currentStatus === "IDLE" || currentStatus === "UNKNOWN"

  const statusBg = isRunning
    ? "bg-green-100 text-green-700 border border-green-200"
    : isStandby
    ? "bg-orange-100 text-orange-700 border border-orange-200"
    // NEW: warna khusus WARNING (amber/kuning tegas), dibedakan dari Standby (oranye)
    // dan dari NG biasa (merah) supaya jelas ini soal sistem, bukan kualitas produk.
    : isWarning
    ? "bg-yellow-100 text-yellow-800 border border-yellow-300"
    : isIdle
    ? "bg-gray-100 text-gray-600 border border-gray-200"
    : "bg-red-100 text-red-700 border border-red-200"

  const dotColor = isRunning 
    ? "bg-green-500" 
    : isStandby 
    ? "bg-orange-500" 
    : isWarning
    ? "bg-yellow-500"
    : isIdle 
    ? "bg-gray-400" 
    : "bg-red-500"

  const dotAnimation = isRunning 
    ? "animate-pulse" 
    : isStandby 
    ? "animate-bounce" 
    // NEW: dot warning ikut berkedip biar menarik perhatian
    : isWarning
    ? "animate-pulse"
    : ""

  return (
    <div className={`bg-white border rounded-xl p-5 hover:shadow-md transition-shadow ${
      isWarning ? "border-yellow-300" : "border-outline-variant"
    }`}>
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm font-bold text-primary uppercase tracking-wide">{name}</h3>
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${statusBg}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${dotColor} ${dotAnimation}`} />
          {currentStatus}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-surface-container-low rounded-lg p-3 text-center">
          <p className="text-[10px] uppercase tracking-wider text-on-surface-variant mb-1">Cycle</p>
          <p className="text-base font-semibold text-primary font-mono">{cycle ?? "—"}</p>
        </div>
        <div className="bg-surface-container-low rounded-lg p-3 text-center">
          <p className="text-[10px] uppercase tracking-wider text-on-surface-variant mb-1">OK</p>
          <p className="text-base font-semibold text-secondary font-mono">{ok ?? 0}</p>
        </div>
        <div className="bg-surface-container-low rounded-lg p-3 text-center">
          <p className="text-[10px] uppercase tracking-wider text-on-surface-variant mb-1">NG</p>
          <p className="text-base font-semibold text-error font-mono">{ng ?? 0}</p>
        </div>
      </div>

      {/* NEW: tampilkan pesan detail masalah kalau statusnya WARNING */}
      {isWarning && warningMessage && (
        <p className="mt-3 text-[11px] text-yellow-800 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2 leading-snug">
          ⚠️ {warningMessage}
        </p>
      )}
    </div>
  )
}