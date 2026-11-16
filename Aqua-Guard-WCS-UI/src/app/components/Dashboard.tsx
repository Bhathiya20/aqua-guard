// Legacy dashboard container.
import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Droplets, Container, AlertTriangle, Radio, CheckCircle, XCircle, Info, Clock, ArrowRightLeft } from "lucide-react";
import { Progress } from "./ui/progress";
import { api, type LiveData } from "../lib/api";

const isFresh = (ageSeconds: number | null | undefined): boolean => {
  return ageSeconds !== null && ageSeconds !== undefined && ageSeconds < 15;
};

const formatAge = (ageSeconds: number | null | undefined, timestamp?: string): string => {
  if (ageSeconds === null || ageSeconds === undefined) return "No data recorded";
  if (ageSeconds === 0) return "Just now";
  if (ageSeconds === 1) return "1 second ago";
  if (ageSeconds < 60) return `${ageSeconds} seconds ago`;
  const mins = Math.floor(ageSeconds / 60);
  return `${mins} min${mins > 1 ? "s" : ""} ago (${timestamp || ""})`;
};

export function Dashboard() {
  const [live, setLive] = useState<LiveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let isMounted = true;

    const fetchLive = async () => {
      try {
        const data = await api.live();
        if (isMounted) {
          setLive(data);
          setError("");
          setLoading(false);
        }
      } catch (err) {
        if (isMounted) {
          setError("Backend API unavailable. Ensure Flask server is running at http://127.0.0.1:5000");
          setLoading(false);
        }
      }
    };

    fetchLive();
    const interval = setInterval(fetchLive, 2000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const n1 = live?.node1;
  const n2 = live?.node2;
  const rw = live?.rainwater;

  const n1Active = isFresh(n1?.ageSeconds);
  const n2Active = isFresh(n2?.ageSeconds);
  const bothActive = n1Active && n2Active;

  const n1Flow = n1?.flowRate ?? null;
  const n2Flow = n2?.flowRate ?? null;

  const flowDiff = bothActive && n1Flow !== null && n2Flow !== null
    ? Math.abs(n1Flow - n2Flow)
    : null;

  // Prototype rule: flow difference > 1.0 L/min indicates potential leak
  const isLeak = bothActive && flowDiff !== null && flowDiff > 1.0;

  // Ultrasonic sensor distance check
  const ultrasonicValid = rw !== null && rw !== undefined && rw.distance >= 0;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Water Usage & Leakage Detection</h1>
          <p className="text-sm text-muted-foreground">
            Real-time monitoring from ESP-NOW, MQTT and SQLite
          </p>
        </div>
        {live?.gateway && (
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-100 text-xs text-slate-700 border border-slate-200">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            Gateway: <span className="font-semibold">{live.gateway.status}</span>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 text-red-500 mt-0.5" />
          <div>
            <p className="font-semibold">Connection Notice</p>
            <p>{error}</p>
          </div>
        </div>
      )}

      {/* Top 3 Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Total Water Consumption */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Authoritative Consumption</CardTitle>
            <Droplets className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : n1?.totalLiters !== undefined ? (
                `${n1.totalLiters.toFixed(3)} L`
              ) : (
                "--"
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Upstream Node 1 volume. Node 2:{" "}
              {n2?.totalLiters !== undefined ? `${n2.totalLiters.toFixed(3)} L` : "--"}
            </p>
          </CardContent>
        </Card>

        {/* Water Tank Level */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Water Tank Level</CardTitle>
            <Container className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : rw && ultrasonicValid ? (
                `${rw.waterLevel.toFixed(1)}%`
              ) : rw && !ultrasonicValid ? (
                <span className="text-amber-600 text-lg">Sensor unavailable</span>
              ) : (
                "--"
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {rw && ultrasonicValid
                ? `Distance: ${rw.distance.toFixed(2)} cm`
                : rw && !ultrasonicValid
                ? "Ultrasonic Sensor Error"
                : "Waiting for rainwater node..."}
            </p>
          </CardContent>
        </Card>

        {/* Leakage Status */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Leakage Status</CardTitle>
            <AlertTriangle
              className={`h-4 w-4 ${
                isLeak ? "text-red-500" : bothActive ? "text-emerald-500" : "text-amber-500"
              }`}
            />
          </CardHeader>
          <CardContent>
            <div
              className={`text-2xl font-bold ${
                isLeak
                  ? "text-red-600"
                  : bothActive
                  ? "text-emerald-600"
                  : "text-amber-600"
              }`}
            >
              {loading
                ? "--"
                : !bothActive
                ? "Insufficient live data"
                : isLeak
                ? "Possible Leak"
                : "Normal"}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {flowDiff !== null
                ? `Flow Difference: ${flowDiff.toFixed(3)} L/min`
                : "Requires both nodes active"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Dedicated Flow Node Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Flow Node 1 */}
        <Card className="border-t-4 border-t-blue-500">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Radio className={`h-4 w-4 ${n1Active ? "text-emerald-600" : "text-red-500"}`} />
                <CardTitle className="text-base font-semibold">Flow Node 1 (Upstream)</CardTitle>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                  n1Active
                    ? "bg-emerald-100 text-emerald-800"
                    : "bg-red-100 text-red-800"
                }`}
              >
                {n1Active ? (
                  <>
                    <CheckCircle className="h-3 w-3" /> ACTIVE
                  </>
                ) : (
                  <>
                    <XCircle className="h-3 w-3" /> OFFLINE / STALE
                  </>
                )}
              </span>
            </div>
            <CardDescription className="text-xs">
              YF-S201 Flow Sensor • ESP-01S Node 1
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4 p-4 rounded-lg bg-slate-50 border border-slate-100">
              <div>
                <p className="text-xs text-muted-foreground">Flow Rate</p>
                <p className="text-2xl font-bold tracking-tight text-slate-900">
                  {n1?.flowRate !== undefined ? `${n1.flowRate.toFixed(3)}` : "--"}
                  <span className="text-xs font-normal text-muted-foreground ml-1">L/min</span>
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Accumulated Volume</p>
                <p className="text-2xl font-bold tracking-tight text-slate-900">
                  {n1?.totalLiters !== undefined ? `${n1.totalLiters.toFixed(3)}` : "--"}
                  <span className="text-xs font-normal text-muted-foreground ml-1">L</span>
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between text-xs text-muted-foreground pt-1 border-t border-slate-100">
              <div className="flex items-center gap-1">
                <Clock className="h-3.5 w-3.5 text-slate-400" />
                <span>Last update: {formatAge(n1?.ageSeconds, n1?.receivedAt)}</span>
              </div>
              <div>Uptime: {n1?.uptime !== undefined ? `${n1.uptime}s` : "--"}</div>
            </div>
          </CardContent>
        </Card>

        {/* Flow Node 2 */}
        <Card className="border-t-4 border-t-indigo-500">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Radio className={`h-4 w-4 ${n2Active ? "text-emerald-600" : "text-red-500"}`} />
                <CardTitle className="text-base font-semibold">Flow Node 2 (Downstream)</CardTitle>
              </div>
              <span
                className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                  n2Active
                    ? "bg-emerald-100 text-emerald-800"
                    : "bg-red-100 text-red-800"
                }`}
              >
                {n2Active ? (
                  <>
                    <CheckCircle className="h-3 w-3" /> ACTIVE
                  </>
                ) : (
                  <>
                    <XCircle className="h-3 w-3" /> OFFLINE / STALE
                  </>
                )}
              </span>
            </div>
            <CardDescription className="text-xs">
              YF-S201 Flow Sensor • ESP-01S Node 2
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4 p-4 rounded-lg bg-slate-50 border border-slate-100">
              <div>
                <p className="text-xs text-muted-foreground">Flow Rate</p>
                <p className="text-2xl font-bold tracking-tight text-slate-900">
                  {n2?.flowRate !== undefined ? `${n2.flowRate.toFixed(3)}` : "--"}
                  <span className="text-xs font-normal text-muted-foreground ml-1">L/min</span>
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Accumulated Volume</p>
                <p className="text-2xl font-bold tracking-tight text-slate-900">
                  {n2?.totalLiters !== undefined ? `${n2.totalLiters.toFixed(3)}` : "--"}
                  <span className="text-xs font-normal text-muted-foreground ml-1">L</span>
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between text-xs text-muted-foreground pt-1 border-t border-slate-100">
              <div className="flex items-center gap-1">
                <Clock className="h-3.5 w-3.5 text-slate-400" />
                <span>Last update: {formatAge(n2?.ageSeconds, n2?.receivedAt)}</span>
              </div>
              <div>Uptime: {n2?.uptime !== undefined ? `${n2.uptime}s` : "--"}</div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Dedicated Leakage Detection Panel */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ArrowRightLeft className="h-5 w-5 text-blue-600" />
            <CardTitle>Pipeline Leakage Detection Analysis</CardTitle>
          </div>
          <CardDescription>
            Continuous upstream (Node 1) vs downstream (Node 2) differential flow comparison
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            className={`p-4 rounded-lg border transition-colors ${
              isLeak
                ? "bg-red-50/80 border-red-200 text-red-900"
                : bothActive
                ? "bg-emerald-50/80 border-emerald-200 text-emerald-900"
                : "bg-slate-50 border-slate-200 text-slate-800"
            }`}
          >
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <div>
                <p className="font-semibold text-base">
                  {!bothActive
                    ? "Status: Insufficient live sensor data for leakage analysis"
                    : isLeak
                    ? "Alert: Abnormal flow difference detected (Potential Leak)"
                    : "Status: Pipeline flow differential is within normal limits"}
                </p>
                <p className="text-xs opacity-80 mt-0.5">
                  Node 1: {n1?.flowRate !== undefined ? `${n1.flowRate.toFixed(3)} L/min` : "--"} •
                  Node 2: {n2?.flowRate !== undefined ? `${n2.flowRate.toFixed(3)} L/min` : "--"}
                </p>
              </div>
              <div className="text-right">
                <span className="text-xs uppercase font-bold tracking-wider opacity-70 block">
                  Absolute Difference
                </span>
                <span className="text-xl font-bold font-mono">
                  {flowDiff !== null ? `${flowDiff.toFixed(3)} L/min` : "--"}
                </span>
              </div>
            </div>

            {/* Differential Flow Visual Indicator */}
            <div className="mt-4 space-y-1.5">
              <div className="flex justify-between text-xs opacity-75">
                <span>0.0 L/min</span>
                <span>Threshold: 1.0 L/min</span>
                <span>2.0+ L/min</span>
              </div>
              <Progress
                value={flowDiff !== null ? Math.min((flowDiff / 2.0) * 100, 100) : 0}
                className="h-2.5"
              />
            </div>

            <div className="mt-4 flex items-start gap-2 text-xs opacity-75">
              <Info className="h-4 w-4 shrink-0 mt-0.5" />
              <span>
                Prototype leakage threshold. Final threshold will be determined after sensor calibration.
              </span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
