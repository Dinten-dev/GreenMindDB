'use client';

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { AuthUser, apiGetMe, apiLogin, apiSignup, apiLogout, apiCreateOrg } from '@/lib/api';

interface AuthContextType {
    user: AuthUser | null;
    loading: boolean;
    login: (email: string, password: string) => Promise<void>;
    signup: (email: string, password: string, name: string) => Promise<void>;
    logout: () => Promise<void>;
    createOrg: (name: string) => Promise<void>;
    refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<AuthUser | null>(null);
    const [loading, setLoading] = useState(true);

    const refresh = useCallback(async () => {
        try {
            const me = await apiGetMe();
            setUser(me);
        } catch {
            setUser(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        refresh();
    }, [refresh]);

    const login = async (email: string, password: string) => {
        const res = await apiLogin(email, password);
        setUser(res.user);
    };

    const signup = async (email: string, password: string, name: string) => {
        const res = await apiSignup(email, password, name);
        setUser(res.user);
    };

    const logout = async () => {
        await apiLogout();
        setUser(null);
    };

    const createOrg = async (name: string) => {
        await apiCreateOrg(name);
        await refresh();
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, signup, logout, createOrg, refresh }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth(): AuthContextType {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used within AuthProvider');
    return ctx;
}
