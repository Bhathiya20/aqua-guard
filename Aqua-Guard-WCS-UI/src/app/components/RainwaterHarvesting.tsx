// Rainwater page: tank level, rain, turbidity and valve state (valve stays dry-run unless enabled).
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Container, Activity, CloudRain, Radio, CheckCircle, XCircle, Gauge, Waves, Info, Clock, ShieldCheck, RefreshCw } from "lucide-react";
import { Progress } from "./ui/progress";
import { fmtDuration, fmtTime, usePolling } from "../lib/usePolling";
import { ConnectionBanner, EmptyState, PageHeader, Pill, valveStateName } from "./common";
import { api, type RainwaterReading, type RainwaterSummaryData } from "../lib/api";

const formatAge = (ageSeconds: number | null | undefined, timestamp?: string): string => {
  if (ageSeconds === null || ageSeconds === undefined) return "No data recorded";
  if (ageSeconds === 0) return "Just now";
  if (ageSeconds === 1) return "1 second ago";
  if (ageSeconds < 60) return `${ageSeconds} seconds ago`;
  const mins = Math.floor(ageSeconds / 60);
  return `${fmtDuration(ageSeconds)} ago (${fmtTime(timestamp)})`;
};

export function RainwaterHarvesting() {
  const poll = usePolling(async () => {
    const [liveData, summaryData] = await Promise.all([api.getRainwaterLive(), api.getRainwaterSummary()]);
    return { liveData, summaryData };
  }, 2000);
  const histPoll = usePolling(() => api.getRainwaterHistory(15), 6000);

  const data: RainwaterReading | null = poll.data?.liveData.rainwater ?? null;
  const summary: RainwaterSummaryData | null = poll.data?.summaryData ?? null;
  const history: RainwaterReading[] = histPoll.data ?? [];
  const loading = poll.loading && !poll.data;
  const error = poll.error;
  const fetchHistory = () => histPoll.refresh();

  const active = data?.active ?? false;
  // An invalid ultrasonic reading is "sensor error", never 0 %.
  const ultrasonicValid = !!data && data.levelValid && data.waterLevel !== null && data.distance !== null;
  const tankFillPercent = ultrasonicValid ? Math.max(0, Math.min(100, data!.waterLevel as number)) : 0;
  const oor = !!data && !ultrasonicValid && !!data.outOfRange;           // nothing in front of the sensor: not a failure
  const usErr = oor ? "Out of range" : "Sensor error";
  const turbValid = !!data && data.turbidityValid;
  const qualityLabel = !data ? "--" : !turbValid ? "SENSOR ERROR" : data.waterClear ? "CLEAR" : "TURBID";

  return (
    <div className="space-y-5">
      <PageHeader icon={<CloudRain className="h-6 w-6 text-blue-600" />} title="Rainwater Harvesting" subtitle="Rain, storage tank, water quality and the collection valve."
        right={!data ? <Pill tone="gray">No data</Pill> : active ? <Pill tone="green">Rain node online</Pill> : <Pill tone="red">Rain node offline</Pill>} />

      <ConnectionBanner error={error} stale={poll.stale} onRetry={poll.refresh} />
      {!loading && !data && (
        <Card>
          <CardContent>
            <EmptyState title="No data" action={{ to: "/nodes", label: "Go to Nodes" }}>
              The rainwater node has not sent any reading yet. Pair it from the Nodes page (type rain) and power it; the page fills in as soon as the first reading arrives.
            </EmptyState>
          </CardContent>
        </Card>
      )}
      {data && !active && (
        <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          The rainwater node is <b>offline</b>. The values below are its last readings ({formatAge(data.ageSeconds, data.receivedAt)}).
        </div>
      )}
      {data?.dryRun && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <b>Dry-run mode:</b> the valve output is disabled in firmware. The state shown is what the controller <i>would</i> command; the physical valve is not energised.
        </div>
      )}

      {data && (<>
      {/* Top 5 Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {/* Card 1: Rain Detection */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Rain Detection</CardTitle>
            <CloudRain className={`h-4 w-4 ${data?.rain ? "text-blue-500" : "text-slate-400"}`} />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : data ? (
                data.rain ? (
                  <span className="text-blue-600">RAINING</span>
                ) : (
                  <span className="text-slate-700">NO RAIN</span>
                )
              ) : (
                "--"
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.rain ? "Precipitation active" : "Dry weather conditions"}
            </p>
          </CardContent>
        </Card>

        {/* Card 2: Tank Level */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Tank Level</CardTitle>
            <Container className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : data && ultrasonicValid ? (
                `${tankFillPercent.toFixed(1)}%`
              ) : data && !ultrasonicValid ? (
                <span className={`${oor ? "text-slate-600" : "text-amber-600"} text-lg`}>{usErr}</span>
              ) : (
                "--"
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {data && ultrasonicValid
                ? `Distance: ${(data.distance as number).toFixed(2)} cm`
                : data && !ultrasonicValid
                ? (oor ? "No tank in front of the sensor" : "Ultrasonic sensor error")
                : "Waiting for readings..."}
            </p>
          </CardContent>
        </Card>

        {/* Card 3: Water Quality */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Water Quality</CardTitle>
            <Activity className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : data ? (
                !turbValid ? (
                  <span className="text-amber-600 font-semibold text-lg">SENSOR ERROR</span>
                ) : data.waterClear ? (
                  <span className="text-emerald-600 font-semibold">CLEAR</span>
                ) : (
                  <span className="text-amber-600 font-semibold">TURBID</span>
                )
              ) : (
                "--"
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.turbidity !== undefined && data?.turbidity !== null ? `${data.turbidity} ADC` : "No ADC reading"}
            </p>
          </CardContent>
        </Card>

        {/* Card 4: Valve Status */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Valve Status</CardTitle>
            <span className="text-xs font-semibold text-slate-400">RELAY</span>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? (
                "--"
              ) : data?.valveOpen ? (
                <span className="text-blue-600">OPEN</span>
              ) : (
                <span className="text-slate-600">CLOSED</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {valveStateName(data?.valveState)}{data?.dryRun ? " · dry-run" : ""}
            </p>
          </CardContent>
        </Card>

        {/* Card 5: Rainwater Node */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-sm font-medium">Rainwater Node</CardTitle>
            <Radio className={`h-4 w-4 ${active ? "text-emerald-600" : "text-red-500"}`} />
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${active ? "text-emerald-600" : "text-red-600"}`}>
              {loading ? "--" : active ? "ACTIVE" : "OFFLINE"}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Rainwater ESP32 Node
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Rain Detection & Control Status Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Rainwater Control & Valve Actuation</CardTitle>
              <CardDescription>
                Live solenoid relay state and precipitation trigger
              </CardDescription>
            </div>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                active ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"
              }`}
            >
              {active ? <CheckCircle className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
              {active ? "ACTIVE" : "OFFLINE"}
            </span>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-lg bg-slate-50 border border-slate-100 text-center">
            <div>
              <p className="text-xs text-muted-foreground">Solenoid Valve</p>
              <p className="text-xl font-bold mt-1">
                {loading ? (
                  "--"
                ) : data?.valveOpen ? (
                  <span className="text-blue-600">OPEN</span>
                ) : (
                  <span className="text-slate-600">CLOSED</span>
                )}
              </p>
              <span className="text-[10px] text-muted-foreground block mt-0.5">
                {valveStateName(data?.valveState)}{data?.dryRun ? " · dry-run" : ""}
              </span>
            </div>

            <div>
              <p className="text-xs text-muted-foreground">Water Classification</p>
              <p className="text-xl font-bold mt-1">
                {loading ? (
                  "--"
                ) : !turbValid ? (
                  <span className="text-amber-600 text-base">SENSOR ERROR</span>
                ) : data?.waterClear ? (
                  <span className="text-emerald-600">CLEAR</span>
                ) : (
                  <span className="text-amber-600">TURBID</span>
                )}
              </p>
              <span className="text-[10px] text-muted-foreground block mt-0.5">
                ESP32 ADC threshold
              </span>
            </div>

            <div>
              <p className="text-xs text-muted-foreground">Ultrasonic Distance</p>
              <p className="text-xl font-bold mt-1 font-mono">
                {loading ? (
                  "--"
                ) : ultrasonicValid ? (
                  `${data?.distance?.toFixed(2)} cm`
                ) : (
                  <span className={`${oor ? "text-slate-600" : "text-amber-600"} text-base`}>{usErr}</span>
                )}
              </p>
              <span className="text-[10px] text-muted-foreground block mt-0.5">
                {ultrasonicValid ? "Valid range" : oor ? "Out of range (no tank in front)" : "Echo failure"}
              </span>
            </div>

            <div>
              <p className="text-xs text-muted-foreground">Subsystem Uptime</p>
              <p className="text-xl font-bold mt-1 font-mono">
                {loading ? "--" : fmtDuration(data?.uptime ?? 0)}
              </p>
              <span className="text-[10px] text-muted-foreground block mt-0.5">
                ESP32 operating time
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground pt-3 mt-3 border-t border-slate-100">
            <div className="flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-slate-400" />
              <span>Last update: {formatAge(data?.ageSeconds, data?.receivedAt)}</span>
            </div>
            <div className="flex items-center gap-1">
              <ShieldCheck className="h-3.5 w-3.5 text-blue-500" />
              <span>Rainwater ESP32 → ESP-NOW → Main Gateway → MQTT → SQLite</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Turbidity & Water Quality Panel */}
      <Card>
        <CardHeader>
          <CardTitle>Turbidity & Water Quality Metrics</CardTitle>
          <CardDescription>
            Raw analog-to-digital converter telemetry and classification
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-6 md:grid-cols-2">
            <div className="p-4 border rounded-lg bg-slate-50/50">
              <div className="flex justify-between items-center">
                <span className="text-sm font-medium">Turbidity Sensor</span>
                <span className="text-lg font-bold font-mono">
                  {data?.turbidity !== undefined && data?.turbidity !== null ? `${data.turbidity} ADC` : "--"}
                </span>
              </div>
              <Progress
                value={data?.turbidity !== undefined && data?.turbidity !== null ? Math.min(100, (data.turbidity / 4095) * 100) : 0}
                className="h-2.5 mt-3"
              />
              <div className="flex justify-between text-[11px] text-muted-foreground mt-1.5">
                <span>0 ADC (High Turbidity)</span>
                <span>Threshold: ~2500 ADC</span>
                <span>4095 ADC (Clear)</span>
              </div>
              <p className="text-xs text-muted-foreground mt-3 flex items-start gap-1.5">
                <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                Raw ADC measurement. NTU conversion requires turbidity sensor calibration.
              </p>
            </div>

            <div className="p-4 border rounded-lg bg-slate-50/50">
              <div className="flex justify-between items-center">
                <span className="text-sm font-medium">Classification</span>
                <span
                  className={`text-lg font-bold ${
                    data?.waterClear ? "text-emerald-600" : "text-amber-600"
                  }`}
                >
                  {qualityLabel}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-3 flex items-center gap-1.5">
                <Gauge className="h-4 w-4 text-blue-500" />
                Calculated on rainwater ESP32 node via calibrated ADC threshold before transmission.
              </p>
              <div className="mt-4 p-2.5 rounded bg-blue-50/70 border border-blue-100 text-[11px] text-blue-900">
                Notice: Physical hardware currently supplies Turbidity ADC and Water Clear status. pH,
                TDS, and Dissolved Oxygen are not fabricated.
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Storage Tank Visualization Panel */}
      <Card>
        <CardHeader>
          <CardTitle>Storage Tank</CardTitle>
          <CardDescription>
            Ultrasonic depth measurement and calculated water level percentage
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm font-medium">Main Storage Tank (30 cm Prototype)</span>
              <span className="text-2xl font-bold font-mono">
                {ultrasonicValid ? `${tankFillPercent.toFixed(1)}%` : oor ? "No tank in range" : "Water level unavailable"}
              </span>
            </div>
            <Progress value={tankFillPercent} className="h-5" />
          </div>

          <div className="grid gap-4 sm:grid-cols-3 pt-2">
            <div className="p-4 border rounded-lg bg-slate-50/50 flex flex-col justify-between">
              <div className="flex items-center gap-2 text-blue-600 mb-2">
                <Waves className="h-4 w-4" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                  Water Level
                </span>
              </div>
              <p className="text-xl font-bold">
                {ultrasonicValid ? `${tankFillPercent.toFixed(1)}%` : "Unavailable"}
              </p>
            </div>

            <div className="p-4 border rounded-lg bg-slate-50/50 flex flex-col justify-between">
              <div className="flex items-center gap-2 text-blue-600 mb-2">
                <Gauge className="h-4 w-4" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                  Sensor Distance
                </span>
              </div>
              <p className="text-xl font-bold font-mono">
                {ultrasonicValid ? `${data?.distance?.toFixed(2)} cm` : usErr}
              </p>
            </div>

            <div className="p-4 border rounded-lg bg-slate-50/50 flex flex-col justify-between">
              <div className="flex items-center gap-2 text-blue-600 mb-2">
                <CloudRain className="h-4 w-4" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                  Rain Status
                </span>
              </div>
              <p className="text-xl font-bold">
                {data ? (data.rain ? "Detected" : "Not Detected") : "--"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Historical Rainwater Telemetry Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Historical Rainwater Telemetry</CardTitle>
              <CardDescription>
                Stored samples from the rainwater ESP32 node
              </CardDescription>
            </div>
            <button
              onClick={fetchHistory}
              className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
              title="Refresh history"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No historical rainwater readings in database.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="bg-slate-50 text-slate-600 uppercase border-b border-slate-200">
                  <tr>
                    <th className="px-4 py-2.5">Record ID</th>
                    <th className="px-4 py-2.5">Rain</th>
                    <th className="px-4 py-2.5">Water Level (%)</th>
                    <th className="px-4 py-2.5">Distance</th>
                    <th className="px-4 py-2.5">Turbidity</th>
                    <th className="px-4 py-2.5">Water Clear</th>
                    <th className="px-4 py-2.5">Valve</th>
                    <th className="px-4 py-2.5">Timestamp</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {history.slice(-10).reverse().map((r) => (
                    <tr key={r.id} className="hover:bg-slate-50/50">
                      <td className="px-4 py-2 font-mono text-slate-500">#{r.id}</td>
                      <td className="px-4 py-2">
                        <span className={`font-semibold ${r.rain ? "text-blue-600" : "text-slate-500"}`}>
                          {r.rain ? "Raining" : "Dry"}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-mono">
                        {r.levelValid && r.waterLevel !== null ? `${r.waterLevel.toFixed(1)}%` : "Unavailable"}
                      </td>
                      <td className="px-4 py-2 font-mono">
                        {r.levelValid && r.distance !== null ? `${r.distance.toFixed(2)} cm` : <span className={r.outOfRange ? "text-slate-500" : "text-amber-600"}>{r.outOfRange ? "Out of range" : "Error"}</span>}
                      </td>
                      <td className="px-4 py-2 font-mono">{r.turbidity} ADC</td>
                      <td className="px-4 py-2">
                        <span className={r.waterClear ? "text-emerald-600 font-semibold" : "text-amber-600 font-semibold"}>
                          {!r.turbidityValid ? "ERROR" : r.waterClear ? "CLEAR" : "TURBID"}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-semibold">
                        {r.valveOpen ? <span className="text-blue-600">OPEN</span> : <span className="text-slate-500">CLOSED</span>}{r.dryRun ? <span className="ml-1 text-[10px] text-amber-600">(dry-run)</span> : null}
                      </td>
                      <td className="px-4 py-2 text-slate-500 whitespace-nowrap">{fmtTime(r.receivedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
      </>)}
    </div>
  );
}
