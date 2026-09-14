"use client";
import { useEffect, useRef, useState } from "react";
import styles from "./selection.module.css";
type Field = {
  id: string;
  label: string;
  question: string;
  kind: string;
  unit?: string;
  visible: boolean;
  choices: { value: string; label: string }[];
  allow_custom: boolean;
  skip_if: Record<string, string[]>;
};
type View = {
  id: string;
  title: string;
  revision: number;
  status: string;
  ready: boolean;
  answers: Record<string, { status: string; value?: string }>;
  fields: Field[];
  files: { id: string; name: string; size: number }[];
};
export default function SelectionForm() {
  const [view, setView] = useState<View | null>(null),
    [values, setValues] = useState<Record<string, string>>({}),
    [error, setError] = useState(""),
    [errors, setErrors] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  const token = useRef(""),
    pending = useRef<Record<string, unknown> | null>(null);
  const key = "ashira-customer-form-token";
  function adopt(v: View) {
    setView(v);
    setValues(
      Object.fromEntries(
        Object.entries(v.answers).map(([k, a]) => [
          k,
          a.status === "unknown"
            ? "@unknown"
            : a.status === "drawing"
              ? "@drawing"
              : String(a.value ?? ""),
        ]),
      ),
    );
  }
  async function fetchView() {
    const r = await fetch("/api/platform/customer-form", {
      headers: { "X-Form-Token": token.current },
      cache: "no-store",
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Form yüklenemedi.");
    adopt(data);
  }
  useEffect(() => {
    token.current =
      new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem(key) || "";
    if (token.current) {
      sessionStorage.setItem(key, token.current);
      history.replaceState(null, "", location.pathname);
    }
    fetchView().catch((e) => setError(e.message));
  }, []);
  const dirty =
    !!view &&
    JSON.stringify(values) !==
      JSON.stringify(
        Object.fromEntries(
          Object.entries(view.answers).map(([k, a]) => [
            k,
            a.status === "unknown"
              ? "@unknown"
              : a.status === "drawing"
                ? "@drawing"
                : String(a.value ?? ""),
          ]),
        ),
      );
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function act(action: string, file?: { name: string; mime: string; data: string }) {
    if (!view || busy) return;
    setBusy(true);
    setError("");
    setErrors({});
    setSaved(false);
    const body = pending.current ?? {
      action,
      revision: view.revision,
      operation_id: crypto.randomUUID(),
      fields: action === "save" ? values : {},
      ...(file ? { file } : {}),
    };
    pending.current = body;
    try {
      const r = await fetch("/api/platform/customer-form", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Form-Token": token.current },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        if (r.status < 500) pending.current = null;
        if (data.detail?.form) {
          adopt(data.detail.form);
          setError(data.detail.message);
        } else if (data.detail?.errors) {
          setErrors(data.detail.errors);
          setError("İşaretli alanları kontrol edin.");
        } else setError(typeof data.detail === "string" ? data.detail : "Bilgiler kaydedilemedi.");
        return;
      }
      pending.current = null;
      adopt(data);
      if (body.action === "upload" && dirty) setValues(values);
      setSaved(true);
    } catch {
      setError("Bağlantı kesildi. Aynı kaydı yeniden deneyebilirsiniz.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <span className={styles.brand}>ashira.</span>
        <h1>{view?.title || "Talep formu"}</h1>
        <p>
          Bu form ve WhatsApp sohbeti aynı talebe bağlıdır. Kaydettikten sonra iki yöntemden biriyle
          devam edebilirsiniz.
        </p>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        {!view && !error && <p>Form yükleniyor…</p>}
        {view && (
          <>
            <small>Talep no: {view.id.slice(0, 8)}</small>
            {view.status !== "draft" ? (
              <p role="status">
                {["cancelled", "superseded"].includes(view.status)
                  ? "Bu talep kapatılmış. WhatsApp üzerinden yeni bir talep başlatabilirsiniz."
                  : "Talebiniz kaydedildi. Ekibimiz bilgileri ve dosyaları inceleyecek. Bu kayıt fiyat veya teknik uygunluk onayı değildir."}
              </p>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  act("save");
                }}
              >
                <fieldset disabled={busy || !!pending.current}>
                  {view.fields
                    .filter(
                      (f) =>
                        !Object.entries(f.skip_if).some(([key, options]) =>
                          options.includes(values[key]),
                        ),
                    )
                    .map((f) => (
                      <div className={styles.field} key={f.id}>
                        <label htmlFor={f.id}>
                          {f.label}
                          {f.unit ? ` (${f.unit})` : ""}
                        </label>
                        <small>{f.question}</small>
                        {f.choices.length > 0 && !f.allow_custom ? (
                          <select
                            id={f.id}
                            value={values[f.id] || ""}
                            aria-invalid={!!errors[f.id]}
                            aria-describedby={errors[f.id] ? f.id + "-error" : undefined}
                            onChange={(e) => setValues({ ...values, [f.id]: e.target.value })}
                          >
                            <option value="">Seçin</option>
                            {f.choices.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <>
                            <input
                              id={f.id}
                              list={f.id + "-options"}
                              value={
                                f.choices.find((o) => o.value === values[f.id])?.label ||
                                values[f.id] ||
                                ""
                              }
                              aria-invalid={!!errors[f.id]}
                              aria-describedby={errors[f.id] ? f.id + "-error" : undefined}
                              onChange={(e) =>
                                setValues({
                                  ...values,
                                  [f.id]:
                                    f.choices.find((o) => o.label === e.target.value)?.value ||
                                    e.target.value,
                                })
                              }
                            />
                            <datalist id={f.id + "-options"}>
                              {f.choices.map((o) => (
                                <option key={o.value} value={o.label}>
                                  {o.label}
                                </option>
                              ))}
                            </datalist>
                          </>
                        )}
                        {errors[f.id] && (
                          <small id={f.id + "-error"} className={styles.error}>
                            {errors[f.id]}
                          </small>
                        )}
                      </div>
                    ))}
                  <button type="submit">Bilgileri kaydet</button>
                </fieldset>
              </form>
            )}
            <section>
              <h2>Dosyalar</h2>
              {view.files.map((f) => (
                <p key={f.id}>
                  {f.name} · {Math.ceil(f.size / 1024)} KB
                </p>
              ))}
              {view.status === "draft" && (
                <label className={styles.upload}>
                  PDF veya fotoğraf ekle (en fazla 5 MB)
                  <input
                    type="file"
                    accept="application/pdf,image/jpeg,image/png"
                    disabled={busy || !!pending.current}
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      if (f.size > 5 * 1024 * 1024) {
                        setError("Dosya en fazla 5 MB olabilir.");
                        return;
                      }
                      const reader = new FileReader();
                      reader.onload = () =>
                        act("upload", {
                          name: f.name,
                          mime: f.type,
                          data: String(reader.result).split(",")[1],
                        });
                      reader.readAsDataURL(f);
                      e.target.value = "";
                    }}
                  />
                </label>
              )}
            </section>
            {saved && (
              <p role="status">
                {view.status === "draft"
                  ? "Kaydedildi. WhatsApp’tan “devam” yazarak aynı talebe dönebilirsiniz."
                  : "Onayınız kaydedildi."}
              </p>
            )}
            {pending.current && !busy && (
              <button onClick={() => act(String(pending.current?.action))}>
                Aynı işlemi yeniden dene
              </button>
            )}
            {view.ready && view.status === "draft" && (
              <section>
                <h2>Son kontrol</h2>
                <p>Bilgileri inceleyip onayladığınızda talebiniz teknik/satış ekibine iletilir.</p>
                <button
                  disabled={dirty || busy || !!pending.current}
                  onClick={() => act("confirm")}
                >
                  Bilgilerimi onayla
                </button>
              </section>
            )}
            <button
              className={styles.secondary}
              disabled={busy || dirty || !!pending.current}
              onClick={() => fetchView().catch((e) => setError(e.message))}
            >
              WhatsApp’taki güncel bilgileri getir
            </button>
          </>
        )}
      </section>
    </main>
  );
}
