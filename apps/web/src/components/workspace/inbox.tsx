"use client";
import { useCallback, useEffect, useState } from "react";
import { api, type Me } from "./types";
import styles from "./workspace.module.css";
type Conversation = { id: string; status: string; last_message_at: string | null };
type Message = { id: string; direction: string; body: string; created_at: string };
type BotState = { paused: boolean; reasons: string[]; can_resume: boolean };
export default function Inbox({ me }: { me: Me }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [state, setState] = useState<BotState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const canReply = me.user.role !== "viewer";
  const reload = useCallback(async () => {
    const cs = await api<Conversation[]>("conversations");
    setConversations(cs);
    if (selected) {
      const [ms, bs] = await Promise.all([
        api<Message[]>(`conversations/${selected}/messages`),
        api<BotState>(`conversations/${selected}/bot-state`),
      ]);
      setMessages(ms);
      setState(bs);
    }
  }, [selected]);
  useEffect(() => {
    reload().catch((e) => setError(e.message));
    const timer = setInterval(() => reload().catch(() => {}), 10000);
    return () => clearInterval(timer);
  }, [reload]);
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.section}>
      <span className={styles.eyebrow}>MÜŞTERİ KONUŞMALARI</span>
      <h1>Gelen kutusu</h1>
      <p>Yanıtları inceleyin, insan devrine alınan konuşmaları yönetin.</p>
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      <div className={styles.inboxGrid}>
        <div className={styles.card}>
          <h2>Konuşmalar</h2>
          {!conversations.length && (
            <p>
              Henüz gelen mesaj yok. WhatsApp bağlantısı kurulduğunda konuşmalar burada görünür.
            </p>
          )}
          {conversations.map((c, i) => (
            <button
              key={c.id}
              className={styles.conversation}
              aria-pressed={selected === c.id}
              onClick={() => {
                setSelected(c.id);
                setMessages([]);
                setState(null);
              }}
            >
              <strong>Konuşma {i + 1}</strong>
              <small>
                {c.status} ·{" "}
                {c.last_message_at ? new Date(c.last_message_at).toLocaleString("tr-TR") : "Yeni"}
              </small>
            </button>
          ))}
        </div>
        <div className={styles.card}>
          {!selected ? (
            <p className={styles.empty}>Bir konuşma seçin.</p>
          ) : (
            <>
              <div className={styles.titleRow}>
                <h2>{state?.paused ? "İnsan yanıtı bekliyor" : "Bot aktif"}</h2>
                {state?.can_resume && canReply && (
                  <button
                    className={styles.secondary}
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api(`conversations/${selected}/resume`, "POST", {});
                      })
                    }
                  >
                    Botu devam ettir
                  </button>
                )}
              </div>
              {state?.reasons.map((r, i) => (
                <p key={i} className={styles.notice}>
                  {r}
                </p>
              ))}
              <div className={styles.transcript}>
                {messages.map((m) => (
                  <article
                    key={m.id}
                    className={m.direction === "inbound" ? styles.userMessage : styles.botMessage}
                  >
                    <small>{m.direction === "inbound" ? "Müşteri" : "Yanıt"}</small>
                    <p>{m.body}</p>
                  </article>
                ))}
              </div>
              {canReply && (
                <form
                  className={styles.composer}
                  onSubmit={(e) => {
                    e.preventDefault();
                    const form = e.currentTarget;
                    const body = new FormData(form).get("body");
                    run(async () => {
                      await api(`conversations/${selected}/messages`, "POST", { body });
                      form.reset();
                    });
                  }}
                >
                  <label className={styles.grow}>
                    Manuel yanıt
                    <input name="body" required maxLength={4000} />
                  </label>
                  <button disabled={busy}>WhatsApp’tan gönder</button>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
