"use client";
import { useEffect, useState } from "react";
import { api, type Me } from "./types";
import OpsChat from "./ops-chat";
import styles from "./workspace.module.css";

export default function Workspace() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("ops");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [slug, setSlug] = useState("");
  const [invite, setInvite] = useState("");
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setTab(params.get("tab") === "agents" ? "agents" : "ops");
    setSlug(params.get("company") ?? "");
    setInvite(params.get("invite") ?? "");
    api<Me>("auth/me")
      .then(setMe)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  async function authenticate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      if (invite) {
        await api("auth/accept-invite", "POST", {
          token: invite,
          password: data.get("password"),
          full_name: data.get("name"),
        });
        setInvite("");
        window.history.replaceState({}, "", window.location.pathname);
        setError("Davet kabul edildi. E-posta ve şifrenizle giriş yapabilirsiniz.");
      } else {
        await api("auth/login", "POST", {
          tenant_slug: slug,
          email: data.get("email"),
          password: data.get("password"),
        });
        setMe(await api<Me>("auth/me"));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (loading)
    return (
      <main className={styles.login}>
        <p>Çalışma alanı yükleniyor…</p>
      </main>
    );
  if (!me)
    return (
      <main className={styles.login}>
        <form onSubmit={authenticate} className={styles.loginCard}>
          <span className={styles.eyebrow}>ASHIRA · ŞİRKET ÇALIŞMA ALANI</span>
          <h1>{invite ? "Ekibinize katılın" : "Tekrar hoş geldiniz"}</h1>
          <p>Şirket bilgilerinizi ve müşteri asistanınızı tek yerden yönetin.</p>
          {!invite && (
            <label>
              Şirket kodu
              <input
                required
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                autoComplete="organization"
              />
            </label>
          )}
          {invite ? (
            <label>
              Ad soyad
              <input name="name" required autoComplete="name" />
            </label>
          ) : (
            <label>
              E-posta
              <input name="email" type="email" required autoComplete="username" />
            </label>
          )}
          <label>
            Şifre
            <input
              name="password"
              type="password"
              required
              minLength={8}
              autoComplete={invite ? "new-password" : "current-password"}
            />
          </label>
          {error && (
            <p role="status" className={styles.notice}>
              {error}
            </p>
          )}
          <button disabled={busy}>
            {busy ? "İşleniyor…" : invite ? "Daveti kabul et" : "Giriş yap"}
          </button>
          <small>Hesaplar davetle açılır. Erişim için şirket yöneticinizle iletişime geçin.</small>
        </form>
      </main>
    );
  const logout = async () => {
    try {
      await api("auth/logout", "POST", {});
      setMe(null);
      setTab("ops");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className={`${styles.workspace} ${styles.chatWorkspace}`}>
      <OpsChat me={me} initialSection={tab} onLogout={logout} />
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
    </div>
  );
}
