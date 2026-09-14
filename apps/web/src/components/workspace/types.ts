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
  const response = await fetch("/api/platform/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  const value = await response.json();
  if (!response.ok)
    throw new ApiError(
      typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail ?? value),
      response.status,
      value.detail,
    );
  return value as T;
}
