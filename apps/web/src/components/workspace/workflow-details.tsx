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
  return (
    <section aria-label="İşlem çıktısı" className={styles.output}>
      {text("summary") && <p>{text("summary")}</p>}
      {text("delivery_note") && <p>{text("delivery_note")}</p>}
      {text("reply") && (
        <>
          <h4>Müşteri testi yanıtı</h4>
          <p>{text("reply")}</p>
          <dl>
            <div>
              <dt>Yanıt kaynağı</dt>
              <dd>
                {{ model: "Model", guided: "Menü", fallback: "Model hatasında güvenli yanıt" }[
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
