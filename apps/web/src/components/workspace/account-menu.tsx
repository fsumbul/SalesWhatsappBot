"use client";
import { useRef } from "react";
import { ChevronDown } from "lucide-react";
import type { Me } from "./types";
import styles from "./workspace.module.css";
export default function AccountMenu({
  me,
  onNavigate,
  onLogout,
}: {
  me: Me;
  onNavigate: (tab: string) => void;
  onLogout: () => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const owner = ["tenant_owner", "super_admin"].includes(me.user.role);
  return (
    <details ref={menu} className={styles.chatAccount}>
      <summary>
        Hesap <ChevronDown size={14} aria-hidden="true" />
      </summary>
      <div className={styles.accountPopover}>
        <small>{me.user.full_name || me.user.email}</small>
        {[
          ...(me.user.role !== "viewer" ? [["ops", "Sohbet"]] : []),
          ["agents", "Asistanlar"],
          ["inbox", "Gelen kutusu"],
          ...(owner ? [["team", "Ekip ve yetkiler"]] : []),
          ...(me.user.role === "super_admin" ? [["platform", "Şirketler"]] : []),
        ].map(([tab, label]) => (
          <button
            key={tab}
            onClick={() => {
              if (menu.current) menu.current.open = false;
              onNavigate(tab);
            }}
          >
            {label}
          </button>
        ))}
        <button onClick={onLogout}>Çıkış yap</button>
      </div>
    </details>
  );
}
