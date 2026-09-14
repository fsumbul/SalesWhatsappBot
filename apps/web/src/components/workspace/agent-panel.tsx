"use client";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  type Agent,
  type Me,
  type Proposal,
  type Session,
  type Version,
  type Turn,
  type CompanyConfig,
} from "./types";
import styles from "./workspace.module.css";

export default function AgentPanel({
  me,
  initialView = "knowledge",
  initialAgentId = "",
  initialValues = {},
}: {
  me: Me;
  initialView?: string;
  initialAgentId?: string;
  initialValues?: Record<string, string>;
}) {
  const canEdit = ["super_admin", "tenant_owner", "sales_manager"].includes(me.user.role);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [aid, setAid] = useState("");
  const [versions, setVersions] = useState<Version[]>([]);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [builder, setBuilder] = useState<Session | null>(null);
  const [test, setTest] = useState<Session | null>(null);
  const [json, setJson] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState(initialView);
  const [file, setFile] = useState<{ content: string; format: string; headers: string[] } | null>(
    null,
  );
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [channel, setChannel] = useState<{ connected: boolean; agent_id?: string } | null>(null);
  const draft = versions.find((v) => v.status === "draft");
  const base = "agents/" + aid;
  const reload = useCallback(async () => {
    if (!aid) return;
    const [vs, ps, bs, ts] = await Promise.all([
      api<Version[]>(`agents/${aid}/versions`),
      canEdit ? api<Proposal[]>(`agents/${aid}/proposals`) : Promise.resolve([]),
      canEdit ? api<Session[]>(`agents/${aid}/builder/sessions`) : Promise.resolve([]),
      canEdit ? api<Session[]>(`agents/${aid}/test-sessions`) : Promise.resolve([]),
    ]);
    setVersions(vs);
    setProposals(ps);
    setBuilder(bs[0] ?? null);
    setTest(ts[0] ?? null);
    const d = vs.find((v) => v.status === "draft");
    setJson(JSON.stringify((d ?? vs[0])?.company_config ?? {}, null, 2));
    setSelectedVersion((old) => (vs.some((v) => v.id === old) ? old : ((d ?? vs[0])?.id ?? "")));
  }, [aid, canEdit]);
  useEffect(() => {
    api<Agent[]>("agents")
      .then((a) => {
        setAgents(a);
        setAid(a.some((item) => item.id === initialAgentId) ? initialAgentId : (a[0]?.id ?? ""));
      })
      .catch((e) => setError(e.message));
    api<{ connected: boolean; agent_id?: string }>("senders/connection")
      .then(setChannel)
      .catch((e) => setError(e.message));
  }, [initialAgentId]);
  useEffect(() => {
    setVersions([]);
    setProposals([]);
    setBuilder(null);
    setTest(null);
    reload().catch((e) => setError(e.message));
  }, [reload]);
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function proposeConfig(config: CompanyConfig) {
    await api(base + "/proposals", "POST", {
      company_config: config,
      expected_revision: draft?.revision,
    });
    await reload();
  }
  async function createAgent(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    await run(async () => {
      const a = await api<Agent>("agents", "POST", {
        name: data.get("name"),
        slug: data.get("slug"),
      });
      setAgents(await api<Agent[]>("agents"));
      setAid(a.id);
      setView("knowledge");
      form.reset();
    });
  }
  async function chat(e: React.FormEvent<HTMLFormElement>, kind: "builder" | "test") {
    e.preventDefault();
    const form = e.currentTarget;
    const text = String(new FormData(form).get("text"));
    await run(async () => {
      if (kind === "builder") {
        let session = builder;
        if (!session || session.draft_version_id !== draft?.id)
          session = await api<Session>(base + "/builder/sessions", "POST", {});
        await api(base + `/builder/sessions/${session.id}/messages`, "POST", { text });
      } else {
        let session = test;
        if (!session || session.version_id !== selectedVersion)
          session = await api<Session>(base + "/test-sessions", "POST", {
            version_id: selectedVersion,
          });
        await api(base + `/test-sessions/${session.id}/messages`, "POST", { text });
      }
      form.reset();
      await reload();
    });
  }
  return (
    <section className={styles.section}>
      <div className={styles.titleRow}>
        <div>
          <span className={styles.eyebrow}>ŞİRKET ASİSTANLARI</span>
          <h1>Bilgiden müşteri yanıtına</h1>
          <p>Bilgileri hazırlayın, asistanı test edin ve hazır sürümü yayınlayın.</p>
        </div>
        <span className={styles.badge}>
          {channel?.connected ? "WhatsApp bağlı" : "WhatsApp kurulmadı"}
        </span>
      </div>
      {error && (
        <div role="alert" className={styles.notice}>
          {error}
        </div>
      )}
      <div className={styles.toolbar}>
        <label>
          Asistan
          <select value={aid} onChange={(e) => setAid(e.target.value)} disabled={busy}>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        {canEdit && (
          <details open={view === "create" || undefined}>
            <summary>Yeni asistan</summary>
            <form onSubmit={createAgent} className={styles.inlineForm}>
              <label>
                Ad
                <input name="name" required minLength={2} defaultValue={initialValues.name} />
              </label>
              <label>
                Kod
                <input
                  name="slug"
                  required
                  minLength={2}
                  pattern="[a-z0-9-]+"
                  defaultValue={initialValues.slug}
                />
              </label>
              <button disabled={busy}>Oluştur</button>
            </form>
          </details>
        )}
      </div>
      {!aid ? (
        <div className={styles.empty}>
          <h2>İlk asistanınız için hazır</h2>
          <p>
            Şirket bilgilerinizi eklemek ve gerçek modelle test etmek için bir asistan oluşturun.
          </p>
        </div>
      ) : (
        <>
          <nav className={styles.tabs} aria-label="Asistan bölümleri">
            {[
              ["knowledge", "Bilgiler"],
              ["builder", "Yapılandırma sohbeti"],
              ["test", "Müşteri testi"],
              ["versions", "Sürümler"],
            ]
              .filter(([id]) => canEdit || id === "knowledge" || id === "versions")
              .map(([id, title]) => (
                <button
                  key={id}
                  aria-current={view === id ? "page" : undefined}
                  onClick={() => setView(id)}
                >
                  {title}
                </button>
              ))}
          </nav>
          {view === "knowledge" && (
            <div className={styles.grid}>
              <div className={styles.card}>
                <h2>Şirket bilgileri</h2>
                <p>Değişiklikler önce önizlemeye gelir; kabul ettiğinizde taslağa kaydedilir.</p>
                {canEdit && draft && (
                  <form
                    key={draft.id + ":" + draft.revision}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const data = new FormData(e.currentTarget);
                      run(async () => {
                        const config = structuredClone(draft.company_config);
                        const locale = config.agent?.default_locale ?? "tr";
                        config.organization = {
                          ...(config.organization ?? {}),
                          id: config.organization?.id ?? "company",
                          display_names: {
                            ...(config.organization?.display_names ?? {}),
                            [locale]: String(data.get("company")),
                          },
                        };
                        config.agent = {
                          ...(config.agent ?? {}),
                          purposes: [String(data.get("purpose"))],
                          supported_locales: config.agent?.supported_locales ?? [locale],
                          default_locale: locale,
                          require_fact_ids_for_claims: true,
                          unknown_fact_action: "handoff",
                        };
                        await proposeConfig(config);
                      });
                    }}
                  >
                    <label>
                      Şirket adı
                      <input
                        name="company"
                        required
                        defaultValue={
                          draft.company_config.organization?.display_names[
                            draft.company_config.agent?.default_locale ?? "tr"
                          ] ?? me.tenant.name
                        }
                      />
                    </label>
                    <label>
                      Asistan amacı
                      <select
                        name="purpose"
                        defaultValue={draft.company_config.agent?.purposes[0] ?? "information"}
                      >
                        {[
                          ["information", "Bilgilendirme"],
                          ["sales", "Satış"],
                          ["support", "Destek"],
                          ["appointment", "Randevu"],
                        ].map(([id, label]) => (
                          <option value={id} key={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button disabled={busy}>Değişikliği önizle</button>
                  </form>
                )}
                {!draft && canEdit && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api(base + "/versions", "POST", {});
                        await reload();
                      })
                    }
                  >
                    Yeni taslak aç
                  </button>
                )}
                {canEdit && draft && draft.company_config.organization && (
                  <details>
                    <summary>Onaylı bilgi ekle</summary>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const data = new FormData(e.currentTarget);
                        run(async () => {
                          const config = structuredClone(draft.company_config);
                          const locale = config.agent?.default_locale ?? "tr";
                          const id = String(data.get("id"));
                          const fact = {
                            id,
                            subject_id: String(data.get("subject")),
                            category: String(data.get("category")),
                            value: String(data.get("text")),
                            customer_text: { [locale]: String(data.get("text")) },
                            customer_visible: true,
                            source: String(data.get("source")),
                          };
                          config.facts = [...config.facts.filter((f) => f.id !== id), fact];
                          await proposeConfig(config);
                        });
                      }}
                    >
                      <label>
                        Bilgi kodu
                        <input
                          name="id"
                          required
                          pattern="[a-z][a-z0-9_-]*"
                          placeholder="company_services"
                        />
                      </label>
                      <label>
                        Bilginin konusu
                        <select name="subject">
                          <option value={draft.company_config.organization.id}>Şirket</option>
                          {draft.company_config.offerings.map((o) => (
                            <option key={String(o.id)} value={String(o.id)}>
                              {String(o.id)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Bilgi türü
                        <select name="category">
                          {[
                            ["capability", "Ürün ve hizmetler"],
                            ["specification", "Özellikler"],
                            ["commercial_rule", "Ticari koşullar"],
                            ["availability", "Stok"],
                            ["delivery", "Teslimat"],
                            ["support", "İletişim / destek"],
                            ["other", "Diğer"],
                          ].map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Müşteriye gösterilecek onaylı metin
                        <textarea name="text" required rows={3} maxLength={1000} />
                      </label>
                      <label>
                        Bilginin kaynağı
                        <input
                          name="source"
                          required
                          placeholder="Şirket sahibi, onaylı katalog veya kaynak URL"
                        />
                      </label>
                      <button disabled={busy}>Bilgiyi önizle</button>
                    </form>
                  </details>
                )}
                <details>
                  <summary>Tüm şirket verisini düzenle</summary>
                  <label>
                    Şirket JSON
                    <textarea
                      className={styles.code}
                      rows={20}
                      value={json}
                      onChange={(e) => setJson(e.target.value)}
                      readOnly={!canEdit || !draft}
                    />
                  </label>
                  {canEdit && draft && (
                    <button
                      disabled={busy}
                      onClick={() => run(async () => proposeConfig(JSON.parse(json)))}
                    >
                      JSON değişikliğini önizle
                    </button>
                  )}
                </details>
                {canEdit && draft && (
                  <details>
                    <summary>JSON / CSV içe aktar</summary>
                    <a
                      href="/api/platform/agents/imports/template.csv"
                      download="company-facts.csv"
                    >
                      CSV şablonunu indir
                    </a>
                    <label>
                      Dosya
                      <input
                        type="file"
                        accept=".json,.csv"
                        onChange={async (e) => {
                          const f = e.target.files?.[0];
                          if (!f) return;
                          if (f.size > 500000) {
                            setError("En fazla 500 KB yükleyebilirsiniz.");
                            return;
                          }
                          const content = await f.text();
                          const format = f.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
                          setFile({
                            content,
                            format,
                            headers: content
                              .split(/\r?\n/)[0]
                              .replace(/^\uFEFF/, "")
                              .split(",")
                              .map((h) => h.trim().replace(/^"|"$/g, "")),
                          });
                          setColumns({});
                        }}
                      />
                    </label>
                    {file?.format === "csv" &&
                      [
                        "id",
                        "subject_id",
                        "category",
                        "customer_text",
                        "source",
                        "search_terms",
                      ].map((field) => (
                        <label key={field}>
                          {field}
                          <select
                            value={columns[field] ?? field}
                            onChange={(e) => setColumns((c) => ({ ...c, [field]: e.target.value }))}
                          >
                            <option value={field}>{field}</option>
                            {file.headers
                              .filter((h) => h !== field)
                              .map((h) => (
                                <option key={h}>{h}</option>
                              ))}
                          </select>
                        </label>
                      ))}
                    <button
                      disabled={busy || !file}
                      onClick={() =>
                        run(async () => {
                          const result = await api<{ errors: unknown[] }>(
                            base + "/imports/preview",
                            "POST",
                            {
                              format: file?.format,
                              content: file?.content,
                              columns,
                              expected_revision: draft.revision,
                            },
                          );
                          if (result.errors.length) throw new Error(JSON.stringify(result.errors));
                          await reload();
                        })
                      }
                    >
                      Dosyayı önizle
                    </button>
                  </details>
                )}
              </div>
              <div className={styles.card}>
                <h2>
                  Onay bekleyen değişiklikler <span>{proposals.length}</span>
                </h2>
                {proposals.length === 0 && <p>Henüz bekleyen değişiklik yok.</p>}
                {proposals.map((p) => (
                  <article key={p.id} className={styles.proposal}>
                    <strong>
                      {p.source} · Taslak revizyonu {p.base_revision}
                    </strong>
                    <p>
                      {p.company_config.facts.length} bilgi · {p.company_config.offerings.length}{" "}
                      ürün/hizmet
                    </p>
                    <details>
                      <summary>Önerilen veriyi incele</summary>
                      <pre>{JSON.stringify(p.company_config, null, 2)}</pre>
                    </details>
                    <div className={styles.actions}>
                      <button
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api(base + `/proposals/${p.id}/accept`, "POST", {});
                            await reload();
                          })
                        }
                      >
                        Taslağa uygula
                      </button>
                      <button
                        className={styles.secondary}
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api(base + `/proposals/${p.id}/reject`, "POST", {});
                            await reload();
                          })
                        }
                      >
                        Reddet
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          )}
          {view === "builder" && (
            <div className={styles.card}>
              <h2>Asistanınızı anlatın</h2>
              <p>
                Şirketinizi, sunduğunuz ürün veya hizmetleri ve cevaplanacak soruları yazın.
                Öneriler Bilgiler sekmesinde onayınızı bekler.
              </p>
              <Transcript messages={builder?.messages ?? []} />
              {draft ? (
                <form onSubmit={(e) => chat(e, "builder")} className={styles.composer}>
                  <label className={styles.grow}>
                    Mesaj
                    <input
                      name="text"
                      required
                      maxLength={4000}
                      placeholder="Şirketimiz ne yapıyor, müşterilere neler anlatmalıyız?"
                    />
                  </label>
                  <button disabled={busy}>{busy ? "Model çalışıyor…" : "Gönder"}</button>
                </form>
              ) : (
                <p>Yapılandırma için önce yeni taslak açın.</p>
              )}
            </div>
          )}
          {view === "test" && (
            <div className={styles.card}>
              <div className={styles.titleRow}>
                <div>
                  <h2>Müşteri gibi deneyin</h2>
                  <p>Bu konuşma yalnız test içindir; WhatsApp mesajı göndermez.</p>
                </div>
                <label>
                  Test sürümü
                  <select
                    value={selectedVersion}
                    onChange={(e) => {
                      setSelectedVersion(e.target.value);
                      setTest(null);
                    }}
                  >
                    {versions.map((v) => (
                      <option value={v.id} key={v.id}>
                        v{v.version} · {v.status}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className={styles.secondary}
                  disabled={busy}
                  onClick={() =>
                    run(async () =>
                      setTest(
                        await api<Session>(base + "/test-sessions", "POST", {
                          version_id: selectedVersion,
                        }),
                      ),
                    )
                  }
                >
                  Yeni test
                </button>
              </div>
              <Transcript
                messages={test?.version_id === selectedVersion ? test.messages : []}
                busy={busy}
                onAction={(text) =>
                  run(async () => {
                    if (!test) return;
                    await api(base + `/test-sessions/${test.id}/messages`, "POST", {
                      text: `[${text}]`,
                    });
                    await reload();
                  })
                }
              />
              <form className={styles.composer} onSubmit={(e) => chat(e, "test")}>
                <label className={styles.grow}>
                  Müşteri mesajı
                  <input
                    name="text"
                    required
                    maxLength={4000}
                    placeholder="Ürün veya hizmetleriniz hakkında bilgi alabilir miyim?"
                  />
                </label>
                <button disabled={busy}>{busy ? "Yanıt hazırlanıyor…" : "Gönder"}</button>
              </form>
            </div>
          )}
          {view === "versions" && (
            <div className={styles.card}>
              <h2>Sürüm geçmişi</h2>
              <p>
                Yayınlanan sürüm yeni müşteri mesajlarında kullanılır. WhatsApp gönderimi için
                ayrıca aktif bağlantı gerekir.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Sürüm</th>
                    <th>Durum</th>
                    <th>İşlem</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((v) => (
                    <tr key={v.id}>
                      <td>v{v.version}</td>
                      <td>{v.status}</td>
                      <td>
                        {canEdit && ["draft", "testing"].includes(v.status) && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                await api(base + `/versions/${v.id}/promote-to-live`, "POST", {});
                                await reload();
                              })
                            }
                          >
                            Bu sürümü yayınla
                          </button>
                        )}
                        {canEdit && v.status === "archived" && (
                          <button
                            className={styles.secondary}
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                await api(base + `/versions/${v.id}/rollback`, "POST", {});
                                await reload();
                              })
                            }
                          >
                            Yeni sürüm olarak geri al
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
export function Transcript({
  messages,
  onAction,
  busy,
}: {
  messages: Turn[];
  onAction?: (id: string) => void;
  busy?: boolean;
}) {
  return (
    <div className={styles.transcript} aria-live="polite">
      {messages.length === 0 && (
        <p className={styles.empty}>Konuşmaya başlamak için bir mesaj yazın.</p>
      )}
      {messages.map((m, i) => (
        <article key={i} className={m.role === "user" ? styles.userMessage : styles.botMessage}>
          <small>{m.role === "user" ? "Siz" : m.answer_verified === false && !m.content ? "Test durumu" : "Asistan"}</small>
          <p>{m.content}</p>
          {(m.action === "handoff" || m.handoff_requested) && (
            <small>İnsan devri istendi · Testte gerçek devir kaydı oluşturulmaz</small>
          )}
          {m.interaction && (
            <div className={styles.actions}>
              {m.interaction.options.map((o) => (
                <button
                  key={o.id}
                  disabled={busy || i !== messages.length - 1}
                  onClick={() => onAction?.(o.id)}
                >
                  {o.title}
                </button>
              ))}
              {m.interaction.url && (
                <a href={m.interaction.url} target="_blank" rel="noreferrer">
                  {m.interaction.button_text}
                </a>
              )}
              {m.interaction.carousel_cards?.map((c) => (
                <a key={c.id} href={c.url} target="_blank" rel="noreferrer">
                  {c.title}
                </a>
              ))}
            </div>
          )}
          {m.response_source && (
            <small className={m.response_source === "fallback" ? styles.warning : ""}>
              {m.answer_origin === "model_generated"
                ? `Modelden üretildi: ${m.model}`
                : m.answer_origin === "model_unavailable" || m.answer_origin === "verification_failed"
                  ? "Model yanıtı üretilemedi veya doğrulanamadı · Mesaj gönderilmedi"
                : m.response_source === "fallback"
                  ? "Eski sürüm yedek yanıtı"
                : m.response_source === "guided"
                  ? "Menü eylemi"
                  : `Model: ${m.model}`}
              {m.fallback_reason ? ` (${m.fallback_reason})` : ""} · v{m.version} · {m.latency_ms}{" "}
              ms{m.fact_ids?.length ? " · " + m.fact_ids.join(", ") : ""}
            </small>
          )}
        </article>
      ))}
    </div>
  );
}
