"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useLocale } from "next-intl";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Sector {
  id: string;
  name: string;
  slug: string;
  default_language: string;
  is_active: boolean;
}

export default function SectorsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <SectorsContent />
      </AppShell>
    </RequireAuth>
  );
}

function SectorsContent() {
  const locale = useLocale();
  const qc = useQueryClient();
  const sectorsQ = useQuery({
    queryKey: ["sectors"],
    queryFn: () => api<Sector[]>("/api/v1/sectors"),
  });
  const importPreset = useMutation({
    mutationFn: () =>
      api("/api/v1/sectors/import-preset/elevator-sheave", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sectors"] }),
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Sectors</h1>
        <div className="flex gap-2">
          <button
            onClick={() => importPreset.mutate()}
            disabled={importPreset.isPending}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
          >
            {importPreset.isPending ? "..." : "Import elevator sheave preset"}
          </button>
          <Link
            href={`/${locale}/sectors/new`}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            + New sector
          </Link>
        </div>
      </div>

      {sectorsQ.isLoading && <p>Loading...</p>}
      {sectorsQ.data && sectorsQ.data.length === 0 && (
        <p className="text-slate-500">No sectors yet.</p>
      )}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Slug</th>
              <th className="px-4 py-2">Language</th>
              <th className="px-4 py-2">Active</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {sectorsQ.data?.map((s) => (
              <tr key={s.id} className="border-t">
                <td className="px-4 py-2 font-medium">{s.name}</td>
                <td className="px-4 py-2 text-slate-500">{s.slug}</td>
                <td className="px-4 py-2">{s.default_language}</td>
                <td className="px-4 py-2">{s.is_active ? "Yes" : "No"}</td>
                <td className="px-4 py-2 text-right">
                  <Link
                    href={`/${locale}/sectors/${s.id}`}
                    className="text-slate-600 underline"
                  >
                    View
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
