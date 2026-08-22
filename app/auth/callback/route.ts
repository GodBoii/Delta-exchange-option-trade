import { NextRequest, NextResponse } from "next/server";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const next = request.nextUrl.searchParams.get("next") ?? "/";
  const fallbackUrl = new URL("/", request.url);
  const candidateUrl = next.startsWith("/") && !next.startsWith("//") && !next.includes("\\")
    ? new URL(next, request.url)
    : fallbackUrl;
  const destinationUrl = candidateUrl.origin === fallbackUrl.origin ? candidateUrl : fallbackUrl;
  if (code) {
    const supabase = await getSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(destinationUrl);
  }
  const failureUrl = new URL(destinationUrl);
  failureUrl.searchParams.set("auth_error", "callback");
  return NextResponse.redirect(failureUrl);
}
