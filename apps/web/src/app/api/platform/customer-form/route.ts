import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";
async function proxy(req: NextRequest) {
  if (
    req.method === "POST" &&
    req.headers.get("origin") !== (process.env.WEB_BASE_URL || req.nextUrl.origin)
  )
    return NextResponse.json({ detail: "İstek kaynağı geçersiz." }, { status: 403 });
  const token = req.headers.get("x-form-token");
  if (!token || token.length > 2000)
    return NextResponse.json({ detail: "Form bağlantısı gerekli." }, { status: 401 });
  const body = req.method === "POST" ? await req.text() : undefined;
  if (body && body.length > 7_100_000)
    return NextResponse.json({ detail: "Dosya çok büyük." }, { status: 413 });
  const response = await fetch(
    `${process.env.API_BASE_URL || "http://127.0.0.1:8001"}/api/v1/customer-form`,
    {
      method: req.method,
      headers: { "Content-Type": "application/json", "X-Form-Token": token },
      body,
      cache: "no-store",
    },
  );
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
    },
  });
}
export const GET = proxy;
export const POST = proxy;
