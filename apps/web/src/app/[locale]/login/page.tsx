"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function LoginPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const locale = useLocale();
  const setSession = useAuthStore((s) => s.setSession);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await api<{
        access_token: string;
        refresh_token: string;
        user: { id: string; email: string; role: string; full_name: string | null };
        tenant: { id: string; slug: string; name: string };
      }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, tenant_slug: tenantSlug || null }),
      });
      setSession(data);
      router.push(`/${locale}/dashboard`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.detail as { detail?: string } | null)?.detail ?? err.message
          : "Login failed";
      setError(String(msg));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 shadow-sm"
      >
        <h1 className="mb-6 text-2xl font-bold">{t("loginTitle")}</h1>

        <label className="mb-3 block">
          <span className="text-sm font-medium text-slate-700">{t("tenantSlug")}</span>
          <input
            value={tenantSlug}
            onChange={(e) => setTenantSlug(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="mycompany"
          />
        </label>

        <label className="mb-3 block">
          <span className="text-sm font-medium text-slate-700">{t("email")}</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>

        <label className="mb-4 block">
          <span className="text-sm font-medium text-slate-700">{t("password")}</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>

        {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

        <button
          disabled={loading}
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {loading ? "..." : t("loginButton")}
        </button>

        <p className="mt-4 text-center text-sm text-slate-600">
          {t("noAccount")}{" "}
          <Link href={`/${locale}/register`} className="font-medium text-slate-900 underline">
            {t("registerLink")}
          </Link>
        </p>
      </form>
    </div>
  );
}
