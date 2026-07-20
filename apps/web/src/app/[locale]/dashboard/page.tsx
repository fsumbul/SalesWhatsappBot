"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useLocale } from "next-intl";
import { useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Funnel {
  discovered: number;
  enriched: number;
  qualified: number;
  contacted: number;
  replied: number;
  interested: number;
  won: number;
  lost: number;
}

interface Sales {
  total_leads: number;
  contacted: number;
  reply_rate: number;
  conversion_rate: number;
}

interface Sector {
  id: string;
  name: string;
}

interface Campaign {
  id: string;
  name: string;
  status: string;
  sector_id: string;
  created_at: string;
}

interface DiscoveryStatus {
  campaign_id: string;
  status: string;
  total_leads: number;
  discovered: number;
  scanned: number;
  enriched: number;
  qualified: number;
}

interface SectorCountry {
  id: string;
  country_code: string;
  priority: number;
}

interface SectorDetail {
  id: string;
  name: string;
  countries: SectorCountry[];
}

export default function DashboardPage() {
  return (
    <RequireAuth>
      <AppShell>
        <DashboardContent />
      </AppShell>
    </RequireAuth>
  );
}

const FUNNEL_LABELS: Record<keyof Funnel, string> = {
  discovered: "Bulundu",
  enriched: "Zenginleştirildi",
  qualified: "Nitelikli",
  contacted: "İletişim kuruldu",
  replied: "Yanıtladı",
  interested: "İlgilendi",
  won: "Kazanıldı",
  lost: "Kaybedildi",
};

const COUNTRY_META: Record<string, { flag: string; label: string }> = {
  TR: { flag: "🇹🇷", label: "Türkiye" },
  DE: { flag: "🇩🇪", label: "Almanya" },
  GB: { flag: "🇬🇧", label: "İngiltere" },
  UK: { flag: "🇬🇧", label: "İngiltere" },
  AE: { flag: "🇦🇪", label: "BAE" },
  SA: { flag: "🇸🇦", label: "S. Arabistan" },
  RU: { flag: "🇷🇺", label: "Rusya" },
  US: { flag: "🇺🇸", label: "ABD" },
  FR: { flag: "🇫🇷", label: "Fransa" },
  IT: { flag: "🇮🇹", label: "İtalya" },
  ES: { flag: "🇪🇸", label: "İspanya" },
  NL: { flag: "🇳🇱", label: "Hollanda" },
};

function DashboardContent() {
  const locale = useLocale();
  const qc = useQueryClient();
  const [sectorId, setSectorId] = useState("");
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null);
  const [selectedCountries, setSelectedCountries] = useState<string[]>([]);
  const initedSector = useRef<string | null>(null);

  const funnelQ = useQuery({
    queryKey: ["funnel"],
    queryFn: () => api<Funnel>("/api/v1/reports/funnel"),
  });
  const salesQ = useQuery({
    queryKey: ["sales"],
    queryFn: () => api<Sales>("/api/v1/reports/sales"),
  });
  const sectorsQ = useQuery({
    queryKey: ["sectors"],
    queryFn: () => api<Sector[]>("/api/v1/sectors"),
  });

  // Countries configured for the picked sector (drives the scan filter chips).
  const sectorDetailQ = useQuery({
    queryKey: ["sector-detail", sectorId],
    queryFn: () => api<SectorDetail>(`/api/v1/sectors/${sectorId}`),
    enabled: !!sectorId,
  });

  const availableCountries = useMemo(
    () =>
      (sectorDetailQ.data?.countries ?? [])
        .slice()
        .sort((a, b) => b.priority - a.priority)
        .map((c) => c.country_code.toUpperCase()),
    [sectorDetailQ.data],
  );

  // Default to "all countries selected" once a sector's countries load.
  useEffect(() => {
    if (
      sectorId &&
      availableCountries.length &&
      initedSector.current !== sectorId
    ) {
      setSelectedCountries(availableCountries);
      initedSector.current = sectorId;
    }
  }, [sectorId, availableCountries]);

  const toggleCountry = (code: string) =>
    setSelectedCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );

  // Live status of a running scan.
  const statusQ = useQuery({
    queryKey: ["discovery-status", activeCampaignId],
    queryFn: () =>
      api<DiscoveryStatus>(
        `/api/v1/campaigns/${activeCampaignId}/discovery-status`,
      ),
    enabled: !!activeCampaignId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "discovering" || s === "enriching" ? 4000 : false;
    },
  });

  const scan = useMutation({
    mutationFn: async () => {
      const sector = sectorsQ.data?.find((s) => s.id === sectorId);
      const name = `${sector?.name ?? "Tarama"} — ${new Date().toLocaleDateString(
        "tr-TR",
      )}`;
      const campaign = await api<Campaign>("/api/v1/campaigns", {
        method: "POST",
        body: JSON.stringify({ name, sector_id: sectorId }),
      });
      await api(`/api/v1/campaigns/${campaign.id}/discover`, {
        method: "POST",
        body: JSON.stringify({
          countries: selectedCountries.length ? selectedCountries : null,
        }),
      });
      return campaign;
    },
    onSuccess: (c) => {
      setActiveCampaignId(c.id);
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  const f = funnelQ.data;
  const s = salesQ.data;
  const status = statusQ.data;
  const scanning =
    status?.status === "discovering" || status?.status === "enriching";
  const scanReady =
    !!status && (status.status === "ready" || status.status === "completed");

  const funnelMax = useMemo(
    () => (f ? Math.max(...Object.values(f), 1) : 1),
    [f],
  );

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Panel</h1>
        <p className="mt-1 text-sm text-slate-500">
          Sektörünü seç, tek tıkla gerçek müşterileri tara ve yönet.
        </p>
      </div>

      {/* Scan hero */}
      <div className="overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-600 to-sky-500 p-6 text-white shadow-lg">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="max-w-lg">
            <h2 className="text-lg font-semibold">Müşteri Tara</h2>
            <p className="mt-1 text-sm text-indigo-100">
              Bir sektör seç; sistem senin için gerçek firmaları bulur, web
              sitelerini tarar ve iletişim bilgilerini çıkarır.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={sectorId}
              onChange={(e) => setSectorId(e.target.value)}
              className="rounded-lg border-0 bg-white/95 px-4 py-2.5 text-sm font-medium text-slate-800 shadow-sm focus:ring-2 focus:ring-white"
            >
              <option value="">Sektör seç…</option>
              {sectorsQ.data?.map((sec) => (
                <option key={sec.id} value={sec.id}>
                  {sec.name}
                </option>
              ))}
            </select>
            <button
              disabled={!sectorId || scan.isPending || scanning}
              onClick={() => scan.mutate()}
              className="flex items-center justify-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {scan.isPending || scanning ? (
                <>
                  <Spinner /> Taranıyor…
                </>
              ) : (
                <>
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="h-4 w-4"
                  >
                    <circle cx="11" cy="11" r="8" />
                    <path d="m21 21-4.3-4.3" />
                  </svg>
                  Taramayı Başlat
                </>
              )}
            </button>
          </div>
        </div>

        {sectorId && availableCountries.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-indigo-100">
              Hedef ülkeler
            </div>
            <div className="flex flex-wrap gap-2">
              {availableCountries.map((code) => {
                const active = selectedCountries.includes(code);
                const meta = COUNTRY_META[code] ?? { flag: "🌐", label: code };
                return (
                  <button
                    key={code}
                    type="button"
                    disabled={scanning}
                    onClick={() => toggleCountry(code)}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
                      active
                        ? "bg-white text-indigo-700 shadow-sm ring-2 ring-white/70"
                        : "bg-white/15 text-white hover:bg-white/25"
                    }`}
                  >
                    <span>{meta.flag}</span>
                    {meta.label}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {status && (
          <div className="mt-5 rounded-xl bg-white/10 p-4 backdrop-blur">
            <div className="flex items-end justify-between">
              <div>
                <div className="text-3xl font-bold leading-none">
                  {status.qualified}
                </div>
                <div className="mt-1 text-xs text-indigo-100">
                  nitelikli firma bulundu (≥%80 eşleşme)
                </div>
              </div>
              <div className="text-right text-sm">
                <div className="font-medium">
                  {scanning
                    ? status.total_leads === 0
                      ? "Firmalar bulunuyor…"
                      : "Web siteleri taranıyor…"
                    : "Tarama tamamlandı"}
                </div>
                {scanning && status.total_leads > 0 && (
                  <div className="text-indigo-100">
                    {status.scanned}/{status.total_leads} firma tarandı
                  </div>
                )}
              </div>
            </div>
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-white/20">
              <div
                className="h-full rounded-full bg-white transition-all"
                style={{
                  width: scanReady
                    ? "100%"
                    : `${Math.min(
                        95,
                        status.total_leads
                          ? (status.scanned / status.total_leads) * 100
                          : 8,
                      )}%`,
                }}
              />
            </div>
            {scanReady && (
              <Link
                href={`/${locale}/leads?campaign_id=${status.campaign_id}`}
                className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-white underline-offset-2 hover:underline"
              >
                {status.qualified} nitelikli müşteriyi görüntüle →
              </Link>
            )}
          </div>
        )}
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Toplam Aday"
          value={s?.total_leads ?? 0}
          tone="indigo"
        />
        <StatCard
          label="İletişim Kurulan"
          value={s?.contacted ?? 0}
          tone="sky"
        />
        <StatCard
          label="Yanıt Oranı"
          value={`${((s?.reply_rate ?? 0) * 100).toFixed(1)}%`}
          tone="emerald"
        />
        <StatCard
          label="Dönüşüm"
          value={`${((s?.conversion_rate ?? 0) * 100).toFixed(1)}%`}
          tone="amber"
        />
      </div>

      {/* Funnel + quick links */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold text-slate-700">
            Satış Hunisi
          </h2>
          <div className="space-y-3">
            {f &&
              (Object.keys(FUNNEL_LABELS) as (keyof Funnel)[]).map((k) => (
                <div key={k} className="flex items-center gap-3">
                  <div className="w-32 shrink-0 text-xs font-medium text-slate-500">
                    {FUNNEL_LABELS[k]}
                  </div>
                  <div className="h-6 flex-1 overflow-hidden rounded-md bg-slate-100">
                    <div
                      className="flex h-full items-center justify-end rounded-md bg-gradient-to-r from-indigo-500 to-sky-500 px-2 text-[11px] font-semibold text-white transition-all"
                      style={{
                        width: `${Math.max((f[k] / funnelMax) * 100, 6)}%`,
                      }}
                    >
                      {f[k]}
                    </div>
                  </div>
                </div>
              ))}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-sm font-semibold text-slate-700">
            Hızlı Erişim
          </h2>
          <div className="space-y-2">
            <QuickLink
              href={`/${locale}/leads`}
              title="Müşterileri Yönet"
              desc="Bulunan firmaları filtrele ve mesaj gönder"
            />
            <QuickLink
              href={`/${locale}/templates`}
              title="Mesaj Şablonları"
              desc="Sabit mesaj metinlerini düzenle"
            />
            <QuickLink
              href={`/${locale}/inbox`}
              title="Gelen Kutusu"
              desc="Yanıtları görüntüle ve sohbet et"
            />
            <QuickLink
              href={`/${locale}/campaigns`}
              title="Kampanyalar"
              desc="Tüm tarama kampanyalarını gör"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone: "indigo" | "sky" | "emerald" | "amber";
}) {
  const tones: Record<string, string> = {
    indigo: "from-indigo-500 to-indigo-600",
    sky: "from-sky-500 to-sky-600",
    emerald: "from-emerald-500 to-emerald-600",
    amber: "from-amber-500 to-amber-600",
  };
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {label}
        </div>
        <div
          className={`h-2.5 w-2.5 rounded-full bg-gradient-to-br ${tones[tone]}`}
        />
      </div>
      <div className="mt-2 text-3xl font-bold text-slate-900">{value}</div>
    </div>
  );
}

function QuickLink({
  href,
  title,
  desc,
}: {
  href: string;
  title: string;
  desc: string;
}) {
  return (
    <Link
      href={href}
      className="block rounded-xl border border-slate-200 p-3 transition hover:border-indigo-300 hover:bg-indigo-50/50"
    >
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      <div className="text-xs text-slate-500">{desc}</div>
    </Link>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" />
      <path className="opacity-75" d="M12 2a10 10 0 0 1 10 10" />
    </svg>
  );
}
