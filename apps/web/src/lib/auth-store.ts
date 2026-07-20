"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface AuthUser {
  id: string;
  email: string;
  role: string;
  full_name: string | null;
}

export interface AuthTenant {
  id: string;
  slug: string;
  name: string;
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  tenant: AuthTenant | null;
  setSession: (data: {
    access_token: string;
    refresh_token: string;
    user: AuthUser;
    tenant: AuthTenant;
  }) => void;
  setTokens: (access: string, refresh: string) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      tenant: null,
      setSession: (d) =>
        set({
          accessToken: d.access_token,
          refreshToken: d.refresh_token,
          user: d.user,
          tenant: d.tenant,
        }),
      setTokens: (access, refresh) =>
        set({ accessToken: access, refreshToken: refresh }),
      clear: () =>
        set({ accessToken: null, refreshToken: null, user: null, tenant: null }),
    }),
    { name: "leadpulse-auth" },
  ),
);
