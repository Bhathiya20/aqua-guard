import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { User, Mail, Phone, MapPin, CheckCircle, AlertCircle } from "lucide-react";
import { api } from "../lib/api";
import { ChangePassword } from "./ChangePassword";

interface UserProfileProps {
  isAdmin?: boolean;
  userEmail: string;
}

export function UserProfile({ isAdmin = false, userEmail }: UserProfileProps) {
  const [form, setForm] = useState({
    firstName: "",
    lastName: "",
    email: userEmail,
    phone: "",
    address: "",
  });

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let isMounted = true;

    const loadProfile = async () => {
      try {
        setLoading(true);
        const data = await api.getProfile();
        if (isMounted) {
          setForm({
            firstName: data.firstName || "",
            lastName: data.lastName || "",
            email: data.email || userEmail,
            phone: data.phone || "",
            address: data.address || "",
          });
          setError("");
        }
      } catch (err) {
        if (isMounted) {
          setError("Failed to load user profile from database.");
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    if (userEmail) {
      loadProfile();
    }
  }, [userEmail]);

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    setError("");

    try {
      await api.updateProfile({
        firstName: form.firstName.trim(),
        lastName: form.lastName.trim(),
        email: form.email.trim(),
        phone: form.phone.trim(),
        address: form.address.trim(),
      });
      setMessage("Profile updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update profile.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">User Profile</h1>
        <p className="text-sm text-muted-foreground">
          Manage your account settings and contact details
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="h-12 w-12 rounded-full bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center text-blue-700">
              <User className="h-6 w-6" />
            </div>
            <div>
              <CardTitle>{isAdmin ? "Administrator" : "User"} Information</CardTitle>
              <CardDescription>
                Account role: <span className="font-semibold uppercase text-xs tracking-wider">{isAdmin ? "Admin" : "User"}</span>
              </CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 p-3 text-xs text-red-700 flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {message && (
            <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3 text-xs text-emerald-700 flex items-center gap-2">
              <CheckCircle className="h-4 w-4 shrink-0" />
              {message}
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-700">First Name</label>
              <Input
                placeholder="First Name"
                value={form.firstName}
                onChange={(e) => setForm({ ...form, firstName: e.target.value })}
                disabled={loading}
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-700">Last Name</label>
              <Input
                placeholder="Last Name"
                value={form.lastName}
                onChange={(e) => setForm({ ...form, lastName: e.target.value })}
                disabled={loading}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
              <Mail className="h-3.5 w-3.5 text-slate-400" /> Email Address
            </label>
            <Input
              type="email"
              placeholder="user@example.com"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              disabled={loading}
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
              <Phone className="h-3.5 w-3.5 text-slate-400" /> Contact Phone
            </label>
            <Input
              placeholder="+94 71 234 5678"
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
              disabled={loading}
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
              <MapPin className="h-3.5 w-3.5 text-slate-400" /> Physical Address
            </label>
            <Input
              placeholder="Department / Faculty / Campus Address"
              value={form.address}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
              disabled={loading}
            />
          </div>

          <div className="pt-2">
            <Button
              className="bg-blue-600 hover:bg-blue-700 text-white"
              onClick={handleSave}
              disabled={loading || saving}
            >
              {saving ? "Saving Changes..." : "Save Profile Changes"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <ChangePassword />
    </div>
  );
}
