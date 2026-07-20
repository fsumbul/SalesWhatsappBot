"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api, ApiError } from "@/lib/api";

interface Contact {
  id: string;
  type: string;
  normalized_value: string;
  is_whatsapp: boolean | null;
}

interface Lead {
  id: string;
  company_name: string;
  website: string | null;
  country: string | null;
  city: string | null;
  status: string;
  fit_score: number;
  priority: string;
  source: string;
  contacts: Contact[];
}

interface Template {
  id: string;
  name: string;
  language: string;
  body: string;
}

interface Sender {
  id: string;
  display_name: string;
}

const STATUS_LABELS: Record<string, string> = {
  discovered: "Bulundu",
  enriched: "Zenginleşti",
  qualified: "Nitelikli",
  ready_to_contact: "İletişime hazır",
  contacted: "İletişim kuruldu",
  replied: "Yanıtladı",
  interested: "İlgilendi",
  won: "Kazanıldı",
  lost: "Kaybedildi",
  discarded: "Elendi",
};

const COUNTRY_LABELS: Record<string, { flag: string; label: string }> = {
  TR: { flag: "🇹🇷", label: "Türkiye" },
  DE: { flag: "🇩🇪", label: "Almanya" },
  GB: { flag: "🇬🇧", label: "İngiltere" },
  AE: { flag: "🇦🇪", label: "BAE" },
  SA: { flag: "🇸🇦", label: "S. Arabistan" },
  RU: { flag: "🇷🇺", label: "Rusya" },
  US: { flag: "🇺🇸", label: "ABD" },
  FR: { flag: "🇫🇷", label: "Fransa" },
  IT: { flag: "🇮🇹", label: "İtalya" },
  ES: { flag: "🇪🇸", label: "İspanya" },
  NL: { flag: "🇳🇱", label: "Hollanda" },
};

export default function LeadsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <LeadsContent />
      </AppShell>
    </RequireAuth>
  );
}

