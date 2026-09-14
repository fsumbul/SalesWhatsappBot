"use client";
import { useCallback, useEffect, useState } from "react";
import { api, type Me } from "./types";
import styles from "./workspace.module.css";
type Row = {
  id: string;
  name?: string;
  slug?: string;
  email?: string;
  role?: string;
  is_active?: boolean;
  status?: string;
};
export default function AdminPanel({
  mode,
  me,
  initialValues = {},
}: {
  mode: string;
  me: Me;
  initialValues?: Record<string, string>;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const reload = useCallback(
    () => api<Row[]>(mode === "platform" ? "platform/tenants" : "users").then(setRows),
    [mode],
  );
  useEffect(() => {
    reload().catch((e) => setError(e.message));
  }, [reload]);
  async function run(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function showLink(token: string, company: string) {
    const url = new URL(window.location.pathname, window.location.origin);
    url.searchParams.set("invite", token);
    url.searchParams.set("company", company);
    setLink(url.toString());
  }
  return (
    <section className={styles.section}>
      <span className={styles.eyebrow}>{mode === "platform" ? "PLATFORM YÖNETİMİ" : "EKİP"}</span>
      <h1>{mode === "platform" ? "Şirketler" : "Ekip ve yetkiler"}</h1>
      <p>
        {mode === "platform"
          ? "Yeni şirket çalışma alanını açın ve sahibini davet edin."
          : "Şirketinize kullanıcı davet edin; erişim düzeylerini yönetin."}
      </p>
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      <div className={styles.card}>
        <h2>{mode === "platform" ? "Şirket oluştur" : "Kullanıcı davet et"}</h2>
        <form
          className={styles.inlineForm}
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const d = new FormData(form);
            run(async () => {
              if (mode === "platform") {
                const result = await api<{ tenant: { slug: string }; invitation_token: string }>(
                  "platform/tenants",
                  "POST",
                  { name: d.get("name"), slug: d.get("slug"), owner_email: d.get("email") },
                );
                showLink(result.invitation_token, result.tenant.slug);
              } else {
                const result = await api<{ token: string }>("auth/invite", "POST", {
                  email: d.get("email"),
                  role: d.get("role"),
                });
                showLink(result.token, me.tenant.slug);
              }
              form.reset();
            });
          }}
        >
          {mode === "platform" && (
            <>
              <label>
                Şirket adı
                <input name="name" required minLength={2} defaultValue={initialValues.name} />
              </label>
              <label>
                Şirket kodu
                <input
                  name="slug"
                  required
                  minLength={2}
                  pattern="[a-z0-9-]+"
                  defaultValue={initialValues.slug}
                />
              </label>
            </>
          )}
          <label>
            {mode === "platform" ? "Şirket sahibinin e-postası" : "E-posta"}
            <input type="email" name="email" required defaultValue={initialValues.email} />
          </label>
          {mode !== "platform" && (
            <label>
              Rol
              <select name="role" defaultValue={initialValues.role ?? "sales_agent"}>
                {[
                  ["tenant_owner", "Şirket sahibi"],
                  ["sales_manager", "Yönetici"],
                  ["sales_agent", "Temsilci"],
                  ["viewer", "İzleyici"],
                ].map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button disabled={busy}>Davet bağlantısı oluştur</button>
        </form>
        {link && (
          <div className={styles.notice}>
            <label>
              Davet bağlantısı · 7 gün geçerli
              <input readOnly value={link} onFocus={(e) => e.target.select()} />
            </label>
            <button
              className={styles.secondary}
              onClick={() =>
                navigator.clipboard
                  .writeText(link)
                  .catch(() => setError("Bağlantıyı seçip kopyalayabilirsiniz."))
              }
            >
              Bağlantıyı kopyala
            </button>
          </div>
        )}
      </div>
      <div className={styles.card}>
        <table>
          <thead>
            <tr>
              <th>{mode === "platform" ? "Şirket" : "Kullanıcı"}</th>
              <th>{mode === "platform" ? "Kod" : "Rol"}</th>
              <th>Durum</th>
              <th>İşlem</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name ?? row.email}</td>
                <td>
                  {mode === "platform" ? (
                    row.slug
                  ) : row.role === "super_admin" ? (
                    "Platform yöneticisi"
                  ) : (
                    <select
                      aria-label={`${row.email} rolü`}
                      disabled={busy}
                      value={row.role}
                      onChange={(e) => {
                        const role = e.target.value;
                        run(async () => {
                          await api("users/" + row.id, "PATCH", { role });
                        });
                      }}
                    >
                      {["tenant_owner", "sales_manager", "sales_agent", "viewer"].map((role) => (
                        <option key={role}>{role}</option>
                      ))}
                    </select>
                  )}
                </td>
                <td>{mode === "platform" ? row.status : row.is_active ? "Aktif" : "Devre dışı"}</td>
                <td>
                  {mode !== "platform" && row.role !== "super_admin" && (
                    <button
                      className={styles.secondary}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api("users/" + row.id, "PATCH", { is_active: !row.is_active });
                        })
                      }
                    >
                      {row.is_active ? "Devre dışı bırak" : "Etkinleştir"}
                    </button>
                  )}
                  {mode === "platform" && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const email = new FormData(e.currentTarget).get("email");
                        run(async () => {
                          const r = await api<{ invitation_token: string; tenant_slug: string }>(
                            `platform/tenants/${row.id}/owner-invitations`,
                            "POST",
                            { email },
                          );
                          showLink(r.invitation_token, r.tenant_slug);
                        });
                      }}
                    >
                      <label className={styles.srOnly}>Şirket sahibi e-postası</label>
                      <input
                        name="email"
                        type="email"
                        aria-label="Şirket sahibi e-postası"
                        required
                        placeholder="Sahip e-postası"
                      />
                      <button disabled={busy} className={styles.secondary}>
                        Sahip davet et
                      </button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
