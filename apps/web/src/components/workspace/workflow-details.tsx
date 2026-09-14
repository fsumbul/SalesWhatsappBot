import { MessageCircle, LockKeyhole, CornerUpLeft } from "lucide-react";
import styles from "./workflow.module.css";
export type WorkflowChange = { label: string; before: string; after: string };
export function ChangeSummary({ changes }: { changes: WorkflowChange[] }) {
  if (!changes.length) return null;
  return (
    <section aria-label="Değişiklik özeti" className={styles.changes}>
      <h4>Değişiklikler</h4>
      {changes.map((change, i) => (
        <details key={i} open={changes.length < 5}>
          <summary>{change.label}</summary>
          <dl>
            <div>
              <dt>Önce</dt>
              <dd>{change.before}</dd>
            </div>
            <div>
              <dt>Sonra</dt>
              <dd>{change.after}</dd>
            </div>
          </dl>
        </details>
      ))}
    </section>
  );
}
export function WorkflowOutput({ output }: { output: Record<string, unknown> }) {
  if (!Object.keys(output).length) return null;
  const text = (key: string) => (typeof output[key] === "string" ? String(output[key]) : "");
  const preview = output.template_preview as
    | {
        name?: string;
        language?: string;
        category?: string;
        status?: string;
        header?: string;
        body?: string;
        footer?: string;
        buttons?: string[];
      }
    | undefined;
  return (
    <section aria-label="İşlem çıktısı" className={styles.output}>
      {!preview && text("summary") && <p>{text("summary")}</p>}
      {!preview && text("delivery_note") && <p>{text("delivery_note")}</p>}
      {preview && (
        <section className={styles.messagePreview} aria-label="WhatsApp mesaj önizlemesi">
          <div className={styles.previewHeading}>
            <span>
              <MessageCircle size={16} aria-hidden="true" /> WhatsApp
            </span>
            <span className={styles.previewBadge}>Önizleme</span>
          </div>
          <div className={styles.chatCanvas}>
            <div className={styles.messageBubble}>
              <div className={styles.messageText}>
                {preview.header && <strong>{preview.header}</strong>}
                <div>{preview.body}</div>
                {preview.footer && <small>{preview.footer}</small>}
              </div>
              {!!preview.buttons?.length && (
                <div className={styles.messageButtons} aria-label="Mesajdaki düğmeler">
                  {preview.buttons.map((label, i) => (
                    <div key={i}>
                      <CornerUpLeft size={15} aria-hidden="true" />
                      {label}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className={styles.previewCaption}>
            <LockKeyhole size={13} aria-hidden="true" />
            <span>{preview.status || "Şablon taslağı"}</span>
          </div>
          <details className={styles.templateMetadata}>
            <summary>Şablon bilgileri</summary>
            <p>{[preview.name, preview.language, preview.category].filter(Boolean).join(" · ")}</p>
          </details>
        </section>
      )}
      {(text("reply") || text("answer_origin")) && (
        <>
          <h4>{text("reply") ? "Müşteri testi yanıtı" : "Müşteri testi durumu"}</h4>
          <p role={text("reply") ? undefined : "status"}>{text("reply") || "Model yanıtı üretilemedi veya doğrulanamadı."}</p>
          <dl>
            <div>
              <dt>Yanıt kaynağı</dt>
              <dd>
                {{ model_generated: "Modelden üretildi", model_unavailable: "Model kullanılamıyor", verification_failed: "Doğrulama başarısız" }[text("answer_origin")] ||
                { model: "Model", guided: "Menü", fallback: "Eski sürüm yedek yanıtı" }[
                  text("response_source")
                ] ||
                  text("response_source") ||
                  "Belirtilmedi"}
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{text("model") || "Belirtilmedi"}</dd>
            </div>
            <div>
              <dt>Sürüm</dt>
              <dd>{String(output.version ?? "Belirtilmedi")}</dd>
            </div>
            {typeof output.latency_ms === "number" && (
              <div>
                <dt>Süre</dt>
                <dd>{output.latency_ms} ms</dd>
              </div>
            )}
            {text("fallback_reason") && (
              <div>
                <dt>Model hatası</dt>
                <dd>{text("fallback_reason")}</dd>
              </div>
            )}
          </dl>
          <p>WhatsApp mesajı gönderilmedi.</p>
        </>
      )}
    </section>
  );
}
