"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useLocale } from "next-intl";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Campaign {
  id: string;
  name: string;
  status: string;
  sector_id: string;
  daily_quota: number;
  created_at: string;
}

interface Sector {
  id: string;
  name: string;
}

export default function CampaignsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <CampaignsContent />
      </AppShell>
    </RequireAuth>
  );
}

function CampaignsContent() {
  const locale = useLocale();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [sectorId, setSectorId] = useState("");

  const sectorsQ = useQuery({
    queryKey: ["sectors"],
    queryFn: () => api<Sector[]>("/api/v1/sectors"),
  });
  const campaignsQ = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<Campaign[]>("/api/v1/campaigns"),
  });

  const create = useMutation({
    mutationFn: () =>
      api<Campaign>("/api/v1/campaigns", {
        method: "POST",
        body: JSON.stringify({ name, sector_id: sectorId }),
      }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  const startDiscover = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/campaigns/${id}/discover`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Campaigns</h1>

      <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold">New campaign</h2>
        <div className="flex gap-2">
          <select
            value={sectorId}
            onChange={(e) => setSectorId(e.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="">-- select sector --</option>
            {sectorsQ.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Campaign name"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            disabled={!name || !sectorId || create.isPending}
            onClick={() => create.mutate()}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Daily quota</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {campaignsQ.data?.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="px-4 py-2 font-medium">{c.name}</td>
                <td className="px-4 py-2">
                  <span className="rounded-full bg-slate-100 px-2 py-1 text-xs">
                    {c.status}
                  </span>
                </td>
                <td className="px-4 py-2">{c.daily_quota}</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => startDiscover.mutate(c.id)}
                    disabled={c.status !== "draft" && c.status !== "ready"}
                    className="mr-2 rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100 disabled:opacity-50"
                  >
                    Discover
                  </button>
                  <Link
                    href={`/${locale}/leads?campaign_id=${c.id}`}
                    className="text-slate-600 underline"
                  >
                    Leads
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
