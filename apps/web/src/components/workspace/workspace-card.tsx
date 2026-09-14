"use client";
import { useState } from "react";
import AgentPanel, { Transcript } from "./agent-panel";
import AdminPanel from "./admin-panel";
import Inbox from "./inbox";
import { type Me } from "./types";
import { type OperationCard } from "./operation-cards";
import styles from "./workspace.module.css";

export default function WorkspaceCard({
  card,
  me,
  send,
  disabled,
}: {
  card: OperationCard;
  me: Me;
  send: (text: string) => Promise<void>;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  if (card.type === "agent_choices")
    return (
      <div className={styles.chatToolCard}>
        <strong>{card.title}</strong>
        <div className={styles.actions}>
          {card.choices?.map((a) => (
            <button
              key={a.id}
              disabled={disabled}
              onClick={() => send(`action:workspace:select_agent:${a.id}`)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
    );
  if (card.type === "agent_test" && card.result)
    return (
      <div className={styles.chatToolCard} aria-label="Müşteri testi sonucu">
        <strong>{card.title} · Müşteri testi</strong>
        <Transcript
          messages={[{ ...card.result, role: "assistant", content: card.result.reply ?? "" }]}
          busy={disabled}
          onAction={(id) => {
            void send(`Müşteri testinde şu mesajı dene: [${id}]`);
          }}
        />
      </div>
    );
  if (card.type === "invitation") {
    const url = new URL(window.location.pathname, window.location.origin);
    url.searchParams.set("invite", card.token ?? "");
    url.searchParams.set("company", card.company ?? "");
    return (
      <div className={styles.chatToolCard}>
        <strong>{card.title}</strong>
        <p>{card.summary}</p>
        <label>
          Davet bağlantısı · 7 gün geçerli
          <input readOnly value={url.toString()} onFocus={(e) => e.target.select()} />
        </label>
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(url.toString());
              setCopied(true);
            } catch {
              setCopyError("Bağlantıyı alandan seçip kopyalayabilirsiniz.");
            }
          }}
        >
          {copied ? "Kopyalandı" : "Bağlantıyı kopyala"}
        </button>
        {copyError && <p role="status">{copyError}</p>}
      </div>
    );
  }
  if (card.type === "workspace_preview")
    return (
      <div className={styles.chatToolCard} aria-label="İşlem önizlemesi">
        <strong>{card.title}</strong>
        <p>{card.summary}</p>
        {card.company_config && (
          <>
            <p>
              {Object.values(card.company_config.organization?.display_names ?? {}).join(" · ")}
            </p>
            {card.company_config.facts?.map((fact, index) => (
              <p key={index}>
                {Object.values((fact.customer_text ?? {}) as Record<string, string>).join(" · ")}
              </p>
            ))}
            <details>
              <summary>Yapılandırmanın tamamı</summary>
              <pre>{JSON.stringify(card.company_config, null, 2)}</pre>
            </details>
          </>
        )}
        {card.status && (
          <p role="status">{card.status === "applied" ? "Uygulandı" : "İptal edildi"}</p>
        )}
        <div className={styles.actions}>
          <button
            disabled={disabled || !!card.status}
            onClick={() => send(`action:workspace:confirm:${card.operation_id}`)}
          >
            Uygula
          </button>
          <button
            disabled={disabled || !!card.status}
            className={styles.secondary}
            onClick={() => send(`action:workspace:cancel:${card.operation_id}`)}
          >
            Vazgeç
          </button>
        </div>
      </div>
    );
  const op = card.operation ?? "knowledge";
  const view =
    (
      { agents: "knowledge", configure: "builder", create_agent: "create" } as Record<
        string,
        string
      >
    )[op] ?? op;
  const mode = ["team", "invite"].includes(op) ? "team" : "platform";
  return (
    <div className={styles.chatToolCard} aria-label={card.title}>
      <button className={styles.toolToggle} aria-expanded={open} onClick={() => setOpen(!open)}>
        <strong>{card.title}</strong>
        <span>{open ? "Daralt" : "Aç"}</span>
      </button>
      {open && (
        <div className={styles.inlineTool}>
          {["team", "invite", "platform", "create_company"].includes(op) ? (
            <AdminPanel mode={mode} me={me} initialValues={card.values} />
          ) : op === "inbox" ? (
            <Inbox me={me} />
          ) : (
            <AgentPanel
              me={me}
              initialView={view}
              initialAgentId={card.agent_id}
              initialValues={card.values}
            />
          )}
        </div>
      )}
    </div>
  );
}
