import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { FileText, Plus, AlertCircle, Clock, CheckCircle2, XCircle, Search } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";
import { api, type LeakageReport } from "../lib/api";

const BUILDINGS = ["A1", "A4", "A5", "A6", "A10", "A11", "A12", "A13", "B1", "B2"];

const getStatusBadge = (status: LeakageReport["status"]) => {
  switch (status) {
    case "pending":
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200">
          <Clock className="h-3 w-3" /> Pending
        </span>
      );
    case "investigating":
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200">
          <Search className="h-3 w-3" /> Investigating
        </span>
      );
    case "resolved":
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200">
          <CheckCircle2 className="h-3 w-3" /> Resolved
        </span>
      );
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-red-100 text-red-800 border border-red-200">
          <XCircle className="h-3 w-3" /> Rejected
        </span>
      );
    default:
      return null;
  }
};

export function ReportsAnalysis() {
  const [reports, setReports] = useState<LeakageReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  const [form, setForm] = useState({
    building: "",
    location: "",
    description: "",
  });

  const loadReports = async () => {
    try {
      setLoading(true);
      const data = await api.reports();
      setReports(data);
      setError("");
    } catch (err) {
      setError("Failed to load reports from database.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadReports();
  }, []);

  const handleSubmit = async () => {
    if (!form.building || !form.location.trim() || !form.description.trim()) {
      setFormError("Please fill out all fields.");
      return;
    }

    setSubmitting(true);
    setFormError("");

    try {
      await api.createReport({
        building: form.building,
        location: form.location.trim(),
        description: form.description.trim(),
        reportedBy: "Campus Resident",
      });

      setForm({ building: "", location: "", description: "" });
      setOpen(false);
      await loadReports();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to submit report.");
    } finally {
      setSubmitting(false);
    }
  };

  const totalCount = reports.length;
  const pendingCount = reports.filter((r) => r.status === "pending").length;
  const investigatingCount = reports.filter((r) => r.status === "investigating").length;
  const resolvedCount = reports.filter((r) => r.status === "resolved").length;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Reports & Analysis</h1>
          <p className="text-sm text-muted-foreground">
            Submit and track physical pipeline leakage reports across university buildings
          </p>
        </div>

        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button className="bg-blue-600 hover:bg-blue-700 text-white">
              <Plus className="mr-2 h-4 w-4" /> New Leakage Report
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[480px]">
            <DialogHeader>
              <DialogTitle>Submit Leakage Report</DialogTitle>
              <DialogDescription>
                Report an observed water leak to facilities management for investigation.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-2">
              {formError && (
                <div className="rounded-lg bg-red-50 border border-red-200 p-3 text-xs text-red-700">
                  {formError}
                </div>
              )}

              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-700">Building Identifier</label>
                <Select
                  value={form.building}
                  onValueChange={(val) => setForm({ ...form, building: val })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select building (e.g. A1, B2)" />
                  </SelectTrigger>
                  <SelectContent>
                    {BUILDINGS.map((b) => (
                      <SelectItem key={b} value={b}>
                        Building {b}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-700">Specific Location</label>
                <Input
                  placeholder="e.g. 2nd Floor East Washroom, Pipe Valve 3"
                  value={form.location}
                  onChange={(e) => setForm({ ...form, location: e.target.value })}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-700">Leakage Description</label>
                <Textarea
                  placeholder="Describe the severity, water volume, or pipe condition..."
                  rows={4}
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </div>

              <Button
                className="w-full bg-blue-600 hover:bg-blue-700 text-white"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? "Submitting..." : "Submit Report to SQLite"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Summary Stat Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Total Reports</CardTitle>
            <FileText className="h-4 w-4 text-slate-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{loading ? "--" : totalCount}</div>
            <p className="text-xs text-muted-foreground mt-1">Recorded in SQLite database</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Pending</CardTitle>
            <Clock className="h-4 w-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-600">{loading ? "--" : pendingCount}</div>
            <p className="text-xs text-muted-foreground mt-1">Awaiting investigation</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Investigating</CardTitle>
            <Search className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-600">
              {loading ? "--" : investigatingCount}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Under active inspection</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Resolved</CardTitle>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-600">
              {loading ? "--" : resolvedCount}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Repairs completed</p>
          </CardContent>
        </Card>
      </div>

      {/* Reports List */}
      <Card>
        <CardHeader>
          <CardTitle>Submitted Leakage Reports</CardTitle>
          <CardDescription>
            Historical reports loaded from SQLite database with status tracking
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Loading leakage reports...
            </div>
          ) : reports.length === 0 ? (
            <div className="py-12 text-center text-muted-foreground">
              <AlertCircle className="h-8 w-8 mx-auto mb-2 text-slate-400" />
              <p className="text-sm font-medium">No leakage reports found.</p>
              <p className="text-xs text-muted-foreground">
                Click &quot;New Leakage Report&quot; above to submit an incident.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {reports.map((report) => (
                <div
                  key={report.dbId}
                  className="p-4 rounded-lg border border-slate-200 bg-white hover:border-slate-300 transition-colors shadow-2xs space-y-2"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-bold text-blue-700">
                        {report.id}
                      </span>
                      <span className="text-sm font-semibold text-slate-800">
                        • Building {report.building}
                      </span>
                    </div>
                    <div>{getStatusBadge(report.status)}</div>
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
    </div>
  );
}
