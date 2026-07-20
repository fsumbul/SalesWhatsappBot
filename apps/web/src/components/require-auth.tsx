"use client";

import { useRouter } from "next/navigation";
import { useLocale } from "next-intl";
import { useEffect, useState, type ReactNode } from "react";

import { useAuthStore } from "@/lib/auth-store";

export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const locale = useLocale();
  const accessToken = useAuthStore((s) => s.accessToken);
  // Zustand `persist` rehydrates from localStorage asynchronously. Until that
  // finishes, `accessToken` is null even for a logged-in user — so we must not
  // redirect (otherwise a page refresh bounces the user to /login).
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    return unsub;
  }, []);

  useEffect(() => {
    if (hydrated && !accessToken) router.replace(`/${locale}/login`);
  }, [hydrated, accessToken, locale, router]);

  if (!hydrated) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Yükleniyor…
      </div>
    );
  }
  if (!accessToken) return null;
  return <>{children}</>;
}
