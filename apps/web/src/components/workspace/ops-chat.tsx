"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { ArrowUp, History, Plus, X } from "lucide-react";
import { motionScrollBehavior, useMotionDialog } from "../../lib/motion";
import { api, ApiError, type Me } from "./types";
import WorkflowCard, { type WorkflowView } from "./workflow-card";
import WorkspaceCard from "./workspace-card";
import AccountMenu from "./account-menu";
import styles from "./workspace.module.css";
import {
  CapacityCard,
  OutboundCard,
  RecordCard,
  TaskResultCard,
  type OperationCard,
} from "./operation-cards";
type Suggestion = { label: string; text: string };
type Message = {
  id?: string;
  sequence?: number;
  role: string;
  text: string;
  display_text?: string;
  response_source?: string;
  technical_error?: string | null;
  cards?: OperationCard[];
  suggestions?: Suggestion[];
};
type Chat = { id: string; title: string };
const examples = [
  { label: "Teklifleri görüntüle", text: "Teklif taleplerini göster" },
  { label: "Kişi ekle", text: "Kişi ekle" },
  { label: "WhatsApp gönderimi hazırla", text: "WhatsApp gönderimi hazırla" },
  { label: "WhatsApp limiti", text: "WhatsApp limiti" },
];
export default function OpsChat({
  me,
  initialSection,
  onLogout,
}: {
  me: Me;
  initialSection?: string;
  onLogout: () => void;
}) {
  const [workflows, setWorkflows] = useState<WorkflowView[]>([]);
  const [sessions, setSessions] = useState<Chat[]>([]);
  const [sid, setSid] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ text: string; id: string; sessionId: string } | null>(
    null,
  );
  const initialized = useRef(false);
  const historyMotion = useMotionDialog();
  const history = historyMotion.ref;
  const composer = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const restoredMessageIds = useRef(new Set<string>());
  const locked = useRef(false);
  const draftFlushers = useRef(new Map<string, () => Promise<boolean>>());
  function registerFlush(id: string, flush: (() => Promise<boolean>) | null) {
    if (flush) draftFlushers.current.set(id, flush);
    else draftFlushers.current.delete(id);
  }
  async function flushDrafts() {
    for (const flush of Array.from(draftFlushers.current.values())) {
      if (!(await flush())) {
        setError("İşlem kartındaki bilgiler henüz kaydedilemedi. Karttan yeniden deneyin.");
        return false;
      }
    }
    return true;
  }
  useEffect(() => {
    api<Chat[]>("admin-chat/sessions")
      .then(setSessions)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (nearBottom.current)
      end.current?.scrollIntoView({ behavior: motionScrollBehavior(), block: "nearest" });
  }, [messages, busy]);
  const visibleWorkflowIds = useRef(new Set<string>());
  useEffect(() => {
    const added = workflows.filter((view) => !visibleWorkflowIds.current.has(view.id));
    visibleWorkflowIds.current = new Set(workflows.map((view) => view.id));
    const active = added.find(
      (view) => !["paused", "completed", "cancelled"].includes(view.status),
    );
    if (active && nearBottom.current)
      document
        .getElementById(`workflow-${active.id}`)
        ?.scrollIntoView({ behavior: motionScrollBehavior(), block: "nearest" });
  }, [workflows]);
  const hasPendingDelivery = workflows.some(
    (view) =>
      (view.kind === "create_template" &&
        ["running", "paused"].includes(view.status) &&
        !!view.result.outcome) ||
      (view.kind === "conversation" && view.status === "awaiting_input") ||
      (view.kind === "reply" &&
        ["sending", "accepted", "sent", "delivered", "ambiguous", "failed"].includes(
          view.result.outcome,
        )) ||
      (view.kind === "outreach" &&
        view.result.batch_id &&
        view.status !== "cancelled" &&
        view.records?.some((record) =>
          ["Kuyrukta", "Gönderiliyor", "Meta kabul etti", "Gönderildi", "Teslim edildi"].includes(
            record.subtitle,
          ),
        )),
  );
  useEffect(() => {
    if (!sid || !hasPendingDelivery) return;
    let disposed = false;
    let fetching = false;
    const timer = window.setInterval(async () => {
      if (fetching || locked.current || document.hidden) return;
      fetching = true;
      try {
        const fresh = await api<WorkflowView[]>(`admin-chat/sessions/${sid}/workflows`);
        if (!disposed)
          setWorkflows((current) =>
            current.map((view) => {
              const next = fresh.find((candidate) => candidate.id === view.id);
              return next && next.revision > view.revision ? next : view;
            }),
          );
      } catch {
        // Keep the last confirmed state; reconnect on the next interval.
      } finally {
        fetching = false;
      }
    }, 5000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [sid, hasPendingDelivery]);
  useEffect(() => {
    const viewport = window.visualViewport;
    const resize = () =>
      document.documentElement.style.setProperty(
        "--chat-height",
        `${viewport?.height || window.innerHeight}px`,
      );
    resize();
    viewport?.addEventListener("resize", resize);
    return () => {
      viewport?.removeEventListener("resize", resize);
      document.documentElement.style.removeProperty("--chat-height");
    };
  }, []);
  async function newChat() {
    if (locked.current || pending) return;
    locked.current = true;
    const saved = await flushDrafts();
    locked.current = false;
    if (!saved) return;
    nearBottom.current = true;
    restoredMessageIds.current.clear();
    setSid("");
    setWorkflows([]);
    setMessages([]);
    setText("");
    setError("");
    historyMotion.close();
    composer.current?.focus();
  }
  async function logout() {
    if (locked.current || pending) return;
    locked.current = true;
    setBusy(true);
    try {
      if (await flushDrafts()) onLogout();
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function openChat(id: string) {
    if (locked.current || pending) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      if (!(await flushDrafts())) return;
      const rows = await api<Message[]>(`admin-chat/sessions/${id}/messages`);
      nearBottom.current = true;
      restoredMessageIds.current = new Set(rows.map((row) => row.id ?? String(row.sequence)));
      setSid(id);
      setWorkflows(await api<WorkflowView[]>(`admin-chat/sessions/${id}/workflows`));
      setMessages(rows);
      setText("");
      historyMotion.close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function send(value: string) {
    if (locked.current || (!value.trim() && !pending)) return;
    nearBottom.current = true;
    locked.current = true;
    setBusy(true);
    setError("");
    let attempt = pending;
    try {
      if (!(await flushDrafts())) return;
      let id = attempt?.sessionId || sid;
      if (!id) {
        const s = await api<Chat>("admin-chat/sessions", "POST", {});
        id = s.id;
        setSid(id);
      }
      if (!attempt) {
        attempt = { text: value.trim(), id: crypto.randomUUID(), sessionId: id };
        setPending(attempt);
        setText("");
        setMessages((rows) => [...rows, { role: "user", text: value.replace(/^action:/, "") }]);
      }
      await api(`admin-chat/sessions/${id}/turns`, "POST", {
        text: attempt.text,
        client_message_id: attempt.id,
      });
      const rows = await api<Message[]>(`admin-chat/sessions/${id}/messages`);
      setMessages(rows);
      setWorkflows(await api<WorkflowView[]>(`admin-chat/sessions/${id}/workflows`));
      setPending(null);
      // Failure to refresh the history list must not change the status of the completed turn.
      api<Chat[]>("admin-chat/sessions")
        .then(setSessions)
        .catch(() => {});
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        setPending(null);
        setText(attempt?.text.replace(/^action:/, "") ?? value);
      }
      setError((e as Error).message);
    } finally {
      locked.current = false;
      setBusy(false);
      requestAnimationFrame(() => composer.current?.focus({ preventScroll: true }));
    }
  }
  const commands: Record<string, string> = {
    agents: "Asistanlar",
    contacts: "Kişiler",
    inbox: "Gelen kutusu",
    team: "Ekip ve yetkiler",
    platform: "Şirketler",
  };
  const onNavigate = (tab: string) => {
    if (tab === "ops") {
      composer.current?.focus();
      return;
    }
    if (commands[tab]) void send("action:" + commands[tab]);
  };
  useEffect(() => {
    if (!initialized.current && initialSection === "agents") {
      initialized.current = true;
      void send("action:Asistanlar");
    }
    // Initial deep link only; subsequent operations are ordinary persisted chat turns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSection]);
  function renderWorkflow(view: WorkflowView) {
    return (
      <WorkflowCard
        key={view.id}
        view={view}
        refresh={refreshWorkflows}
        registerFlush={registerFlush}
        disabled={busy || !!pending}
      />
    );
  }
  const empty = messages.length === 0 && workflows.length === 0;
  async function refreshWorkflows() {
    setWorkflows(await api<WorkflowView[]>(`admin-chat/sessions/${sid}/workflows`));
  }
  const suggestions =
    me.user.role === "viewer"
      ? [
          { label: "Şirket bilgileri", text: "Şirket bilgileri" },
          { label: "Sürümler", text: "Sürümler" },
          { label: "Gelen kutusu", text: "Gelen kutusu" },
        ]
      : (messages.at(-1)?.suggestions ?? examples);
  const chips = (
    <div className={styles.chatChips} aria-label="Önerilen sorular">
      {suggestions.slice(0, 3).map((s) => (
        <button key={s.text} disabled={busy || !!pending} onClick={() => send("action:" + s.text)}>
          {s.label}
        </button>
      ))}
    </div>
  );
  const compose = (
    <div className={styles.chatComposeArea}>
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      {pending && !busy ? (
        <button onClick={() => send(pending.text)}>Aynı isteği güvenle yeniden dene</button>
      ) : (
        <form
          className={styles.chatCompose}
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <label>
            <span className={styles.srOnly}>Mesajınız</span>
            <textarea
              ref={composer}
              aria-label="Operasyon mesajı"
              placeholder={
                empty ? "Bugün neye bakalım?" : "Bir şey sorun veya ne yapmak istediğinizi yazın…"
              }
              rows={empty ? 2 : 1}
              maxLength={4000}
              value={text}
              disabled={busy}
              onChange={(e) => {
                setText(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 128)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(text);
                }
              }}
            />
          </label>
          <div>
            <span>{busy ? "Ashira yanıt hazırlıyor…" : "Ekibiniz için özel sohbet"}</span>
            <button
              aria-label="Gönder"
              title="Gönder"
              disabled={busy || !text.trim()}
              type="submit"
            >
              <ArrowUp size={20} aria-hidden="true" />
            </button>
          </div>
        </form>
      )}
    </div>
  );
  return (
    <main className={styles.chatShell}>
      <header className={styles.chatHeader}>
        <div>
          <button
            className={styles.chatIcon}
            aria-label="Sohbet geçmişi"
            title="Sohbet geçmişi"
            onClick={() => historyMotion.open()}
          >
            <History size={20} />
          </button>
          <span className={styles.chatWordmark}>
            ashira<span>.</span>
          </span>
          <small>Ekip asistanı</small>
        </div>
        <div>
          <button
            className={styles.chatIcon}
            aria-label="Yeni sohbet"
            title="Yeni sohbet"
            disabled={busy || !!pending}
            onClick={newChat}
          >
            <Plus size={21} />
          </button>
          <AccountMenu
            me={me}
            onNavigate={onNavigate}
            onLogout={() => {
              void logout();
            }}
          />
        </div>
      </header>
      {empty ? (
        <section className={styles.chatWelcome} data-motion="surface">
          <div>
            <h1>Bugün neye bakalım?</h1>
            <p>
              Talepleri inceleyin, işlerin durumunu sorun.
              <br className={styles.mobileBreak} /> Gerisini sohbetle ilerletin.
            </p>
            {compose}
            {chips}
          </div>
        </section>
      ) : (
        <>
          <div
            className={styles.chatMessages}
            onScroll={(event) => {
              const el = event.currentTarget;
              nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
            }}
            role="log"
            aria-label="Sohbet mesajları"
            aria-live="polite"
          >
            <div>
              {workflows.filter((view) => !view.anchor_sequence).map(renderWorkflow)}
              {messages.map((m, i) => (
                <Fragment key={m.id ?? `pending:${i}`}>
                  <article
                    data-turn-sequence={m.sequence}
                    data-motion={
                      m.role === "user"
                        ? !m.id
                          ? "arrive"
                          : undefined
                        : restoredMessageIds.current.has(m.id ?? String(m.sequence))
                          ? undefined
                          : "arrive"
                    }
                    className={m.role === "user" ? styles.chatUser : styles.chatAssistant}
                  >
                    <span className={m.role === "user" ? styles.srOnly : styles.chatSpeaker}>
                      {m.technical_error ? "Bağlantı durumu" : m.role === "user" ? "Siz" : "Ashira"}
                    </span>
                    <p role={m.technical_error ? "status" : undefined}>{m.technical_error ?? m.display_text ?? m.text}</p>
                    {m.cards?.map((c, j) =>
                      [
                        "workspace",
                        "workspace_preview",
                        "agent_choices",
                        "agent_test",
                        "invitation",
                      ].includes(c.type) ? (
                        <WorkspaceCard
                          key={`${i}:${j}`}
                          card={c}
                          me={me}
                          send={send}
                          disabled={busy || !!pending}
                        />
                      ) : c.type === "task_result" ? (
                        <TaskResultCard key={j} card={c} />
                      ) : c.type === "outbound" ? (
                        <OutboundCard key={c.batch_id} initial={c} />
                      ) : c.type === "capacity" ? (
                        <CapacityCard key={j} card={c} />
                      ) : (
                        <RecordCard key={j} card={c} disabled={busy || !!pending} send={send} />
                      ),
                    )}
                  </article>
                  {m.role === "assistant" &&
                    workflows
                      .filter((view) => view.anchor_sequence === m.sequence && !!m.sequence)
                      .map(renderWorkflow)}
                </Fragment>
              ))}
              {workflows
                .filter(
                  (view) =>
                    !!view.anchor_sequence &&
                    !messages.some(
                      (message) =>
                        message.role === "assistant" && message.sequence === view.anchor_sequence,
                    ),
                )
                .map(renderWorkflow)}
              {busy && (
                <p role="status" className={styles.chatWaiting} data-motion="waiting">
                  Yanıt hazırlanıyor…
                </p>
              )}
              <div ref={end} />
            </div>
          </div>
          <footer className={styles.chatFooter}>
            {!pending && chips}
            {compose}
          </footer>
        </>
      )}
      <dialog
        onCancel={(event) => {
          event.preventDefault();
          historyMotion.close();
        }}
        ref={history}
        aria-label="Sohbet geçmişi"
        className={styles.historyDrawer}
      >
        <div>
          <h2>Sohbet geçmişi</h2>
          <button
            className={styles.chatIcon}
            aria-label="Geçmişi kapat"
            onClick={() => historyMotion.close()}
          >
            <X size={20} />
          </button>
        </div>
        <button className={styles.historyNew} disabled={busy || !!pending} onClick={newChat}>
          <Plus size={18} /> Yeni sohbet
        </button>
        <ul>
          {sessions.map((s) => (
            <li key={s.id}>
              <button
                disabled={busy || !!pending}
                aria-current={sid === s.id ? "page" : undefined}
                onClick={() => openChat(s.id)}
              >
                {s.title.replace(/^action:/, "")}
              </button>
            </li>
          ))}
        </ul>
        {!sessions.length && <p>Sohbetleriniz burada görünecek.</p>}
      </dialog>
    </main>
  );
}
