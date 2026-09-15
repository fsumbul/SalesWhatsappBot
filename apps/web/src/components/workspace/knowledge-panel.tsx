"use client";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  apiUpload,
  type KnowledgeCandidate,
  type KnowledgeMedia,
  type KnowledgeSource,
} from "./types";
import styles from "./workspace.module.css";

const STATUS: Record<string, string> = {
  idle: "Hazır",
  queued: "Kuyrukta",
  running: "Taranıyor",
  failed: "Hata",
  pending: "Onay bekliyor",
  staged: "Taslakta, gizli",
  auto_published: "Otomatik yayında",
  accepted: "Onaylandı",
  rejected: "Reddedildi",
  revoked: "Kaldırıldı",
  published: "Yayında",
};

function label(status: string) {
  return STATUS[status] ?? status;
}

export default function KnowledgePanel({
  agentId,
  canEdit,
  isOwner,
}: {
  agentId: string;
  canEdit: boolean;
  isOwner: boolean;
}) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [candidates, setCandidates] = useState<KnowledgeCandidate[]>([]);
  const [media, setMedia] = useState<KnowledgeMedia[]>([]);
  const [url, setUrl] = useState("");
  const [autoPublish, setAutoPublish] = useState(true);
  const [filter, setFilter] = useState("pending");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    if (!agentId) return;
    const [s, c, m] = await Promise.all([
      api<KnowledgeSource[]>(`knowledge/sources?agent_id=${agentId}`),
      api<KnowledgeCandidate[]>(`knowledge/candidates?agent_id=${agentId}&limit=200`),
      api<KnowledgeMedia[]>(`knowledge/media?agent_id=${agentId}&limit=100`),
    ]);
    setSources(s);
    setCandidates(c);
    setMedia(m);
  }, [agentId]);

  useEffect(() => {
    reload().catch((e) => setError(e.message));
    const timer = setInterval(() => reload().catch(() => undefined), 15000);
    return () => clearInterval(timer);
  }, [reload]);

  async function run(task: () => Promise<string | void>) {
    setBusy(true);
    setError("");
    try {
      const message = await task();
      if (message) setNotice(message);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const visibleCandidates = candidates.filter((c) =>
    filter === "all" ? true : filter === "pending" ? ["pending", "staged"].includes(c.review_status) : c.review_status === filter,
  );

  return (
    <div className={styles.grid}>
      <div className={styles.card}>
        <h2>Bilgi kaynakları</h2>
        <p>
          Web sitenizi veya kataloglarınızı ekleyin. Bulunan ürün bilgileri güvenli olduğunda
          otomatik yayınlanır; fiyat, stok, teslimat, garanti gibi konular ve yeni ürünler onayınızı
          bekler. Her şey tek adımda geri alınabilir.
        </p>
        {canEdit && (
          <>
            <form
              className={styles.inlineForm}
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await api("knowledge/sources", "POST", {
                    agent_id: agentId,
                    url,
                    auto_publish: autoPublish,
                    sync_now: true,
                  });
                  setUrl("");
                  return "Web sitesi eklendi ve tarama kuyruğa alındı.";
                });
              }}
            >
              <input
                type="url"
                placeholder="https://www.sirketiniz.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
                disabled={busy}
              />
              <label>
                <input
                  type="checkbox"
                  checked={autoPublish}
                  onChange={(e) => setAutoPublish(e.target.checked)}
                />{" "}
                Güvenli bilgileri otomatik yayınla
              </label>
              <button disabled={busy || !url}>Web sitesini ekle</button>
            </form>
            <label className={styles.inlineForm}>
              Katalog / doküman yükle (PDF, XLSX, CSV, Markdown)
              <input
                type="file"
                accept=".pdf,.xlsx,.csv,.md,.txt,.html"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  if (file.size > 20 * 1024 * 1024) {
                    setError("Dosya en fazla 20 MB olabilir.");
                    return;
                  }
                  run(async () => {
                    const form = new FormData();
                    form.append("agent_id", agentId);
                    form.append("file", file);
                    form.append("auto_publish", String(autoPublish));
                    const result = await apiUpload<{ created: boolean; queued: boolean }>(
                      "knowledge/documents",
                      form,
                    );
                    e.target.value = "";
                    return result.created
                      ? "Doküman yüklendi ve çıkarım kuyruğa alındı."
                      : "Bu doküman daha önce yüklenmişti.";
                  });
                }}
              />
            </label>
          </>
        )}
        {error && <p className={styles.notice}>{error}</p>}
        {notice && !error && <p className={styles.notice}>{notice}</p>}
        <ul>
          {sources.map((s) => (
            <li key={s.id} className={styles.recordCard}>
              <strong>{s.display_name}</strong>{" "}
              <span className={styles.statusPill}>{label(s.status)}</span>
              <small>
                {" "}
                {s.kind === "website" ? s.canonical_uri : "Doküman"} ·{" "}
                {s.last_sync_at
                  ? "Son tarama " + new Date(s.last_sync_at).toLocaleString("tr-TR")
                  : "Henüz taranmadı"}
                {s.stats && typeof s.stats.candidate_facts === "number"
                  ? ` · ${String(s.stats.candidate_facts)} bilgi adayı`
                  : ""}
                {s.last_error ? ` · ${s.last_error}` : ""}
              </small>
              {canEdit && (
                <span className={styles.actions}>
                  <button
                    className={styles.secondary}
                    disabled={busy || s.status === "running"}
                    onClick={() =>
                      run(async () => {
                        await api(`knowledge/sources/${s.id}/sync`, "POST", {});
                        return "Yeniden tarama kuyruğa alındı.";
                      })
                    }
                  >
                    Yeniden tara
                  </button>
                  <button
                    className={styles.secondary}
                    disabled={busy}
                    onClick={() => {
                      if (!window.confirm("Bu kaynak ve ondan yayınlanan tüm bilgiler kaldırılsın mı?")) return;
                      run(async () => {
                        await api(`knowledge/sources/${s.id}`, "DELETE");
                        return "Kaynak ve yayınladığı bilgiler kaldırıldı.";
                      });
                    }}
                  >
                    Kaldır
                  </button>
                </span>
              )}
            </li>
          ))}
          {!sources.length && <li>Henüz bilgi kaynağı eklenmedi.</li>}
        </ul>
      </div>

      <div className={styles.card}>
        <div className={styles.titleRow}>
          <h2>Bulunan bilgiler</h2>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="pending">Onay bekleyenler</option>
            <option value="auto_published">Otomatik yayınlananlar</option>
            <option value="accepted">Onaylananlar</option>
            <option value="revoked">Kaldırılanlar</option>
            <option value="rejected">Reddedilenler</option>
            <option value="all">Tümü</option>
          </select>
        </div>
        <ul>
          {visibleCandidates.map((c) => (
            <li key={c.id} className={styles.recordCard}>
              <span className={styles.statusPill}>{label(c.review_status)}</span>{" "}
              {c.protected && <span className={styles.badge}>korumalı konu</span>}{" "}
              <strong>{c.kind === "offering" ? "Yeni ürün: " : ""}</strong>
              {c.kind === "offering"
                ? String(c.payload.name ?? c.subject_id)
                : String(c.payload.customer_text ?? "")}
              <small>
                {" "}
                · {c.subject_id ?? "şirket"} · {c.category ?? "ürün"} · güven{" "}
                {Math.round(c.confidence * 100)}%
                {c.evidence?.locator ? ` · kaynak: ${c.evidence.locator}` : ""}
                {c.evidence?.quote ? ` · "${c.evidence.quote}"` : ""}
                {c.error ? ` · ${c.error}` : ""}
              </small>
              {canEdit && !["rejected", "revoked"].includes(c.review_status) && (
                <span className={styles.actions}>
                  {["pending", "staged"].includes(c.review_status) && (
                    <button
                      disabled={busy || (c.protected && !isOwner && c.kind === "fact")}
                      title={
                        c.protected && !isOwner
                          ? "Korumalı konuları yalnızca şirket sahibi görünür yapabilir"
                          : undefined
                      }
                      onClick={() =>
                        run(async () => {
                          await api(`knowledge/candidates/${c.id}/accept`, "POST", {});
                          return "Onaylandı ve yayınlandı.";
                        })
                      }
                    >
                      Onayla
                    </button>
                  )}
                  {c.published_ref && (
                    <button
                      className={styles.secondary}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api(`knowledge/candidates/${c.id}/revoke`, "POST", {
                            reason: "panelden kaldırıldı",
                          });
                          return "Yayından kaldırıldı.";
                        })
                      }
                    >
                      Yayından kaldır
                    </button>
                  )}
                  <button
                    className={styles.secondary}
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api(`knowledge/candidates/${c.id}/reject`, "POST", {
                          reason: "panelden reddedildi",
                        });
                        return "Reddedildi.";
                      })
                    }
                  >
                    Reddet
                  </button>
                </span>
              )}
            </li>
          ))}
          {!visibleCandidates.length && <li>Bu filtrede kayıt yok.</li>}
        </ul>
      </div>

      <div className={styles.card}>
        <h2>Ürün görselleri</h2>
        <p>Web sitesinden bulunan görseller ürüne bağlanır ve WhatsApp&apos;ta ürün cevaplarıyla gönderilir.</p>
        <ul className={styles.grid}>
          {media.map((m) => (
            <li key={m.id} className={styles.recordCard}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`/api/platform/knowledge/media/${m.id}/preview`}
                alt={m.alt_text ?? m.subject_id ?? "ürün görseli"}
                width={160}
                height={Math.round((160 * m.height) / Math.max(m.width, 1))}
                style={{ maxWidth: "100%", height: "auto", borderRadius: 8 }}
              />
              <div>
                <span className={styles.statusPill}>{label(m.status)}</span>{" "}
                <small>
                  {m.subject_id ?? "ürün seçilmedi"} · {m.width}×{m.height}
                </small>
              </div>
              {canEdit && (
                <span className={styles.actions}>
                  {m.status === "pending" && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api(`knowledge/media/${m.id}/accept`, "POST", {});
                          return "Görsel yayınlandı.";
                        })
                      }
                    >
                      Onayla
                    </button>
                  )}
                  {m.status === "published" && (
                    <button
                      className={styles.secondary}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api(`knowledge/media/${m.id}/revoke`, "POST", {});
                          return "Görsel yayından kaldırıldı.";
                        })
                      }
                    >
                      Kaldır
                    </button>
                  )}
                </span>
              )}
            </li>
          ))}
          {!media.length && <li>Henüz görsel bulunmadı.</li>}
        </ul>
      </div>
    </div>
  );
}
