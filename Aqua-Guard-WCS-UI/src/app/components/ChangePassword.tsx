import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";

/** Shown full-screen when the account is flagged must-change-password (first login / admin reset). */
export function ChangePassword({ forced = false, onDone }: { forced?: boolean; onDone?: () => void }) {
  const { passwordChanged, logout } = useAuth();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (next !== again) { setError("The new passwords do not match."); return; }
    setBusy(true);
    try {
      await api.changePassword(cur, next);
      setOk(true); setCur(""); setNext(""); setAgain("");
      passwordChanged();
      onDone?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>{forced ? "Choose a new password" : "Change password"}</CardTitle>
        <CardDescription>
          {forced ? "You must set a new password before using the system." : "Other signed-in sessions will be signed out."}
          {" "}Use at least 10 characters.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          {[["Current password", cur, setCur, "current-password"], ["New password", next, setNext, "new-password"], ["Repeat new password", again, setAgain, "new-password"]].map(
            ([label, val, set, ac]: any) => (
              <label key={label} className="block text-sm">
                <span className="text-muted-foreground">{label}</span>
                <input type="password" required autoComplete={ac} value={val} onChange={(e) => set(e.target.value)}
                  className="mt-1 w-full h-10 rounded-md border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-blue-500/50" />
              </label>
            ))}
          {error && <div role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}
          {ok && !forced && <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-700">Password changed.</div>}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>{busy ? "Saving..." : "Change password"}</Button>
            {forced && <Button type="button" variant="outline" onClick={logout}>Sign out</Button>}
          </div>
        </form>
      </CardContent>
    </Card>
  );

  return forced ? <div className="min-h-screen flex items-center justify-center p-4 bg-background">{form}</div> : form;
}
