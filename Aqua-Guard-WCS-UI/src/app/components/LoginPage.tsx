// Login screen.
import { useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

export function LoginPage() {
  const { login, serverDown } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(email.trim(), password);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        const s = err.data?.retryAfterSeconds;
        setError(`Too many failed attempts. Try again${s ? ` in about ${Math.ceil(s / 60)} min` : " later"}.`);
      } else {
        setError(err instanceof Error ? err.message : "Login failed. Please check your credentials.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="min-h-screen w-full flex items-center justify-center bg-cover bg-center bg-no-repeat px-4 py-8 bg-[#10204f]"
      style={{ backgroundImage: "url('/login-background.png')" }}
    >
      <div className="w-full max-w-[430px] rounded-[28px] bg-[#17275f]/80 backdrop-blur-md border border-white/10 shadow-2xl p-6 sm:p-10 lg:-translate-x-[180px]">
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="space-y-1">
            <h1 className="text-4xl font-medium tracking-wide text-white">LOGIN</h1>
            <p className="text-xs text-blue-200/80">Aqua Guard IoT Water Management</p>
          </div>

          <div className="space-y-2">
            <label htmlFor="email" className="block text-base text-white/80">Email</label>
            <input
              id="email" type="email" autoComplete="username" value={email}
              onChange={(e) => setEmail(e.target.value)} required
              className="w-full h-12 rounded-xl border border-white/20 bg-white/90 px-4 text-gray-800 outline-none focus:ring-2 focus:ring-white/50 placeholder:text-gray-400 text-sm"
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="password" className="block text-base text-white/80">Password</label>
            <input
              id="password" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required
              className="w-full h-12 rounded-xl border border-white/20 bg-white/90 px-4 text-gray-800 outline-none focus:ring-2 focus:ring-white/50 placeholder:text-gray-400 text-sm"
            />
          </div>

          <p className="text-xs text-white/60">Forgot your password? Ask a system administrator to reset it.</p>

          {serverDown && !error && (
            <div className="rounded-lg border border-amber-300/30 bg-amber-500/20 px-3.5 py-2.5 text-xs text-white">
              Cannot reach the Aqua Guard server. Check that the backend is running and the network is up.
            </div>
          )}
          {error && (
            <div role="alert" className="rounded-lg border border-red-300/30 bg-red-500/20 px-3.5 py-2.5 text-xs text-white">
              {error}
            </div>
          )}

          <button
            type="submit" disabled={loading}
            className="w-full h-12 rounded-full bg-[#3531a8] text-white font-semibold shadow-lg hover:bg-[#423dc0] transition-colors disabled:opacity-50 text-sm"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