function LeadsContent() {
  const params = useSearchParams();
  const campaignId = params.get("campaign_id");
  const [status, setStatus] = useState("");
  const [country, setCountry] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [modalOpen, setModalOpen] = useState(false);
  const qc = useQueryClient();

  const leadsQ = useQuery({
    queryKey: ["leads", campaignId, status, country],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (campaignId) qs.set("campaign_id", campaignId);
      if (status) qs.set("status", status);
      if (country) qs.set("country", country);
      qs.set("limit", "300");
      return api<Lead[]>(`/api/v1/leads?${qs.toString()}`);
    },
  });

  const enrich = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/leads/${id}/enrich`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const leads = useMemo(() => {
    const all = leadsQ.data ?? [];
    if (!search) return all;
    const q = search.toLowerCase();
    return all.filter(
      (l) =>
        l.company_name.toLowerCase().includes(q) ||
        (l.website ?? "").toLowerCase().includes(q),
    );
  }, [leadsQ.data, search]);

  const allSelected = leads.length > 0 && selected.size === leads.length;

  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(leads.map((l) => l.id)));
  };
  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const counts = useMemo(() => {
    const all = leadsQ.data ?? [];
    return {
      total: all.length,
      qualified: all.filter((l) => l.fit_score >= 80).length,
      withContact: all.filter((l) => l.contacts.length > 0).length,
    };
  }, [leadsQ.data]);

  // Country dropdown options: curated list merged with whatever is in the data.
  const countryOptions = useMemo(() => {
    const set = new Set<string>(Object.keys(COUNTRY_LABELS));
    (leadsQ.data ?? []).forEach((l) => {
      if (l.country) set.add(l.country.toUpperCase());
    });
    if (country) set.add(country.toUpperCase());
    return Array.from(set).sort();
  }, [leadsQ.data, country]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Müşteriler</h1>
          <p className="mt-1 text-sm text-slate-500">
            {counts.total} firma • {counts.qualified} nitelikli •{" "}
            {counts.withContact} iletişimli
          </p>
        </div>
        {selected.size > 0 && (
          <button
            onClick={() => setModalOpen(true)}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700"
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
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
            {selected.size} müşteriye mesaj gönder
          </button>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
        <div className="relative flex-1 min-w-[200px]">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400"
          >
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.3-4.3" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Firma adı veya web sitesi ara…"
            className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
          />
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">Tüm durumlar</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">Tüm ülkeler</option>
          {countryOptions.map((code) => {
            const meta = COUNTRY_LABELS[code];
            return (
              <option key={code} value={code}>
                {meta ? `${meta.flag} ${meta.label}` : code}
              </option>
            );
          })}
        </select>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-10 px-4 py-3">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                />
              </th>
              <th className="px-4 py-3">Firma</th>
              <th className="px-4 py-3">Konum</th>
              <th className="px-4 py-3">İletişim</th>
              <th className="px-4 py-3">Uygunluk</th>
              <th className="px-4 py-3">Öncelik</th>
              <th className="px-4 py-3">Durum</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {leadsQ.isLoading && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-slate-400">
                  Yükleniyor…
                </td>
              </tr>
            )}
            {!leadsQ.isLoading && leads.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-slate-400">
                  Henüz müşteri yok. Panelden bir tarama başlat.
                </td>
              </tr>
            )}
            {leads.map((l) => {
              const phones = l.contacts.filter((c) => c.type === "phone");
              const emails = l.contacts.filter((c) => c.type === "email");
              const isSel = selected.has(l.id);
              return (
                <tr
                  key={l.id}
                  className={isSel ? "bg-indigo-50/60" : "hover:bg-slate-50"}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={isSel}
                      onChange={() => toggle(l.id)}
                      className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">
                      {l.company_name}
                    </div>
                    {l.website && (
                      <a
                        href={l.website}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-indigo-500 hover:underline"
                      >
                        {l.website.replace(/^https?:\/\//, "")}
                      </a>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {[l.city, l.country].filter(Boolean).join(", ") || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {phones.length > 0 && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700">
                          ☎ {phones.length}
                        </span>
                      )}
                      {emails.length > 0 && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-sky-50 px-1.5 py-0.5 text-xs font-medium text-sky-700">
                          ✉ {emails.length}
                        </span>
                      )}
                      {l.contacts.length === 0 && (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <FitBar score={l.fit_score} />
                  </td>
                  <td className="px-4 py-3">
                    <PriorityBadge priority={l.priority} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={l.status} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => enrich.mutate(l.id)}
                      className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 transition hover:bg-slate-100"
                    >
                      Yenile
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {modalOpen && (
        <SendModal
          leadIds={[...selected]}
          onClose={() => setModalOpen(false)}
          onSent={() => {
            setModalOpen(false);
            setSelected(new Set());
            qc.invalidateQueries({ queryKey: ["leads"] });
          }}
        />
      )}
    </div>
  );
}

function FitBar({ score }: { score: number }) {
  const color =
    score >= 60
      ? "from-emerald-400 to-emerald-600"
      : score >= 30
        ? "from-amber-400 to-amber-500"
        : "from-slate-300 to-slate-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs font-medium text-slate-600">{score}</span>
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const map: Record<string, string> = {
    high: "bg-rose-100 text-rose-700",
    medium: "bg-amber-100 text-amber-700",
    low: "bg-slate-100 text-slate-600",
  };
  const label: Record<string, string> = {
    high: "Yüksek",
    medium: "Orta",
    low: "Düşük",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
        map[priority] ?? map.low
      }`}
    >
      {label[priority] ?? priority}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function SendModal({
  leadIds,
  onClose,
  onSent,
}: {
  leadIds: string[];
  onClose: () => void;
  onSent: () => void;
}) {
  const [templateId, setTemplateId] = useState("");
  const [senderId, setSenderId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<Template[]>("/api/v1/templates"),
  });
  const sendersQ = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<Sender[]>("/api/v1/senders"),
  });

  const template = templatesQ.data?.find((t) => t.id === templateId);

  const send = useMutation({
    mutationFn: () =>
      api("/api/v1/outreach/enqueue", {
        method: "POST",
        body: JSON.stringify({
          lead_ids: leadIds,
          template_id: templateId,
          ...(senderId ? { sender_id: senderId } : {}),
        }),
      }),
    onSuccess: onSent,
    onError: (e) => {
      if (e instanceof ApiError) {
        const detail = (e.detail as { detail?: string })?.detail;
        setError(detail ?? `Gönderim başarısız (${e.status})`);
      } else {
        setError("Gönderim başarısız");
      }
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">
            Mesaj Gönder
          </h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-500">
          Seçili <b>{leadIds.length}</b> müşteriye sabit bir şablon mesajı
          gönderilecek.
        </p>

        <label className="mb-1 block text-xs font-medium text-slate-600">
          Şablon
        </label>
        <select
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
          className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">Şablon seç…</option>
          {templatesQ.data?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.language})
            </option>
          ))}
        </select>

        {template && (
          <div className="mb-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
            {template.body}
          </div>
        )}

        {(sendersQ.data?.length ?? 0) > 0 && (
          <>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Gönderen (opsiyonel)
            </label>
            <select
              value={senderId}
              onChange={(e) => setSenderId(e.target.value)}
              className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Otomatik</option>
              {sendersQ.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </>
        )}

        {templatesQ.data?.length === 0 && (
          <div className="mb-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-700">
            Henüz şablon yok. Önce “Şablonlar” sayfasından bir mesaj şablonu
            oluştur.
          </div>
        )}

        {error && (
          <div className="mb-3 rounded-lg bg-rose-50 p-3 text-xs text-rose-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            İptal
          </button>
          <button
            disabled={!templateId || send.isPending}
            onClick={() => {
              setError(null);
              send.mutate();
            }}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {send.isPending ? "Gönderiliyor…" : "Gönder"}
          </button>
        </div>
      </div>
    </div>
  );
}
