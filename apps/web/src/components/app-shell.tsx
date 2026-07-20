"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { useAuthStore } from "@/lib/auth-store";

const NAV_ITEMS = [
  { href: "dashboard", key: "dashboard", icon: "grid" },
  { href: "leads", key: "leads", icon: "users" },
  { href: "campaigns", key: "campaigns", icon: "target" },
  { href: "sectors", key: "sectors", icon: "layers" },
  { href: "templates", key: "templates", icon: "message" },
  { href: "senders", key: "senders", icon: "send" },
  { href: "inbox", key: "inbox", icon: "inbox" },
  { href: "compliance", key: "compliance", icon: "shield" },
  { href: "reports", key: "reports", icon: "chart" },
] as const;

const ICONS: Record<string, ReactNode> = {
  grid: <path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />,
  users: (
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </>
  ),
  layers: <path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />,
  message: (
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  ),
  send: <path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z" />,
  inbox: (
    <path d="M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
  ),
  shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
  chart: <path d="M18 20V10M12 20V4M6 20v-6" />,
};

export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations("nav");
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const { user, tenant, clear } = useAuthStore();

  const handleLogout = () => {
    clear();
    router.push(`/${locale}/login`);
  };

  const initials = (tenant?.name ?? "LP")
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="flex min-h-screen bg-slate-100 text-slate-900">
      <aside className="fixed inset-y-0 left-0 flex w-64 shrink-0 flex-col bg-slate-900 px-4 py-6 text-slate-300">
        <div className="mb-8 flex items-center gap-3 px-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-sky-400 text-sm font-bold text-white shadow-lg">
            {initials}
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-base font-bold text-white">
              {tenant?.name ?? "LeadPulse"}
            </h1>
            <p className="text-[11px] uppercase tracking-wide text-slate-500">
              LeadPulse
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map((item) => {
            const href = `/${locale}/${item.href}`;
            const active =
              pathname === href || pathname.startsWith(`${href}?`);
            return (
              <Link
                key={item.href}
                href={href}
                className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  active
                    ? "bg-gradient-to-r from-indigo-500 to-sky-500 text-white shadow"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                }`}
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-[18px] w-[18px] shrink-0"
                >
                  {ICONS[item.icon]}
                </svg>
                {t(item.key)}
              </Link>
            );
          })}
        </nav>

        <div className="mt-4 border-t border-slate-800 pt-4">
          {user && (
            <div className="mb-3 flex items-center gap-3 px-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-700 text-xs font-semibold text-white">
                {user.email.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0 text-xs">
                <div className="truncate text-slate-200">{user.email}</div>
                <div className="text-slate-500">{user.role}</div>
              </div>
            </div>
          )}
          <button
            onClick={handleLogout}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 transition hover:bg-slate-800 hover:text-white"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
            </svg>
            {t("logout")}
          </button>
        </div>
      </aside>

      <main className="ml-64 flex-1 overflow-x-auto">
        <div className="mx-auto max-w-7xl p-8">{children}</div>
      </main>
    </div>
  );
}
