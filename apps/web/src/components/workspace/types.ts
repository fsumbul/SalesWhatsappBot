export type CompanyConfig = {
  lifecycle: string;
  organization?: {
    id: string;
    display_names: Record<string, string>;
    [key: string]: unknown;
  } | null;
  agent?: {
    default_locale: string;
    supported_locales: string[];
    purposes: string[];
    [key: string]: unknown;
  } | null;
  offerings: Record<string, unknown>[];
  facts: Record<string, unknown>[];
  [key: string]: unknown;
};
export type Me = {
  tenant: { id: string; name: string; slug: string };
  user: { id: string; full_name: string | null; email: string; role: string };
};
export type Agent = { id: string; name: string; slug: string };
export type Version = {
  id: string;
  version: number;
  revision: number;
  status: string;
  company_config: CompanyConfig;
};
export type Proposal = {
  id: string;
  status: string;
  source: string;
  base_revision: number;
  company_config: CompanyConfig;
};
export type Turn = {
  role: string;
  content: string;
  reply?: string;
  action?: string;
  handoff_requested?: boolean;
  response_source?: string;
  fallback_reason?: string;
  interaction?: {
    kind: string;
    button_text: string;
    options: { id: string; title: string }[];
    url?: string;
    carousel_cards?: {
      id: string;
      title: string;
      body: string;
      url: string;
      header_media: { url: string };
    }[];
  };
  fact_ids?: string[];
  version?: number;
  latency_ms?: number;
  model?: string;
};
export type Session = {
  id: string;
  version_id?: string;
  draft_version_id?: string;
  messages: Turn[];
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const request = {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store" as const,
  };
  // Only reads and server-deduplicated chat commands can recover automatically.
  // Reuse the exact serialized body, including the operation/message ID.
  const command = body as Record<string, unknown> | undefined;
  const safeToRetry =
    method === "GET" ||
    (method === "POST" &&
      ((/^admin-chat\/sessions\/[^/]+\/turns$/.test(path) &&
        typeof command?.client_message_id === "string") ||
        (/^admin-chat\/sessions\/[^/]+\/workflows(?:\/[^/]+\/actions)?$/.test(path) &&
          typeof command?.client_operation_id === "string")));
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch("/api/platform/" + path, request);
      if (response.status === 204) return undefined as T;
      const raw = await response.text();
      let value;
      try {
        value = JSON.parse(raw);
      } catch {
        throw new ApiError(
          response.status === 502 || response.status === 504
            ? "Yanıt bekleme süresi doldu. İşlem devam ediyor olabilir; sohbeti yenileyerek sonucu kontrol edin."
            : "Sunucudan geçerli yanıt alınamadı. Sohbeti yenileyerek işlemin durumunu kontrol edin.",
          response.ok ? 502 : response.status,
        );
      }
      if (!response.ok)
        throw new ApiError(
          typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail ?? value),
          response.status,
          value.detail,
        );
      return value as T;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      if (attempt === 0 && safeToRetry) {
        await new Promise((resolve) => setTimeout(resolve, 700));
        continue;
      }
      throw new ApiError("Bağlantı kesildi. Aynı isteği güvenle yeniden deneyebilirsiniz.", 0);
    }
  }
}
