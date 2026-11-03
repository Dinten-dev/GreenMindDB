'use client';

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  ReactNode,
} from 'react';
import { usePathname } from 'next/navigation';
import {
  ApiError,
  AuthUser,
  apiGetMe,
  apiLogin,
  apiSignup,
  apiLogout,
  apiCreateOrg,
} from '@/lib/api';
import { AuthIssue, SessionConfirmationError, sessionIssue } from '@/lib/auth-errors';
import { advanceSessionEpoch, SESSION_INVALIDATED } from '@/lib/auth-session';

type AuthStatus = 'checking' | 'authenticated' | 'anonymous' | 'signed-out' | 'unavailable';
interface AuthContextType {
  user: AuthUser | null;
  loading: boolean;
  status: AuthStatus;
  issue: AuthIssue | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<string>;
  logout: () => Promise<void>;
  createOrg: (name: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('checking');
  const [issue, setIssue] = useState<AuthIssue | null>(null);
  const currentUser = useRef<AuthUser | null>(null);
  const revision = useRef(0);
  const mounted = useRef(false);
  const mutating = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);

  const commit = useCallback(
    (nextUser: AuthUser | null, nextStatus: AuthStatus, nextIssue: AuthIssue | null) => {
      currentUser.current = nextUser;
      setUser(nextUser);
      setStatus(nextStatus);
      setIssue(nextIssue);
    },
    []
  );

  const refresh = useCallback(() => {
    if (mutating.current) return Promise.resolve();
    if (inFlight.current) return inFlight.current;
    const version = ++revision.current;
    const request = new AbortController();
    controller.current = request;
    const hadUser = currentUser.current !== null;
    const task = Promise.resolve().then(async () => {
      try {
        const me = await apiGetMe(request.signal);
        if (mounted.current && revision.current === version) commit(me, 'authenticated', null);
      } catch (error) {
        if (!mounted.current || request.signal.aborted || revision.current !== version) return;
        const nextIssue = sessionIssue(error, hadUser);
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          advanceSessionEpoch();
          commit(null, 'anonymous', nextIssue);
        } else {
          commit(currentUser.current, 'unavailable', nextIssue);
        }
      } finally {
        if (controller.current === request) {
          controller.current = null;
          inFlight.current = null;
        }
      }
    });
    inFlight.current = task;
    return task;
  }, [commit]);

  const cancelRefresh = useCallback(() => {
    ++revision.current;
    controller.current?.abort();
    controller.current = null;
    inFlight.current = null;
  }, []);

  useEffect(() => {
    mounted.current = true;
    const invalidate = () => {
      if (mutating.current) return;
      cancelRefresh();
      advanceSessionEpoch();
      commit(null, 'anonymous', 'expired');
    };
    const checkVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    window.addEventListener(SESSION_INVALIDATED, invalidate);
    window.addEventListener('focus', checkVisible);
    document.addEventListener('visibilitychange', checkVisible);
    const timer = window.setInterval(() => {
      if (currentUser.current) checkVisible();
    }, 60000);
    return () => {
      mounted.current = false;
      cancelRefresh();
      window.clearInterval(timer);
      window.removeEventListener(SESSION_INVALIDATED, invalidate);
      window.removeEventListener('focus', checkVisible);
      document.removeEventListener('visibilitychange', checkVisible);
    };
  }, [cancelRefresh, commit, refresh]);

  useEffect(() => {
    void refresh();
  }, [pathname, refresh]);

  const beginMutation = () => {
    if (mutating.current) throw new SessionConfirmationError();
    mutating.current = true;
    cancelRefresh();
    advanceSessionEpoch();
    const version = revision.current;
    setStatus('checking');
    setIssue(null);
    return version;
  };

  const login = async (email: string, password: string) => {
    const version = beginMutation();
    let confirmedCredentials = false;
    try {
      await apiLogin(email, password);
      confirmedCredentials = true;
      // A 200 login response alone does not prove the browser retained its cookie.
      const me = await apiGetMe();
      if (!mounted.current || revision.current !== version) throw new SessionConfirmationError();
      advanceSessionEpoch();
      commit(me, 'authenticated', null);
    } catch (error) {
      if (mounted.current && revision.current === version) {
        const unavailable = !(error instanceof ApiError && error.status < 500);
        commit(
          null,
          unavailable ? 'unavailable' : 'anonymous',
          unavailable ? sessionIssue(error, false) : null
        );
      }
      if (confirmedCredentials && error instanceof ApiError && error.status === 401)
        throw new SessionConfirmationError();
      throw error;
    } finally {
      mutating.current = false;
    }
  };

  const logout = async () => {
    const version = beginMutation();
    try {
      await apiLogout();
      if (mounted.current && revision.current === version) {
        advanceSessionEpoch();
        commit(null, 'signed-out', null);
      }
    } catch (error) {
      if (mounted.current && revision.current === version)
        commit(currentUser.current, 'unavailable', sessionIssue(error, !!currentUser.current));
      throw error;
    } finally {
      mutating.current = false;
    }
  };

  const signup = async (email: string, password: string, name: string) =>
    (await apiSignup(email, password, name)).detail;
  const createOrg = async (name: string) => {
    await apiCreateOrg(name);
    await refresh();
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading: status === 'checking',
        status,
        issue,
        login,
        signup,
        logout,
        createOrg,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
