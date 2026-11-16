// App shell: router, login gate, sidebar navigation and the list of pages.
import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate, NavLink, useLocation } from "react-router";
import {
  Activity, AlertTriangle, BarChart3, Bell, CloudRain, Droplet, FileText, Gauge, GitBranch, History, LogOut, Menu, Radio, Shield, User, X,
} from "lucide-react";
import { Toaster } from "./components/ui/sonner";
import { Button } from "./components/ui/button";
import { AuthProvider, useAuth } from "./lib/auth";
import { LoginPage } from "./components/LoginPage";
import { ChangePassword } from "./components/ChangePassword";
import { OverviewPage } from "./components/OverviewPage";
import { UsageSettingsPage } from "./components/usage/UsageSettingsPage";
import { DevicesPage } from "./components/DevicesPage";
import { NodesPage } from "./components/nodes/NodesPage";
import { CalibrationWizard } from "./components/nodes/CalibrationWizard";
import { PipelineRoute, PipelinesPage } from "./components/pipeline/PipelinesPage";
import { AnalyticsPage } from "./components/AnalyticsPage";
import { AlertsPage } from "./components/AlertsPage";
import { WaterMonitoringDashboard } from "./components/WaterMonitoringDashboard";
import { LeakDetectionDashboard } from "./components/LeakDetectionDashboard";
import { RainwaterHarvesting } from "./components/RainwaterHarvesting";
import { ReportsAnalysis } from "./components/ReportsAnalysis";
import { UserProfile } from "./components/UserProfile";
import { AdminDashboard } from "./components/AdminDashboard";
import { Loading } from "./components/common";
import { StatusStrip } from "./components/StatusStrip";

/** Route guard. The server enforces roles on every API call; this only decides what the UI shows. */
function RequireRole({ role, children }: { role?: "admin"; children: ReactNode }) {
  const { user } = useAuth();
  if (role && user?.role !== role) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function Shell() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const admin = user?.role === "admin";
  useEffect(() => setOpen(false), [location.pathname]);

  const nav = [
    { name: "Overview", path: "/", icon: Activity },
    { name: "Nodes", path: "/nodes", icon: Radio },
    { name: "Pipeline", path: "/pipelines", icon: GitBranch },
    { name: "Water Monitoring", path: "/water-monitoring", icon: Gauge },
    { name: "Leak Detection", path: "/leak-detection", icon: AlertTriangle },
    { name: "Alerts", path: "/alerts", icon: Bell },
    { name: "Analytics", path: "/analytics", icon: BarChart3 },
    { name: "Rainwater System", path: "/rainwater", icon: CloudRain },
    ...(admin ? [{ name: "Admin", path: "/admin", icon: Shield }] : [{ name: "Reports", path: "/reports", icon: FileText }]),
    { name: "Profile", path: "/profile", icon: User },
    { name: "Legacy (v1)", path: "/legacy", icon: History },
  ];

  const sidebar = (
    <div className="h-full flex flex-col bg-sidebar border-r border-sidebar-border">
      <div className="p-5 border-b border-sidebar-border flex items-center gap-3">
        <div className="h-10 w-10 rounded-full bg-blue-600 flex items-center justify-center shrink-0 shadow-sm"><Droplet className="text-white h-5 w-5" /></div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold leading-tight text-sidebar-foreground">Aqua Guard</h2>
          <p className="text-xs text-muted-foreground">Water Conservation System</p>
        </div>
      </div>
      <nav className="flex-1 p-3 overflow-y-auto" aria-label="Main">
        <ul className="space-y-1">
          {nav.map(({ name, path, icon: Icon }) => (
            <li key={path}>
              <NavLink to={path} end={path === "/"}
                className={({ isActive }) =>
                  `w-full flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    isActive ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold shadow-xs"
                             : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"}`}>
                <Icon className="h-4 w-4 shrink-0" />{name}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="p-4 border-t border-sidebar-border">
        <p className="text-xs text-muted-foreground">Signed in as</p>
        <p className="text-xs font-medium truncate" title={user?.email}>{user?.email}</p>
        <span className="inline-block mt-1 mb-3 text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-blue-100 text-blue-800">{admin ? "Admin" : "User"}</span>
        <Button variant="outline" className="w-full justify-center text-sm" onClick={logout}><LogOut className="mr-2 h-4 w-4" />Logout</Button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen flex bg-background text-foreground">
      <aside className="hidden lg:block w-64 shrink-0 sticky top-0 h-screen">{sidebar}</aside>

      {open && (
        <div className="lg:hidden fixed inset-0 z-40 flex" role="dialog" aria-modal="true">
          <div className="w-72 max-w-[85vw] h-full shadow-xl">{sidebar}</div>
          <button className="flex-1 bg-black/40" aria-label="Close menu" onClick={() => setOpen(false)} />
        </div>
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="lg:hidden sticky top-0 z-30 flex items-center gap-3 border-b bg-background/95 backdrop-blur px-4 h-14">
          <button onClick={() => setOpen((o) => !o)} aria-label="Toggle menu" className="p-1.5 rounded-md hover:bg-muted">
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <Droplet className="h-5 w-5 text-blue-600" /><span className="font-semibold">Aqua Guard</span>
        </header>
        <StatusStrip />
        <main className="flex-1 overflow-x-hidden">
          {/* data pages: up to 1400 px; the pipeline editor uses the whole width and viewport height */}
          <div className={/^\/pipelines\/\d+/.test(location.pathname) ? "w-full p-2 sm:p-3" : "mx-auto w-full max-w-[1400px] p-4 sm:p-6"} data-testid="page-container">
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/water-monitoring" element={<WaterMonitoringDashboard />} />
              <Route path="/leak-detection" element={<LeakDetectionDashboard />} />
              <Route path="/rainwater" element={<RainwaterHarvesting />} />
              <Route path="/nodes" element={<NodesPage />} />
              <Route path="/nodes/calibrate" element={<CalibrationWizard />} />
              <Route path="/pipelines" element={<PipelinesPage />} />
              <Route path="/pipelines/:id" element={<PipelineRoute />} />
              <Route path="/legacy" element={<DevicesPage />} />
              <Route path="/devices" element={<Navigate to="/legacy" replace />} />
              <Route path="/analytics" element={<AnalyticsPage />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/usage-settings" element={<UsageSettingsPage />} />
              <Route path="/reports" element={<ReportsAnalysis />} />
              <Route path="/admin" element={<RequireRole role="admin"><AdminDashboard /></RequireRole>} />
              <Route path="/profile" element={<UserProfile isAdmin={admin} userEmail={user?.email ?? ""} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}

function Gate() {
  const { status, mustChangePassword } = useAuth();
  if (status === "loading") return <div className="min-h-screen flex items-center justify-center"><Loading label="Restoring session…" /></div>;
  if (status === "anonymous") return <LoginPage />;
  if (mustChangePassword) return <ChangePassword forced />;
  return <Shell />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Gate />
        <Toaster />
      </AuthProvider>
    </BrowserRouter>
  );
}
