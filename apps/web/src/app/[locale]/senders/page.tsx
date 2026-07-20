"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Sender {
  id: string;
  display_name: string;
  phone_number_id: string;
  tier: string;
  quality_rating: string | null;
  health_status: string;
  daily_sent: number;
  daily_cap: number;
  is_active: boolean;
}

export default function SendersPage() {
  return (
    <RequireAuth>
      <AppShell>
        <SendersContent />
      </AppShell>
    </RequireAuth>
  );
}

function SendersContent() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [pnid, setPnid] = useState("");
  const [tier, setTier] = useState("T1");

  const q = useQuery({
    queryKey: ["senders"],
    queryFn: () => api<Sender[]>("/api/v1/senders"),
  });

  const create = useMutation({
    mutationFn: () =>
      api("/api/v1/senders", {
        method: "POST",
        body: JSON.stringify({
          display_name: name,
          phone_number_id: pnid,
          tier,
          daily_cap: tier === "T1" ? 1000 : tier === "T2" ? 10000 : tier === "T3" ? 100000 : 1_000_000,
        }),
      }),
    onSuccess: () => {
      setName("");
      setPnid("");
      qc.invalidateQueries({ queryKey: ["senders"] });
    },
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Sender profiles</h1>

      <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
        <div className="grid grid-cols-3 gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Display name"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            value={pnid}
            onChange={(e) => setPnid(e.target.value)}
            placeholder="WA phone_number_id"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <select
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            {["T1", "T2", "T3", "T4"].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => create.mutate()}
          disabled={!name || !pnid}
          className="mt-3 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          Add sender
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Phone number id</th>
              <th className="px-4 py-2">Tier</th>
              <th className="px-4 py-2">Health</th>
              <th className="px-4 py-2">Sent / Cap</th>
              <th className="px-4 py-2">Active</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.map((s) => (
              <tr key={s.id} className="border-t">
                <td className="px-4 py-2 font-medium">{s.display_name}</td>
                <td className="px-4 py-2 font-mono text-xs">{s.phone_number_id}</td>
                <td className="px-4 py-2">{s.tier}</td>
                <td className="px-4 py-2">{s.health_status}</td>
                <td className="px-4 py-2">
                  {s.daily_sent} / {s.daily_cap}
                </td>
                <td className="px-4 py-2">{s.is_active ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
