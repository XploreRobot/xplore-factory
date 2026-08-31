import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom"
import Dashboard from "./pages/Dashboard"
import History from "./pages/History"
import Login from "./pages/Login"
import UserManagement from "./pages/UserManagement"
import Sidebar from "./components/Sidebar"
import TopBar from "./components/TopBar"
import { DarkModeProvider } from "./context/DarkModeContext"
import { AuthProvider, useAuth } from "./context/AuthContext"
import ManufacturingOrders from './pages/ManufacturingOrders'; // Sesuaikan lokasi foldernya

const ProtectedRoute = ({ children, adminOnly = false }) => {
  const { user, loading } = useAuth();
  
  if (loading) return null;
  
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (adminOnly && user.role !== 'admin') {
    return <Navigate to="/" replace />;
  }
  
  return children;
};

function AppContent() {
  const { user } = useAuth();

  return (
    <div className="flex h-full w-full bg-background text-on-background overflow-hidden">
      {/* Sidebar – only show if logged in */}
      {user && (
        <div className="hidden md:block shrink-0">
          <Sidebar />
        </div>
      )}

      {/* Right column: TopBar + scrollable page */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {user && <TopBar />}
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/login" element={!user ? <Login /> : <Navigate to="/" />} />
            <Route 
              path="/" 
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              } 
            />
            <Route 
              path="/history" 
              element={
                <ProtectedRoute>
                  <History />
                </ProtectedRoute>
              } 
            />
            <Route 
              path="/users" 
              element={
                <ProtectedRoute adminOnly={true}>
                  <UserManagement />
                </ProtectedRoute>
              } 
            />
            <Route path="/mo" element={<ManufacturingOrders />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <DarkModeProvider>
      <AuthProvider>
        <Router>
          <AppContent />
        </Router>
      </AuthProvider>
    </DarkModeProvider>
  )
}

export default App