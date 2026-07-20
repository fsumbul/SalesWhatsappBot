"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function RegisterPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const locale = useLocale();
  const setSession = useAuthStore((s) => s.setSession);

  const [tenantName, setTenantName] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
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
      }>("/api/v1/auth/register-tenant", {
        method: "POST",
        body: JSON.stringify({
          tenant_name: tenantName,
          tenant_slug: tenantSlug,
          owner_email: email,
          owner_password: password,
          owner_full_name: fullName || null,
        }),
      });
      setSession(data);
      router.push(`/${locale}/dashboard`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? (err.detail as { detail?: string } | null)?.detail ?? err.message
          : "Register failed";
      setError(String(msg));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-8 shadow-sm"
      >
        <h1 className="mb-6 text-2xl font-bold">{t("registerTitle")}</h1>

        <div className="grid grid-cols-2 gap-3">
          <label className="mb-3 block">
            <span className="text-sm font-medium text-slate-700">{t("tenantName")}</span>
            <input
              required
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="mb-3 block">
            <span className="text-sm font-medium text-slate-700">{t("tenantSlug")}</span>
            <input
              required
              value={tenantSlug}
              onChange={(e) => setTenantSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
        </div>

        <label className="mb-3 block">
          <span className="text-sm font-medium text-slate-700">{t("fullName")}</span>
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
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
            minLength={12}
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
          {loading ? "..." : t("registerButton")}
        </button>

        <p className="mt-4 text-center text-sm text-slate-600">
          {t("haveAccount")}{" "}
          <Link href={`/${locale}/login`} className="font-medium text-slate-900 underline">
            {t("loginLink")}
          </Link>
        </p>
      </form>
    </div>
  );
}
