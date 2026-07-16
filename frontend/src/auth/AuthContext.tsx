import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { login as apiLogin, getMe } from "../api/client";

interface AuthState {
  token: string | null;
  role: string | null;
  org: string | null;
  orgName: string | null;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "auth_token";
const ROLE_KEY = "auth_role";
const ORG_KEY = "auth_org";
const ORG_NAME_KEY = "auth_org_name";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem(TOKEN_KEY)
  );
  const [role, setRole] = useState<string | null>(
    () => localStorage.getItem(ROLE_KEY)
  );
  const [org, setOrg] = useState<string | null>(
    () => localStorage.getItem(ORG_KEY)
  );
  const [orgName, setOrgName] = useState<string | null>(
    () => localStorage.getItem(ORG_NAME_KEY)
  );

  // Self-heal stale sessions: if a token exists but orgName was never stored
  // (sessions created before org_name was added to /auth/me), fetch it once on
  // mount and backfill role/org/orgName so the topbar shows the org name rather
  // than a bare UUID. Runs once; failures are non-fatal.
  useEffect(() => {
    if (!token || orgName) return;
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (cancelled) return;
        localStorage.setItem(ROLE_KEY, me.role);
        setRole(me.role);
        if (me.org) {
          localStorage.setItem(ORG_KEY, me.org);
        } else {
          localStorage.removeItem(ORG_KEY);
        }
        setOrg(me.org);
        if (me.org_name) {
          localStorage.setItem(ORG_NAME_KEY, me.org_name);
        } else {
          localStorage.removeItem(ORG_NAME_KEY);
        }
        setOrgName(me.org_name);
      } catch {
        // Token may be invalid/expired; leave state untouched.
      }
    })();
    return () => {
      cancelled = true;
    };
    // Intentionally run only once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const resp = await apiLogin(email, password);
    localStorage.setItem(TOKEN_KEY, resp.access_token);
    setToken(resp.access_token);
    // Fetch the user's role from /auth/me now that the token is in localStorage.
    // On failure, still treat the user as logged in — role is just unavailable.
    try {
      const me = await getMe();
      localStorage.setItem(ROLE_KEY, me.role);
      setRole(me.role);
      if (me.org) {
        localStorage.setItem(ORG_KEY, me.org);
      } else {
        localStorage.removeItem(ORG_KEY);
      }
      setOrg(me.org);
      if (me.org_name) {
        localStorage.setItem(ORG_NAME_KEY, me.org_name);
      } else {
        localStorage.removeItem(ORG_NAME_KEY);
      }
      setOrgName(me.org_name);
    } catch {
      setRole(null);
      setOrg(null);
      setOrgName(null);
    }
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(ORG_KEY);
    localStorage.removeItem(ORG_NAME_KEY);
    setToken(null);
    setRole(null);
    setOrg(null);
    setOrgName(null);
  }, []);

  return (
    <AuthContext.Provider value={{ token, role, org, orgName, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
