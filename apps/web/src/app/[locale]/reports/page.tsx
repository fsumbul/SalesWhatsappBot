"use client";

import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface FunnelData {
  discovered: number;
  enriched: number;
  qualified: number;
  contacted: number;
  replied: number;
  interested: number;
  won: number;
  lost: number;
}

interface SenderRow {
  sender_id: string;
  display_name: string;
  tier: string;
  health_status: string;
  daily_sent: number;
  daily_cap: number;
}

export default function ReportsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <ReportsContent />
      </AppShell>
    </RequireAuth>
  );
}

function ReportsContent() {
  const funnelQ = useQuery({
    queryKey: ["funnel"],
    queryFn: () => api<FunnelData>("/api/v1/reports/funnel"),
  });
  const sendersQ = useQuery({
    queryKey: ["senders-report"],
    queryFn: () => api<{ senders: SenderRow[] }>("/api/v1/reports/senders"),
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Reports</h1>

      <h2 className="mb-3 text-lg font-semibold">Funnel</h2>
      <div className="mb-8 grid grid-cols-4 gap-3">
        {funnelQ.data &&
          Object.entries(funnelQ.data).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="text-xs uppercase text-slate-500">{k}</div>
              <div className="mt-1 text-2xl font-bold">{v}</div>
            </div>
          ))}
      </div>

      <h2 className="mb-3 text-lg font-semibold">Sender health</h2>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Tier</th>
              <th className="px-4 py-2">Health</th>
              <th className="px-4 py-2">Today</th>
            </tr>
          </thead>
          <tbody>
            {sendersQ.data?.senders.map((s) => (
              <tr key={s.sender_id} className="border-t">
                <td className="px-4 py-2 font-medium">{s.display_name}</td>
                <td className="px-4 py-2">{s.tier}</td>
                <td className="px-4 py-2">{s.health_status}</td>
                <td className="px-4 py-2">
                  {s.daily_sent} / {s.daily_cap}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
