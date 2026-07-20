import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

export default async function HomePage() {
  const t = await getTranslations("home");
  const locale = await getLocale();

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col items-center justify-center gap-6 px-6 text-center">
      <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-medium uppercase tracking-wider text-brand-700 dark:bg-brand-700/20 dark:text-brand-500">
        v0.1
      </span>
      <h1 className="text-4xl font-bold tracking-tight sm:text-6xl">{t("title")}</h1>
      <p className="max-w-2xl text-lg text-neutral-600 dark:text-neutral-400">{t("subtitle")}</p>
      <div className="flex flex-wrap justify-center gap-3">
        <Link
          href={`/${locale}/login`}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700"
        >
          {t("loginCta")}
        </Link>
        <Link
          href={`/${locale}/register`}
          className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 transition hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-900"
        >
          {t("registerCta")}
        </Link>
        <a
          href="http://localhost:8000/docs"
          className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 transition hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-900"
        >
          {t("apiDocs")}
        </a>
      </div>
    </main>
  );
}
