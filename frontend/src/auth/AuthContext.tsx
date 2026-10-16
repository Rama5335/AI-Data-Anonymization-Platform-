import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AuthRequiredError, api, setAuthToken } from "../lib/api";
import type { AuthUser, LoginResponse } from "../lib/types";

type AuthState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "authenticated"; user: AuthUser; token: string };

type AuthContextValue = {
  state: AuthState;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider(props: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" });

  // On mount: try /users/me. We always start with the (empty) token,
  // so today the API returns 401 and we land in `anonymous`. If we
  // ever add a refresh-token cookie, this is where it would slot in.
  useEffect(() => {
    api
      .me()
      .then((user) => {
        const tok = getStoredToken();
        if (tok) setAuthToken(tok);
        setState({ kind: "authenticated", user, token: tok ?? "" });
      })
      .catch((err) => {
        if (err instanceof AuthRequiredError) {
          setState({ kind: "anonymous" });
        } else {
          setState({ kind: "anonymous" });
        }
      });
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res: LoginResponse = await api.login({ username, password });
    setAuthToken(res.access_token);
    setState({ kind: "authenticated", user: res.user, token: res.access_token });
    return res.user;
  }, []);

  const logout = useCallback(() => {
    setAuthToken(null);
    setState({ kind: "anonymous" });
  }, []);

  const value = useMemo<AuthContextValue>(() => ({ state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{props.children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth must be used within an AuthProvider");
  return v;
}

function getStoredToken(): string | null {
  return null; // in-memory only; no localStorage
}
