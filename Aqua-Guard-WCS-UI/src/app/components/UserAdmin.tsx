import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { api } from "../lib/api";
import { fmtTime, usePolling } from "../lib/usePolling";
import { ConnectionBanner, Pill } from "./common";

/** Admin-only: user list, create user (forced password change at first login), enable/disable, role, audit log. */
export function UserAdmin() {
  const users = usePolling(api.getUsers, 15000);
  const audit = usePolling(() => api.getAudit(40), 20000);
  const [form, setForm] = useState({ email: "", password: "", role: "user" as "user" | "admin" });
  const [busy, setBusy] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.createUser(form);
      toast.success("User created. They must change the password at first login.");
      setForm({ email: "", password: "", role: "user" });
      users.refresh(); audit.refresh();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Could not create user"); }
    finally { setBusy(false); }
  };

  const update = async (id: number, b: { role?: "user" | "admin"; active?: boolean }) => {
    try { await api.updateUser(id, b); users.refresh(); audit.refresh(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Could not update user"); }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>User accounts</CardTitle>
          <CardDescription>Roles are enforced by the server. Disabling an account signs it out immediately.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ConnectionBanner error={users.error} stale={users.stale} onRetry={users.refresh} />
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead className="text-muted-foreground border-b"><tr><th className="py-1 pr-2">Email</th><th className="pr-2">Role</th><th className="pr-2">Status</th><th /></tr></thead>
              <tbody className="divide-y">
                {users.data?.map((u) => (
                  <tr key={u.id}>
                    <td className="py-2 pr-2">{u.email}</td>
                    <td className="pr-2">{u.role}</td>
                    <td className="pr-2">{u.active ? <Pill tone="green">active</Pill> : <Pill tone="gray">disabled</Pill>}</td>
                    <td className="text-right space-x-2 whitespace-nowrap">
                      <button className="underline" onClick={() => update(u.id, { role: u.role === "admin" ? "user" : "admin" })}>
                        Make {u.role === "admin" ? "user" : "admin"}
                      </button>
                      <button className="underline" onClick={() => update(u.id, { active: !u.active })}>{u.active ? "Disable" : "Enable"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <form onSubmit={create} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto] items-end border-t pt-4">
            <label className="text-xs">Email
              <input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm" /></label>
            <label className="text-xs">Temporary password (min 10)
              <input type="password" required minLength={10} autoComplete="new-password" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm" /></label>
            <label className="text-xs">Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "user" | "admin" })}
                className="mt-1 w-full h-9 rounded-md border bg-background px-2 text-sm">
                <option value="user">user</option><option value="admin">admin</option></select></label>
            <Button type="submit" disabled={busy}>Add user</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Audit log</CardTitle><CardDescription>Logins, failures, password and user changes, alert acknowledgements</CardDescription></CardHeader>
        <CardContent>
          <div className="overflow-x-auto max-h-72">
            <table className="w-full text-xs text-left">
              <thead className="text-muted-foreground border-b"><tr><th className="py-1 pr-2">Time</th><th className="pr-2">Event</th><th className="pr-2">User</th><th>Detail</th></tr></thead>
              <tbody className="divide-y">
                {audit.data?.map((a, i) => (
                  <tr key={i}>
                    <td className="py-1.5 pr-2 whitespace-nowrap">{fmtTime(String(a.ts ?? ""))}</td>
                    <td className="pr-2">{String(a.action ?? "")}</td>
                    <td className="pr-2">{String(a.email ?? "")}</td>
                    <td>{String(a.detail ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
