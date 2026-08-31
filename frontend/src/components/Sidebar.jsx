import { NavLink, useLocation, useNavigate } from "react-router-dom"
import logo from "../assets/logopolman.svg"
import { useAuth } from "../context/AuthContext"
import { LogOut, User as UserIcon, Settings } from "lucide-react"

const navItems = [
  { label: "Assembly Line", icon: "precision_manufacturing", to: "/" },
  { label: "Manufacturing", icon: "factory", to: "/mo" },
]

const footerItems = [
  { label: "Support", icon: "headset_mic", to: "#" },
  { label: "Logs", icon: "terminal", to: "/history" },
]

export default function Sidebar() {
  const location = useLocation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate("/login")
  }

  return (
    <aside className="bg-white text-blue-900 font-sans h-screen w-64 border-r border-slate-200 flex flex-col py-6 shrink-0 sticky top-0 z-40">
      {/* Header */}
      <div className="px-6 pb-6 border-b border-slate-200 mb-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0">
            <img src={logo} alt="Logo" />
          </div>
          <div>
            <h1 className="text-primary font-bold text-sm tracking-normal">Main Plant</h1>
            <p className="text-slate-500 text-[10px] tracking-wide uppercase">Operational Center</p>
          </div>
        </div>
        <div className="bg-surface-container-low text-primary py-2 px-3 rounded-lg flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-secondary animate-pulse-dot"></span>
          <span className="text-[10px] uppercase tracking-wider font-semibold">System Status: Active</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-4 space-y-1">
        {navItems.map(item => {
          const isActive = item.to !== "#" && location.pathname === item.to
          return (
            <NavLink
              key={item.label}
              to={item.to}
              className={`cursor-pointer select-none flex items-center gap-3 px-4 py-3 rounded-l text-sm font-semibold transition-all duration-200 ${isActive
                ? "bg-slate-100 text-primary border-r-4 border-primary"
                : "text-slate-500 hover:bg-slate-50 hover:text-primary"
                }`}
            >
              <span className="material-symbols-outlined text-[20px]"
                style={isActive ? { fontVariationSettings: "'FILL' 1" } : {}}>
                {item.icon}
              </span>
              <span>{item.label}</span>
            </NavLink>
          )
        })}

        {user?.role === 'admin' && (
          <NavLink
            to="/users"
            className={({ isActive }) => `cursor-pointer select-none flex items-center gap-3 px-4 py-3 rounded-l text-sm font-semibold transition-all duration-200 ${isActive
              ? "bg-slate-100 text-primary border-r-4 border-primary"
              : "text-slate-500 hover:bg-slate-50 hover:text-primary"
              }`}
          >
            <Settings className="w-5 h-5" />
            <span>User Management</span>
          </NavLink>
        )}
      </nav>

      {/* User & Footer */}
      <div className="px-4 mt-auto border-t border-slate-200 pt-4 space-y-1">
        <div className="flex items-center gap-3 px-4 py-3 mb-2 bg-surface-container-low rounded-xl border border-outline-variant">
          <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-on-primary">
            <UserIcon size={16} />
          </div>
          <div className="flex-1 overflow-hidden">
            <p className="text-xs font-bold text-primary truncate">{user?.username}</p>
            <p className="text-[10px] text-slate-500 uppercase font-semibold">{user?.role}</p>
          </div>
          <button 
            onClick={handleLogout}
            className="p-1.5 text-slate-400 hover:text-error hover:bg-error/10 rounded-lg transition-colors"
            title="Logout"
          >
            <LogOut size={16} />
          </button>
        </div>

        {footerItems.map(item => {
          const isActive = item.to !== "#" && location.pathname === item.to
          return (
            <NavLink
              key={item.label}
              to={item.to}
              className={`cursor-pointer select-none flex items-center gap-3 px-4 py-2.5 rounded-l text-sm font-semibold transition-all duration-200 ${isActive
                ? "bg-slate-100 text-primary border-r-4 border-primary"
                : "text-slate-500 hover:bg-slate-50 hover:text-primary"
                }`}
            >
              <span className="material-symbols-outlined text-[20px]">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          )
        })}
      </div>
    </aside>
  )
}
