"use client";
import { useEffect, useRef, useState } from "react";
import { MotionRegion } from "../../lib/motion/components";
import { api, ApiError } from "./types";
import { ChangeSummary, WorkflowOutput, type WorkflowChange } from "./workflow-details";
import RecordList, { type WorkflowRecord } from "./workflow-records";
import styles from "./workflow.module.css";

export type WorkflowView = {
  schema_version: 1;
  type: "workflow";
  id: string;
  session_id: string;
  anchor_sequence?: number;
  kind: string;
  revision: number;
  title: string;
  step: string;
  steps: string[];
  status: string;
  fields: Record<string, string>;
  errors: Record<string, string>;
  controls: {
    key: string;
    label: string;
    control: string;
    required: boolean;
    options: Record<string, string>;
  }[];
  primary_action: string | null;
  primary_label: string | null;
  result: Record<string, string>;
  records?: WorkflowRecord[];
  record_actions?: { operation: string; label: string }[];
  page?: number;
  has_more?: boolean;
  changes?: WorkflowChange[];
  output?: Record<string, unknown>;
};
const statuses: Record<string, string> = {
  awaiting_input: "Bilgi bekliyor",
  ready: "İncelemeye hazır",
  running: "Çalışıyor",
  completed: "Tamamlandı",
  failed: "İşlem başarısız",
  paused: "Beklemeye alındı",
  cancelled: "İptal edildi",
};
const steps: Record<string, string> = {
  details: "Bilgiler",
  compose: "Şablon ve alanlar",
  review: "İnceleme",
  result: "Sonuç",
};
export default function WorkflowCard({
  view,
  refresh,
  disabled = false,
  registerFlush,
}: {
  view: WorkflowView;
  refresh: () => Promise<void>;
  disabled?: boolean;
  registerFlush?: (id: string, flush: (() => Promise<boolean>) | null) => void;
}) {
  const [fields, setFields] = useState(view.fields);
  const [busy, setBusy] = useState(false);
  const [autosaving, setAutosaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const lock = useRef(false);
  const saveWaiters = useRef<((success: boolean) => void)[]>([]);
  const retry = useRef<{
    action: string;
    fields: Record<string, string>;
    expected_revision: number;
    client_operation_id: string;
  } | null>(null);
  const observedRevision = useRef(-1);
  const previousFields = useRef(view.fields);
  const submittedFields = useRef<Record<string, string> | null>(null);
  const flushLatest = useRef<() => Promise<boolean>>(async () => true);
  useEffect(() => {
    if (observedRevision.current === view.revision) return;
    observedRevision.current = view.revision;
    const baseline = submittedFields.current ?? previousFields.current;
    setFields((current) => {
      const merged = { ...view.fields };
      for (const key of Object.keys(current)) {
        if (current[key] !== baseline[key]) merged[key] = current[key];
      }
      return merged;
    });
    previousFields.current = view.fields;
    submittedFields.current = null;
  }, [view.revision, view.fields]);
  const terminal = ["completed", "cancelled"].includes(view.status);
  const editable =
    !terminal &&
    view.status !== "paused" &&
    ["details", "compose"].includes(view.step) &&
    view.status !== "running";
  async function act(action: string, overrides?: Record<string, string>, automatic = false) {
    if (lock.current) return false;
    lock.current = true;
    let succeeded = false;
    setBusy(true);
    setAutosaving(automatic);
    setError("");
    setSaved(false);
    const body = retry.current ?? {
      action,
      fields:
        ["update", "continue"].includes(action) || (action === "pause" && editable)
          ? { ...fields, ...overrides }
          : (overrides ?? {}),
      expected_revision: view.revision,
      client_operation_id: crypto.randomUUID(),
    };
    retry.current = body;
    submittedFields.current = body.fields;
    try {
      await api(
        `admin-chat/sessions/${view.session_id}/workflows/${view.id}/actions`,
        "POST",
        body,
      );
      retry.current = null;
      await refresh();
      setSaved(true);
      succeeded = true;
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        retry.current = null;
        if (
          e.status === 409 &&
          e.detail &&
          typeof e.detail === "object" &&
          "workflow" in e.detail
        ) {
          await refresh();
          setError("Kart güncellendi. Bilgileri inceleyip yeniden deneyin.");
        } else setError(e.message);
      } else setError("Bağlantı kesildi. Aynı işlemi güvenle yeniden deneyebilirsiniz.");
      return false;
    } finally {
      lock.current = false;
      setBusy(false);
      setAutosaving(false);
      for (const resolve of saveWaiters.current.splice(0)) resolve(succeeded);
    }
  }
  const dirty = JSON.stringify(fields) !== JSON.stringify(view.fields);
  useEffect(() => {
    flushLatest.current = async () => {
      if (lock.current) {
        const success = await new Promise<boolean>((resolve) => saveWaiters.current.push(resolve));
        if (!success) return false;
        // Let the confirmed revision and edits made during the request render first.
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        return flushLatest.current();
      }
      if (retry.current) return act(retry.current.action);
      if (editable && dirty) return act("update");
      return true;
    };
    registerFlush?.(view.id, () => flushLatest.current());
    return () => registerFlush?.(view.id, null);
  });
  useEffect(() => {
    if (!editable || !dirty || busy || disabled || error || retry.current) return;
    const timer = window.setTimeout(() => {
      void act("update", undefined, true);
    }, 800);
    return () => window.clearTimeout(timer);
  });
  useEffect(() => {
    if (!dirty && !busy && !retry.current) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  const messaging = ["outreach", "create_template"].includes(view.kind);
  return (
    <section
      id={`workflow-${view.id}`}
      data-motion="arrive"
      className={`${styles.card} ${messaging ? styles.messagingCard : ""}`}
      aria-label={view.title}
      aria-busy={busy}
    >
      <header>
        <h3>
          {messaging
            ? view.kind === "outreach"
              ? "WhatsApp mesajı"
              : "Yeni WhatsApp şablonu"
            : view.title}
        </h3>
        <span>
          {view.kind === "outreach" && view.status === "paused" && view.result.outcome === "queued"
            ? "Arka planda çalışıyor"
            : view.kind === "reply" && ["ambiguous", "failed"].includes(view.result.outcome)
              ? "Sonuç kontrol edilmeli"
              : view.kind === "create_template" && view.status === "running"
                ? view.result.meta_status === "PENDING"
                  ? "Meta onayı bekleniyor"
                  : "Meta durumu kontrol ediliyor"
                : statuses[view.status]}
        </span>
      </header>
      <MotionRegion className={messaging && !terminal ? styles.messageLayout : undefined}>
        {!terminal && view.output && <WorkflowOutput output={view.output} />}
        {!terminal &&
          (!messaging || view.status === "failed" || Object.keys(view.errors).length > 0) &&
          view.result.message && <p role="status">{view.result.message}</p>}
        {!terminal && !messaging && view.steps.length > 0 && (
          <ol className={styles.steps} aria-label="İşlem adımları">
            {view.steps.map((step, i) => (
              <li key={step} aria-current={step === view.step ? "step" : undefined}>
                {i + 1}. {steps[step]}
              </li>
            ))}
          </ol>
        )}
        {terminal ? (
          <>
            <p role="status">{view.result.message ?? "İşlem iptal edildi."}</p>
            <details>
              <summary>İşlem ayrıntıları</summary>
              {view.output && <WorkflowOutput output={view.output} />}
              {view.changes && <ChangeSummary changes={view.changes} />}
              <dl>
                {view.controls.map(
                  (c) =>
                    view.fields[c.key] && (
                      <div key={c.key}>
                        <dt>{c.label}</dt>
                        <dd>{c.options[view.fields[c.key]] ?? view.fields[c.key]}</dd>
                      </div>
                    ),
                )}
              </dl>
            </details>
            {view.result.token && (
              <button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(
                      `${location.origin}/${location.pathname.split("/")[1] || "tr"}?invite=${encodeURIComponent(view.result.token)}`,
                    );
                    setCopied(true);
                  } catch {
                    setError("Bağlantı kopyalanamadı.");
                  }
                }}
              >
                Davet bağlantısını kopyala
              </button>
            )}
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (view.primary_action) void act(view.primary_action);
            }}
            noValidate
          >
            {messaging && !editable && view.status !== "paused" ? (
              <section className={styles.reviewSummary} aria-label="Gönderim bilgileri">
                <h4>{view.kind === "outreach" ? "Gönderim bilgileri" : "Şablon bilgileri"}</h4>
                <dl>
                  {view.controls
                    .filter((c) => fields[c.key])
                    .map((c) => (
                      <div key={c.key}>
                        <dt>
                          {c.key === "recipients"
                            ? "Alıcılar"
                            : c.key === "template"
                              ? "Şablon"
                              : c.label}
                        </dt>
                        <dd>{c.options[fields[c.key]] ?? fields[c.key]}</dd>
                      </div>
                    ))}
                </dl>
              </section>
            ) : (
              <details
                className={view.status === "paused" ? styles.pausedDetails : styles.openDetails}
                open={view.status !== "paused"}
              >
                <summary>Kaydedilmiş bilgiler</summary>
                {view.kind === "configure" &&
                  ["json", "csv"].includes(fields.format) &&
                  editable && (
                    <label className={styles.fileUpload}>
                      JSON / CSV dosyası seç
                      <input
                        type="file"
                        accept=".json,.csv"
                        disabled={busy || disabled}
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          if (file.size > 500000) {
                            setError("Dosya en fazla 500 KB olabilir.");
                            return;
                          }
                          try {
                            const content = await file.text();
                            setFields({ ...fields, content });
                            setSaved(false);
                          } catch {
                            setError("Dosya okunamadı.");
                          }
                        }}
                      />
                    </label>
                  )}
                <fieldset
                  disabled={
                    disabled ||
                    (busy && !autosaving) ||
                    !editable ||
                    (!!retry.current && !autosaving)
                  }
                  className={styles.fields}
                >
                  {view.controls.map((c) => (
                    <div key={c.key}>
                      <label htmlFor={`${view.id}-${c.key}`}>
                        {c.label}
                        {c.required && <span aria-hidden="true"> *</span>}
                      </label>
                      {c.control === "select" ? (
                        <select
                          id={`${view.id}-${c.key}`}
                          value={fields[c.key] ?? ""}
                          aria-required={c.required}
                          aria-invalid={!!view.errors[c.key]}
                          aria-describedby={
                            view.errors[c.key] ? `${view.id}-${c.key}-error` : undefined
                          }
                          onChange={(e) => {
                            setFields({ ...fields, [c.key]: e.target.value });
                            setSaved(false);
                            setError("");
                          }}
                        >
                          {!("" in c.options) && <option value="">Seçin</option>}
                          {Object.entries(c.options).map(([v, l]) => (
                            <option key={v} value={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                      ) : c.control === "textarea" ? (
                        <textarea
                          id={`${view.id}-${c.key}`}
                          rows={c.key === "content" ? 5 : 2}
                          value={fields[c.key] ?? ""}
                          aria-required={c.required}
                          aria-invalid={!!view.errors[c.key]}
                          aria-describedby={
                            view.errors[c.key] ? `${view.id}-${c.key}-error` : undefined
                          }
                          onChange={(e) => {
                            setFields({ ...fields, [c.key]: e.target.value });
                            setSaved(false);
                            setError("");
                          }}
                        />
                      ) : (
                        <input
                          id={`${view.id}-${c.key}`}
                          type={c.control}
                          value={fields[c.key] ?? ""}
                          aria-required={c.required}
                          aria-invalid={!!view.errors[c.key]}
                          aria-describedby={
                            view.errors[c.key] ? `${view.id}-${c.key}-error` : undefined
                          }
                          onChange={(e) => {
                            setFields({ ...fields, [c.key]: e.target.value });
                            setSaved(false);
                            setError("");
                          }}
                        />
                      )}
                      {view.errors[c.key] && (
                        <small id={`${view.id}-${c.key}-error`}>{view.errors[c.key]}</small>
                      )}
                    </div>
                  ))}
                </fieldset>
              </details>
            )}
            {view.changes && <ChangeSummary changes={view.changes} />}
            {view.step === "review" && (
              <p>
                {view.kind === "create_template"
                  ? "Önizlemedeki şablon Meta onayına gönderilecek. Yalnız Meta onayladığında mesaj gönderiminde seçilebilir."
                  : view.kind === "rollback"
                    ? "Seçilen geçmiş sürümden yeni bir canlı sürüm oluşturulacak. Sürüm geçmişi korunacak."
                    : view.kind === "owner_invite"
                      ? "Seçilen şirket için şirket sahibi davet bağlantısı oluşturulacak. Üyelik davet kabul edildiğinde başlar."
                      : view.kind === "request_update"
                        ? "Gösterilen talep değişikliği kaydedilecek."
                        : view.kind === "member"
                          ? "Gösterilen hesabın erişimi bu değişiklikle güncellenecek."
                          : view.kind === "configure"
                            ? "Onaylanan değişiklikler taslağa kaydedilecek. Yayınlama ayrı bir işlemdir."
                            : view.kind === "publish"
                              ? "Gösterilen sürüm yeni müşteri mesajları için canlıya alınacak."
                              : view.kind === "reply"
                                ? "Gösterilen yanıt bu müşteriye bir kez gönderilecek. Sonuç belirsiz kalırsa otomatik tekrar yapılmaz."
                                : view.kind === "resume_bot"
                                  ? "Bot yeni müşteri mesajları için devam edecek. Eski mesajlar yeniden gönderilmez."
                                  : view.kind === "outreach"
                                    ? "Gönderim başlatıldıktan sonra teslim durumunu buradan takip edebilirsiniz."
                                    : view.kind === "test"
                                      ? "Seçilen sürümle müşteri testi çalıştırılacak."
                                      : view.kind === "knowledge"
                                        ? "Web sitesi bilgi kaynağı olarak eklenecek ve robots.txt'e uyularak taranacak. Güvenli bilgiler otomatik yayınlanır; korumalı konular ve yeni ürünler onayınızı bekler."
                                        : view.kind === "knowledge_review"
                                          ? "Seçilen bilgi veya görsel için karar uygulanacak. Yayından kaldırma yeni bir canlı sürüm oluşturur."
                                      : view.kind === "invite"
                                        ? "Bu e-posta için seçilen yetkiyle davet bağlantısı oluşturulacak. Üyelik davet kabul edildiğinde başlar."
                                        : view.kind === "create_company"
                                          ? "Bu bilgilerle şirket ve sahibinin davet bağlantısı oluşturulacak."
                                          : view.kind === "create_agent"
                                            ? "Bu bilgilerle asistan ve ilk taslak sürümü oluşturulacak."
                                            : "Bu bilgilerle kişi kaydı oluşturulacak."}
              </p>
            )}
            <div className={styles.actions}>
              {retry.current && !busy ? (
                <button
                  type="button"
                  disabled={busy || disabled}
                  onClick={() => act(retry.current!.action)}
                >
                  Aynı işlemi yeniden dene
                </button>
              ) : (
                <>
                  {view.primary_action && (
                    <button type="submit" disabled={busy || disabled}>
                      {messaging && view.kind === "outreach" && view.step === "review"
                        ? "Gönderimi başlat"
                        : view.primary_label}
                    </button>
                  )}
                  {view.kind === "outreach" && editable && (
                    <button
                      type="button"
                      disabled={busy || disabled || dirty}
                      onClick={() => act("launch", { operation: "create_template" })}
                    >
                      Başka şablon ekle
                    </button>
                  )}
                  {editable && !messaging && view.kind !== "records" && (
                    <button type="button" disabled={busy || disabled} onClick={() => act("update")}>
                      Bilgileri sakla
                    </button>
                  )}
                  {view.step === "review" && view.status !== "paused" && (
                    <button type="button" disabled={busy || disabled} onClick={() => act("back")}>
                      Bilgileri düzenle
                    </button>
                  )}
                  {view.status !== "paused" &&
                    !(view.kind === "create_template" && view.status === "running") && (
                      <button
                        type="button"
                        disabled={busy || disabled}
                        onClick={() => act("pause")}
                      >
                        {view.kind === "outreach" && view.status === "running"
                          ? "Arka planda sürdür"
                          : "Beklet"}
                      </button>
                    )}
                  {!(view.kind === "create_template" && view.status === "running") && (
                    <button type="button" disabled={busy || disabled} onClick={() => act("cancel")}>
                      İptal et
                    </button>
                  )}
                </>
              )}
            </div>
          </form>
        )}
      </MotionRegion>
      {messaging && !terminal && view.result.message && view.step === "result" && (
        <p role="status">{view.result.message}</p>
      )}
      {((["records", "conversation"].includes(view.kind) &&
        !terminal &&
        view.status !== "paused") ||
        (["outreach", "contact"].includes(view.kind) && !!view.records?.length)) && (
        <RecordList
          records={view.records ?? []}
          actions={view.record_actions ?? []}
          page={view.page ?? 1}
          hasMore={view.has_more ?? false}
          disabled={busy || disabled}
          launch={(operation, record) => {
            void act("launch", { operation, ...(record ? { record } : {}) });
          }}
          changePage={(page) => {
            void act("continue", { page: String(page) });
          }}
        />
      )}
      <p role="status" className={styles.feedback} data-motion="feedback">
        {busy
          ? "Kaydediliyor…"
          : copied
            ? "Davet bağlantısı kopyalandı"
            : saved && !dirty
              ? "Kaydedildi"
              : ""}
      </p>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
