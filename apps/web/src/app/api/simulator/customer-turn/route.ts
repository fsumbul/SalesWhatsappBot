import { NextResponse } from "next/server";
export async function POST() {
  return NextResponse.json({ detail: "Şirket hesabınızla giriş yapıp ajan testini kullanın." }, { status: 410 });
}
