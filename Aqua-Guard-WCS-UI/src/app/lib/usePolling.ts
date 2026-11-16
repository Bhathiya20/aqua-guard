// usePolling hook (refresh on an interval, keep the last good data, flag stale/error) plus time and duration formatters.
import { useCallback, useEffect, useRef, useState } from "react";

export interface PollState<T> {
  data: T | null;
  error: string;
  loading: boolean;        // true until the first response (or failure)
  stale: boolean;          // last refresh failed but older data is still shown
  lastOk: number | null;   // epoch ms of last successful refresh
  refresh: () => void;
}

/**
 * Polls `fn` every `intervalMs`. On failure it keeps the last data, flags it stale, and backs off
 * exponentially (up to 30 s) until the server is reachable again, then returns to the normal rate.
 * Pauses while the tab is hidden and refreshes immediately when it becomes visible again.
 */
export function usePolling<T>(fn: () => Promise<T>, intervalMs = 2000, deps: unknown[] = []): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lastOk, setLastOk] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const run = async () => {
      if (!alive) return;
      if (document.hidden) { timer = setTimeout(run, intervalMs); return; }
      try {
        const d = await fnRef.current();
        if (!alive) return;
        failures = 0;
        setData(d); setError(""); setLastOk(Date.now());
      } catch (e) {
        if (!alive) return;
        failures += 1;
        setError(e instanceof Error ? e.message : "Request failed");
      } finally {
        if (alive) setLoading(false);
      }
      if (!alive) return;
      const delay = failures ? Math.min(30000, intervalMs * 2 ** Math.min(failures, 5)) : intervalMs;
      timer = setTimeout(run, delay);
    };

    const onVisible = () => { if (!document.hidden) { if (timer) clearTimeout(timer); run(); } };
    document.addEventListener("visibilitychange", onVisible);
    run();
    return () => { alive = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, stale: !!error && data !== null, lastOk, refresh };
}

/** All times are shown in the site's local time zone, whatever the browser's zone is (the backend stores UTC). */
export const TZ = "Asia/Colombo";
const toDate = (ts: string | number): Date => (typeof ts === "number" ? new Date(ts * 1000) : new Date(ts.includes("T") ? ts : ts.replace(" ", "T") + "Z"));
export function fmtTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = toDate(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString("en-GB", { timeZone: TZ, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
/** "HH:mm" in local time (UTC string or epoch seconds). */
export function fmtClock(ts: string | number | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "—";
  const d = toDate(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleTimeString("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
}
/** "Mon 15" in local time. */
export function fmtDayShort(ts: string | number): string {
  const d = toDate(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleDateString("en-GB", { timeZone: TZ, weekday: "short", day: "numeric" });
}
/** "6583 s" -> "1 h 50 min"; seconds only below a minute. */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} s`;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d${h ? ` ${h} h` : ""}`;
  if (h) return m ? `${h} h ${m} min` : `${h} h`;
  return `${m} min`;
}

/** Seconds since a UTC 'YYYY-MM-DD HH:MM:SS' timestamp (null when there is none). */
export function ageSince(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T") + "Z");
  return isNaN(d.getTime()) ? null : Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
}

export function fmtAge(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "never";
  return `${fmtDuration(sec)} ago`;
}

export function fmtUptime(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${sec % 60}s`;
}
