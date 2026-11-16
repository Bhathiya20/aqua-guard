import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, onUnauthorized, type UserProfileData } from "./api";

interface AuthState {
  status: "loading" | "anonymous" | "authenticated";
  user: UserProfileData | null;
  mustChangePassword: boolean;
  serverDown: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  passwordChanged: () => void;
  refreshUser: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [user, setUser] = useState<UserProfileData | null>(null);
  const [mustChange, setMustChange] = useState(false);
  const [serverDown, setServerDown] = useState(false);

  const restore = useCallback(async () => {
    try {
      const s = await api.me();                       // cookie session survives reloads
      setUser(s.user); setMustChange(s.mustChangePassword); setStatus("authenticated"); setServerDown(false);
    } catch (e) {
      setUser(null);
      setServerDown(e instanceof ApiError && e.status === 0);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => { restore(); }, [restore]);

  // any 401 from a data call (expired/revoked session) returns the user to the login screen
  useEffect(() => {
    onUnauthorized(() => { setUser(null); setStatus("anonymous"); });
    return () => onUnauthorized(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const s = await api.login(email, password);
    setUser(s.user); setMustChange(s.mustChangePassword); setStatus("authenticated"); setServerDown(false);
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* session may already be gone */ }
    setUser(null); setMustChange(false); setStatus("anonymous");
  }, []);

  const refreshUser = useCallback(async () => { await restore(); }, [restore]);

  return (
    <Ctx.Provider value={{ status, user, mustChangePassword: mustChange, serverDown, login, logout, passwordChanged: () => setMustChange(false), refreshUser }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
