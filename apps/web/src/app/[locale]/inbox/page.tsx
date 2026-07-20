"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";
import { api } from "@/lib/api";

interface Conversation {
  id: string;
  lead_id: string;
  contact_id: string;
  status: string;
  last_message_at: string | null;
  unread_count: number;
}
interface Message {
  id: string;
  direction: "outbound" | "inbound";
  message_type: string;
  body: string | null;
  created_at: string;
}

export default function InboxPage() {
  return (
    <RequireAuth>
      <AppShell>
        <InboxContent />
      </AppShell>
    </RequireAuth>
  );
}

function InboxContent() {
  const [activeId, setActiveId] = useState<string | null>(null);

  const convQ = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<Conversation[]>("/api/v1/conversations"),
    refetchInterval: 15_000,
  });

  return (
    <div className="grid grid-cols-3 gap-4">
      <div className="col-span-1 overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-3 text-sm font-semibold">
          Conversations
        </div>
        <ul className="max-h-[70vh] overflow-y-auto">
          {convQ.data?.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => setActiveId(c.id)}
                className={`flex w-full flex-col items-start border-b border-slate-100 px-3 py-2 text-left text-sm hover:bg-slate-50 ${
                  activeId === c.id ? "bg-slate-100" : ""
                }`}
              >
                <span className="truncate font-medium">{c.contact_id.slice(0, 8)}...</span>
                <span className="text-xs text-slate-500">
                  {c.last_message_at ?? "no messages"}
                  {c.unread_count > 0 && (
                    <span className="ml-2 rounded-full bg-red-500 px-2 text-xs text-white">
                      {c.unread_count}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="col-span-2 overflow-hidden rounded-lg border border-slate-200 bg-white">
        {activeId ? (
          <MessagePane convId={activeId} />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-400">
            Select a conversation
          </div>
        )}
      </div>
    </div>
  );
}

function MessagePane({ convId }: { convId: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");

  const msgQ = useQuery({
    queryKey: ["messages", convId],
    queryFn: () => api<Message[]>(`/api/v1/conversations/${convId}/messages`),
    refetchInterval: 10_000,
  });

  const send = useMutation({
    mutationFn: () =>
      api(`/api/v1/conversations/${convId}/messages`, {
        method: "POST",
        body: JSON.stringify({ body: text }),
      }),
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: ["messages", convId] });
    },
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-2 overflow-y-auto p-4">
        {msgQ.data?.map((m) => (
          <div
            key={m.id}
            className={`max-w-md rounded-lg px-3 py-2 text-sm ${
              m.direction === "outbound"
                ? "ml-auto bg-emerald-100"
                : "bg-slate-100"
            }`}
          >
            <div>{m.body}</div>
            <div className="mt-1 text-[10px] text-slate-500">
              {m.direction} · {m.created_at}
            </div>
          </div>
        ))}
      </div>
      <div className="border-t border-slate-200 p-3">
        <div className="flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Reply..."
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            onClick={() => send.mutate()}
            disabled={!text}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
