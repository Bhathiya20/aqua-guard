import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Shield, Clock, Search, CheckCircle2, FileText, AlertCircle } from "lucide-react";
import { Button } from "./ui/button";
import { toast } from "sonner";
import { UserAdmin } from "./UserAdmin";
import { AdminModules } from "./AdminModules";
import { api, type LeakageReport } from "../lib/api";

export function AdminDashboard() {
  const [reports, setReports] = useState<LeakageReport[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const loadAll = async () => {
    try {
      setLoading(true);
      const reps = await api.getReports();
      setReports(reps);
      setError("");
    } catch {
      setError("Failed to load administrative data from database.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleStatusChange = async (dbId: number, newStatus: LeakageReport["status"]) => {
    try {
      setUpdatingId(dbId);
      await api.updateReportStatus(dbId, newStatus);
      const reps = await api.getReports();
      setReports(reps);
    } catch (err) {
      toast.error("Failed to update report status: " + (err instanceof Error ? err.message : "Unknown error"));
    } finally {
      setUpdatingId(null);
    }
  };

  const totalCount = reports.length;
  const pendingCount = reports.filter((r) => r.status === "pending").length;
  const investigatingCount = reports.filter((r) => r.status === "investigating").length;
  const resolvedCount = reports.filter((r) => r.status === "resolved").length;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Shield className="h-6 w-6 text-blue-600" />
            <h1 className="text-2xl font-bold tracking-tight">Admin Dashboard</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Centralized administration, module summaries, and physical maintenance triage
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <AdminModules />

      {/* Manual Reports Stats Summary */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Total Reports</CardTitle>
            <FileText className="h-4 w-4 text-slate-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{loading ? "--" : totalCount}</div>
            <p className="text-xs text-muted-foreground mt-1">Total recorded incidents</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Pending Reports</CardTitle>
            <Clock className="h-4 w-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-600">{loading ? "--" : pendingCount}</div>
            <p className="text-xs text-muted-foreground mt-1">Requires triage</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Investigating Reports</CardTitle>
            <Search className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-600">
              {loading ? "--" : investigatingCount}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Maintenance in progress</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Resolved Reports</CardTitle>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-600">
              {loading ? "--" : resolvedCount}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Closed incidents</p>
          </CardContent>
        </Card>
      </div>

      {/* Reports Management Table/List */}
      <Card>
        <CardHeader>
          <CardTitle>Manual Leakage Reports Management</CardTitle>
          <CardDescription>
            Update investigation statuses for campus facility reports (stored in SQLite)
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Loading incidents...
            </div>
          ) : reports.length === 0 ? (
            <div className="py-12 text-center text-muted-foreground">
              <AlertCircle className="h-8 w-8 mx-auto mb-2 text-slate-400" />
              <p className="text-sm font-medium">No leakage reports in database.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {reports.map((report) => (
                <div
                  key={report.dbId}
                  className="p-4 rounded-lg border border-slate-200 bg-white hover:border-slate-300 transition-colors shadow-2xs space-y-3"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-bold text-blue-700">
                        {report.id}
                      </span>
                      <span className="text-sm font-semibold text-slate-800">
                        • Building {report.building}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Status:</span>
                      <Select
                        value={report.status}
                        onValueChange={(val) =>
                          handleStatusChange(report.dbId, val as LeakageReport["status"])
                        }
                        disabled={updatingId === report.dbId}
                      >
                        <SelectTrigger className="w-[160px] h-8 text-xs font-semibold">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="pending">
                            <span className="text-amber-700 font-semibold">Pending</span>
                          </SelectItem>
                          <SelectItem value="investigating">
                            <span className="text-blue-700 font-semibold">Investigating</span>
                          </SelectItem>
                          <SelectItem value="resolved">
                            <span className="text-emerald-700 font-semibold">Resolved</span>
                          </SelectItem>
                          <SelectItem value="rejected">
                            <span className="text-red-700 font-semibold">Rejected</span>
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  <p className="text-sm font-medium text-slate-700">{report.location}</p>
                  <p className="text-xs text-slate-600 leading-relaxed">{report.description}</p>

                  <div className="flex items-center justify-between text-[11px] text-muted-foreground pt-2 border-t border-slate-100">
                    <span>Reported by: {report.reportedBy}</span>
                    <span>Date: {report.date}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <UserAdmin />
    </div>
  );
}
