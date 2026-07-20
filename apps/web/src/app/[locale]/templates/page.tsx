"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Template {
  id: string;
  name: string;
  language: string;
  category: string;
  status: string;
  body: string;
  variables: string[];
  wa_template_id: string | null;
}

export default function TemplatesPage() {
  return (
    <RequireAuth>
      <AppShell>
        <TemplatesContent />
      </AppShell>
    </RequireAuth>
  );
}

function TemplatesContent() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("tr");
  const [body, setBody] = useState("");
  const [variablesText, setVariablesText] = useState("");

  const q = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<Template[]>("/api/v1/templates"),
  });

  const create = useMutation({
    mutationFn: () =>
      api<Template>("/api/v1/templates", {
        method: "POST",
        body: JSON.stringify({
          name,
          language,
          body,
          variables: variablesText
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
        }),
      }),
    onSuccess: () => {
      setName("");
      setBody("");
      setVariablesText("");
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
  });

  const patchStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api(`/api/v1/templates/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Message templates</h1>

      <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold">New template</h2>
        <div className="grid grid-cols-2 gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            {["tr", "en", "de", "ar", "ru"].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Body — use {{variable}} placeholders"
          className="mt-3 h-24 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <input
          value={variablesText}
          onChange={(e) => setVariablesText(e.target.value)}
          placeholder="Variables (comma-separated: name, company)"
          className="mt-3 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          onClick={() => create.mutate()}
          disabled={!name || !body || create.isPending}
          className="mt-3 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          Create draft
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Lang</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Body</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.map((t) => (
              <tr key={t.id} className="border-t">
                <td className="px-4 py-2 font-medium">{t.name}</td>
                <td className="px-4 py-2">{t.language}</td>
                <td className="px-4 py-2">
                  <span className="rounded-full bg-slate-100 px-2 py-1 text-xs">
                    {t.status}
                  </span>
                </td>
                <td className="max-w-md truncate px-4 py-2 text-slate-600">
                  {t.body}
                </td>
                <td className="px-4 py-2 text-right">
                  {t.status === "draft" && (
                    <button
                      onClick={() => patchStatus.mutate({ id: t.id, status: "submitted" })}
                      className="mr-2 rounded-md border border-slate-300 px-2 py-1 text-xs"
                    >
                      Submit
                    </button>
                  )}
                  {t.status === "submitted" && (
                    <button
                      onClick={() => patchStatus.mutate({ id: t.id, status: "approved" })}
                      className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs"
                    >
                      Mark approved
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
