import { notFound } from "next/navigation";
import { getRequestConfig } from "next-intl/server";

export const locales = ["tr", "en", "de", "ar", "ru"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "tr";

export const rtlLocales: readonly Locale[] = ["ar"];

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = (locales as readonly string[]).includes(requested ?? "")
    ? (requested as Locale)
    : defaultLocale;

  try {
    return {
      locale,
      messages: (await import(`../../messages/${locale}.json`)).default,
    };
  } catch {
    notFound();
  }
});
