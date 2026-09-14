"use client";
import { useEffect, useState } from "react";
import { api, type CompanyConfig, type Turn } from "./types";
import styles from "./workspace.module.css";

export type OperationCard = {
  type: string;
  operation?: string;
  operation_id?: string;
  agent_id?: string;
  token?: string;
  company?: string;
  company_config?: CompanyConfig;
  choices?: { id: string; label: string }[];
  result?: Turn;
  title?: string;
  summary?: string;
  status?: string;
  request_id?: string;
  file_id?: string;
  filename?: string;
  batch_id?: string;
  template_name?: string;
  template_text?: string;
  buttons?: string[];
  variables?: string[];
  values?: Record<string, string>;
  consent_evidence?: string;
  recipients?: { phone: string; status: string; reason?: string }[];
  meta_limit?: number | null;
  meta_available?: boolean;
  meta_unlimited?: boolean;
  connected?: boolean;
  local_cap?: number;
  local_used?: number;
  local_remaining?: number;
  checked_at?: string;
};
const labels: Record<string, string> = {
  draft: "Hazırlanıyor",
  queued: "Kuyrukta",
  sending: "Gönderiliyor",
  accepted: "Meta kabul etti",
  sent: "Gönderildi",
  delivered: "Teslim edildi",
  read: "Okundu",
  failed: "Teslim edilemedi",
  ambiguous: "Sonuç belirsiz",
  blocked: "Gönderilemedi",
  cancelled: "İptal edildi",
  completed: "Tamamlandı",
  waiting_review: "İnceleme bekliyor",
  in_review: "İnceleniyor",
};
export function CapacityCard({ card }: { card: OperationCard }) {
  return (
    <aside className={styles.capacityCard} aria-label="WhatsApp kapasitesi">
      <div>
        <span className={styles.eyebrow}>WHATSAPP</span>
        <strong>{card.connected ? "Mesaj kapasitesi" : "Bağlantı kurulmadı"}</strong>
      </div>
      {card.connected && (
        <div className={styles.metrics}>
          <div>
            <small>Meta · portföy limiti</small>
            <b>
              {card.meta_unlimited
                ? "Sınırsız"
                : card.meta_available
                  ? card.meta_limit?.toLocaleString("tr-TR")
                  : "Alınamadı"}
            </b>
            <small>24 saat · farklı alıcı</small>
          </div>
          <div>
            <small>Uygulama · kalan yerel hak</small>
            <b>{card.local_remaining?.toLocaleString("tr-TR") ?? "—"}</b>
            <small>{card.local_used ?? 0} gönderim / rezervasyon</small>
          </div>
        </div>
      )}
      <p>{card.summary}</p>
      {card.checked_at && (
        <small>Kontrol: {new Date(card.checked_at).toLocaleString("tr-TR")}</small>
      )}
    </aside>
  );
}
export function OutboundCard({ initial }: { initial: OperationCard }) {
  const [card, setCard] = useState(initial);
  const [values, setValues] = useState(initial.values ?? {});
  const [evidence, setEvidence] = useState(initial.consent_evidence ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const draft = card.status === "draft";
  useEffect(() => {
    let active = true;
    let first = true;
    async function refresh() {
      try {
        const next = await api<OperationCard>(`admin-chat/batches/${initial.batch_id}`);
        if (active) {
          setCard(next);
          if (first || next.status !== "draft") {
            setValues(next.values ?? {});
            setEvidence(next.consent_evidence ?? "");
            first = false;
          }
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    }
    refresh();
    const timer = setInterval(refresh, 7000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [initial.batch_id]);
  async function act(action: "save" | "send" | "cancel") {
    setBusy(true);
    setError("");
    try {
      const next = await api<OperationCard>(`admin-chat/batches/${card.batch_id}/actions`, "POST", {
        action,
        variables: values,
        consent_evidence: evidence.trim() || null,
      });
      setCard(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const preview = (card.template_text ?? card.summary ?? "").replace(
    /{{\s*(\w+)\s*}}/g,
    (match, name) => values[name] || match,
  );
  return (
    <section className={styles.outboundCard} aria-label="WhatsApp gönderimi">
      <div className={styles.cardHeading}>
        <strong>WhatsApp tanıtımı</strong>
        <span className={styles.statusPill}>{labels[card.status ?? ""] ?? card.status}</span>
      </div>
      <small>{card.template_name} · Meta onaylı şablon</small>
      <blockquote className={styles.messagePreview}>
        {preview}
        {card.buttons?.map((b) => (
          <div className={styles.previewButton} key={b}>
            {b}
          </div>
        ))}
      </blockquote>
      {draft &&
        card.variables?.map((name) => (
          <label key={name}>
            Şablon alanı {name}
            <input
              maxLength={500}
              disabled={busy}
              value={values[name] ?? ""}
              onChange={(e) => setValues({ ...values, [name]: e.target.value })}
            />
          </label>
        ))}
      <details open={draft || (card.recipients?.length ?? 0) < 6}>
        <summary>{card.recipients?.length} alıcı · durumları göster</summary>
        <ul className={styles.recipientList}>
          {card.recipients?.map((r) => (
            <li key={r.phone}>
              <div>
                <strong>{r.phone}</strong>
                <span>{labels[r.status] ?? r.status}</span>
              </div>
              {r.reason && <small>{r.reason}</small>}
            </li>
          ))}
        </ul>
      </details>
      {draft && (
        <details>
          <summary>Eksik tanıtım iznini kaydet</summary>
          <p>
            Bu listedeki alıcıların WhatsApp tanıtımı almayı kabul ettiği kaynağı ve tarihi yazın.
            İletişimi durdurmuş numaralar yine engellenir.
          </p>
          <label>
            İzin kaynağı ve tarihi
            <textarea
              value={evidence}
              disabled={busy}
              maxLength={2000}
              onChange={(e) => setEvidence(e.target.value)}
              placeholder="Örn. 14.09.2026 tarihli web formu başvuruları, kayıt referansı…"
            />
          </label>
        </details>
      )}
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      <div className={styles.actions}>
        {draft && (
          <>
            <button disabled={busy} onClick={() => act("send")}>
              {busy ? "Kontrol ediliyor…" : "Uygun alıcılara gönder"}
            </button>
            <button disabled={busy} className={styles.secondary} onClick={() => act("save")}>
              Taslağı kaydet
            </button>
          </>
        )}
        {["draft", "queued"].includes(card.status ?? "") && (
          <button disabled={busy} className={styles.secondary} onClick={() => act("cancel")}>
            Gönderimi iptal et
          </button>
        )}
      </div>
      {!draft && (
        <small>Durumlar otomatik güncellenir. Belirsiz gönderimler tekrar denenmez.</small>
      )}
    </section>
  );
}
export function RecordCard({
  card,
  disabled,
  send,
}: {
  card: OperationCard;
  disabled: boolean;
  send: (s: string) => void;
}) {
  return (
    <section className={styles.recordCard}>
      <div className={styles.cardHeading}>
        <strong>{card.title ?? card.filename}</strong>
        {card.status && (
          <span className={styles.statusPill}>{labels[card.status] ?? card.status}</span>
        )}
      </div>
      {card.type === "request" && <small>Teknik teklif talebi</small>}
      <p style={{ whiteSpace: "pre-wrap" }}>{card.summary}</p>
      {card.type === "request" && card.request_id && (
        <button disabled={disabled} onClick={() => send("action:" + card.request_id)}>
          Talebi aç
        </button>
      )}
      {card.type === "file" && card.request_id && card.file_id && (
        <a
          href={`/api/platform/selection-requests/${card.request_id}/files/${card.file_id}`}
          download
        >
          {card.filename ?? "Dosyayı indir"}
        </a>
      )}
    </section>
  );
}
