"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface OptOut {
  id: string;
  phone_e164: string;
  source: string;
  reason: string | null;
  created_at: string;
}
interface Report {
  total_opt_outs: number;
  by_source: Record<string, number>;
  blocked_last_30d: number;
}

export default function CompliancePage() {
  return (
    <RequireAuth>
      <AppShell>
        <ComplianceContent />
      </AppShell>
    </RequireAuth>
  );
}

function ComplianceContent() {
  const qc = useQueryClient();
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");

  const list = useQuery({
    queryKey: ["opt-outs"],
    queryFn: () => api<OptOut[]>("/api/v1/opt-outs"),
  });
  const report = useQuery({
    queryKey: ["compliance-report"],
    queryFn: () => api<Report>("/api/v1/compliance/report"),
  });

  const add = useMutation({
    mutationFn: () =>
      api("/api/v1/opt-outs", {
        method: "POST",
        body: JSON.stringify({ phone_e164: phone, source: "manual", reason }),
      }),
    onSuccess: () => {
      setPhone("");
      setReason("");
      qc.invalidateQueries({ queryKey: ["opt-outs"] });
      qc.invalidateQueries({ queryKey: ["compliance-report"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/api/v1/opt-outs/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["opt-outs"] }),
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Compliance & opt-outs</h1>

      <div className="mb-6 grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="text-xs uppercase text-slate-500">Total opt-outs</div>
          <div className="mt-1 text-2xl font-bold">
            {report.data?.total_opt_outs ?? 0}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="text-xs uppercase text-slate-500">Blocked last 30d</div>
          <div className="mt-1 text-2xl font-bold">
            {report.data?.blocked_last_30d ?? 0}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="text-xs uppercase text-slate-500">By source</div>
          <div className="mt-1 text-xs">
            {report.data &&
              Object.entries(report.data.by_source).map(([k, v]) => (
                <div key={k}>
                  {k}: {v}
                </div>
              ))}
          </div>
        </div>
      </div>

      <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold">Manual opt-out</h2>
        <div className="flex gap-2">
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+905551234567"
            className="w-64 rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            onClick={() => add.mutate()}
            disabled={!phone}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Add
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Phone</th>
              <th className="px-4 py-2">Source</th>
              <th className="px-4 py-2">Reason</th>
              <th className="px-4 py-2">Since</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {list.data?.map((o) => (
              <tr key={o.id} className="border-t">
                <td className="px-4 py-2 font-mono">{o.phone_e164}</td>
                <td className="px-4 py-2">{o.source}</td>
                <td className="px-4 py-2 text-slate-500">{o.reason ?? "-"}</td>
                <td className="px-4 py-2 text-slate-500">{o.created_at}</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => remove.mutate(o.id)}
                    className="text-xs text-red-600 underline"
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
