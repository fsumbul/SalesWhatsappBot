import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const API = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const ACCESS = "ashira_access";
const REFRESH = "ashira_refresh";
type Tokens = { access_token: string; refresh_token: string; expires_in: number };
const refreshes = new Map<string, { expires: number; promise: Promise<Tokens | null> }>();

function setTokens(response: NextResponse, tokens: Tokens) {
  const options = {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
  };
  response.cookies.set(ACCESS, tokens.access_token, { ...options, maxAge: tokens.expires_in });
  response.cookies.set(REFRESH, tokens.refresh_token, { ...options, maxAge: 7 * 86400 });
}
function clearTokens(response: NextResponse) {
  response.cookies.delete(ACCESS);
  response.cookies.delete(REFRESH);
}
async function refresh(token: string): Promise<Tokens | null> {
  const key = createHash("sha256").update(token).digest("hex");
  const now = Date.now();
  for (const [id, entry] of refreshes) if (entry.expires < now) refreshes.delete(id);
  const cached = refreshes.get(key);
  if (cached) return cached.promise;
  const promise = fetch(API + "/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  })
    .then(async (r) => (r.ok ? ((await r.json()) as Tokens) : null))
    .catch(() => null);
  refreshes.set(key, { expires: now + 15000, promise });
  return promise;
}
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some((p) => !/^[a-zA-Z0-9_.-]+$/.test(p) || p === ".."))
    return NextResponse.json({ detail: "Invalid path" }, { status: 400 });
  const route = path.join("/");
  if (
    ![
      "auth",
      "agents",
      "platform",
      "users",
      "conversations",
      "senders",
      "admin-chat",
      "selection-requests",
      "knowledge",
    ].includes(path[0])
  )
    return NextResponse.json({}, { status: 404 });
  if (request.method !== "GET") {
    const expected = process.env.WEB_BASE_URL
      ? new URL(process.env.WEB_BASE_URL).origin
      : request.nextUrl.origin;
    if (request.headers.get("origin") !== expected)
      return NextResponse.json({ detail: "Origin rejected" }, { status: 403 });
  }
  // Knowledge documents (PDF/XLSX catalogues) are the only large uploads.
  const upload = route === "knowledge/documents" && request.method === "POST";
  const bodyLimit = upload ? 21 * 1024 * 1024 : 600000;
  if (Number(request.headers.get("content-length") ?? "0") > bodyLimit)
    return NextResponse.json({}, { status: 413 });
  const publicRoute = route === "auth/login" || route === "auth/accept-invite";
  if (route === "auth/register-tenant" || route === "auth/refresh")
    return NextResponse.json({}, { status: 404 });
  let access = request.cookies.get(ACCESS)?.value;
  const refreshToken = request.cookies.get(REFRESH)?.value;
  let tokens: Tokens | null = null;
  if (route === "auth/logout") {
    if (refreshToken)
      await fetch(API + "/api/v1/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        signal: AbortSignal.timeout(10000),
      }).catch(() => null);
    const response = NextResponse.json({ ok: true });
    clearTokens(response);
    return response;
  }
  if (!publicRoute && !access && refreshToken) {
    tokens = await refresh(refreshToken);
    access = tokens?.access_token;
  }
  if (!publicRoute && !access) {
    const response = NextResponse.json({ detail: "Oturum açın" }, { status: 401 });
    clearTokens(response);
    return response;
  }
  const incomingType = request.headers.get("content-type") ?? "";
  const multipart = upload && incomingType.startsWith("multipart/form-data");
  const body: BodyInit | undefined =
    request.method === "GET"
      ? undefined
      : multipart
        ? Buffer.from(await request.arrayBuffer())
        : await request.text();
  if (typeof body === "string" && Buffer.byteLength(body) > 600000)
    return NextResponse.json({}, { status: 413 });
  if (body instanceof Buffer && body.byteLength > bodyLimit)
    return NextResponse.json({}, { status: 413 });
  const invoke = () =>
    fetch(API + "/api/v1/" + route + request.nextUrl.search, {
      method: request.method,
      headers: {
        "Content-Type": multipart ? incomingType : "application/json",
        ...(access ? { Authorization: "Bearer " + access } : {}),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(
        /^admin-chat\/sessions\/[^/]+\/turns$/.test(route) ? 300000 : 180000,
      ),
    });
  try {
    let upstream = await invoke();
    if (upstream.status === 401 && !publicRoute && refreshToken && !tokens) {
      tokens = await refresh(refreshToken);
      if (tokens) {
        access = tokens.access_token;
        upstream = await invoke();
      }
    }
    if (route === "auth/login" && upstream.ok) {
      const response = NextResponse.json({ ok: true });
      setTokens(response, (await upstream.json()) as Tokens);
      return response;
    }
    const response = new NextResponse(
      upstream.status === 204 ? null : await upstream.arrayBuffer(),
      {
        status: upstream.status,
        headers: {
          "Content-Type": upstream.headers.get("content-type") ?? "application/json",
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
          ...(upstream.headers.get("content-disposition")
            ? { "Content-Disposition": upstream.headers.get("content-disposition")! }
            : {}),
        },
      },
    );
    if (tokens) setTokens(response, tokens);
    if (upstream.status === 401) clearTokens(response);
    return response;
  } catch {
    return NextResponse.json(
      { detail: "API bağlantısı kurulamadı. İşlemin durumunu yenileyerek kontrol edin." },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH };
