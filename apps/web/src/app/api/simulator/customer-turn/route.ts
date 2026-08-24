import { NextResponse } from "next/server";

import { generateCustomerTurn, LocalModelError } from "@/lib/simulator/customer-runtime";
import type { CustomerConversationMessage } from "@/lib/simulator/customer-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 16_000;
const MAX_HISTORY_ITEMS = 8;
const MAX_MESSAGE_LENGTH = 800;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return true;

  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

function parseInput(
  value: unknown,
): { message: string; history: CustomerConversationMessage[] } | null {
  if (!isRecord(value) || typeof value.message !== "string") return null;

  const message = value.message.trim();
  if (!message || message.length > MAX_MESSAGE_LENGTH) return null;

  const historyValue = value.history;
  if (!Array.isArray(historyValue) || historyValue.length > MAX_HISTORY_ITEMS) return null;

  const history: CustomerConversationMessage[] = [];
  for (const entry of historyValue) {
    if (!isRecord(entry) || (entry.role !== "customer" && entry.role !== "assistant")) {
      return null;
    }
    if (typeof entry.text !== "string" || entry.text.length > MAX_MESSAGE_LENGTH) return null;
    history.push({ role: entry.role, text: entry.text });
  }

  return { message, history };
}

export async function POST(request: Request) {
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > MAX_REQUEST_BYTES) {
    return NextResponse.json({ error: "İstek izin verilen boyutu aşıyor." }, { status: 413 });
  }
  if (!sameOrigin(request)) {
    return NextResponse.json(
      { error: "Bu simülatör isteği aynı kaynaktan gelmelidir." },
      { status: 403 },
    );
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "İstek JSON formatında olmalıdır." }, { status: 400 });
  }

  const input = parseInput(payload);
  if (!input) {
    return NextResponse.json({ error: "Mesaj veya konuşma geçmişi geçersiz." }, { status: 400 });
  }

  try {
    const turn = await generateCustomerTurn(input);
    return NextResponse.json(turn, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const message =
      error instanceof LocalModelError
        ? error.message
        : "Yerel model dönüşü güvenli şekilde işlenemedi.";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}
