"use client";

import { useAuthStore } from "./auth-store";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function refreshTokens(): Promise<boolean> {
  const rt = useAuthStore.getState().refreshToken;
  if (!rt) return false;
  try {
    const resp = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    useAuthStore.getState().setTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const token = useAuthStore.getState().accessToken;
    const headers = new Headers(init.headers);
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API_BASE}${path}`, { ...init, headers });
  };

  let resp = await doFetch();
  if (resp.status === 401 && useAuthStore.getState().refreshToken) {
    if (await refreshTokens()) {
      resp = await doFetch();
    }
  }

  if (!resp.ok) {
    let detail: unknown = null;
    try {
      detail = await resp.json();
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, resp.statusText, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export { API_BASE };
