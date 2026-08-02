import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { currentAccount } from "@/lib/auth";
import { saveStrategy } from "@/lib/engine";
import { getSupabaseAdmin } from "@/lib/supabase/admin";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

export async function GET() {
  try {
    const account = await currentAccount(true);
    const { data, error } = await getSupabaseAdmin().from("strategies")
      .select("id,name,status,entry_at,exit_at,last_error,created_at")
      .eq("user_id", account!.id).order("created_at", { ascending: false }).limit(100);
    if (error) throw error;
    const rows = (data ?? []).map((row) => ({ id: row.id, name: row.name, status: row.status, entryAt: row.entry_at, exitAt: row.exit_at, lastError: row.last_error, createdAt: row.created_at }));
    return NextResponse.json({ success: true, result: rows });
  } catch (error) { return apiError(error); }
}

export async function POST(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const body = z.object({ strategy: z.unknown(), status: z.enum(["draft", "scheduled"]) }).parse(await jsonBody(request));
    const result = await saveStrategy(account!.id, body.strategy, body.status);
    return NextResponse.json({ success: true, result }, { status: 201 });
  } catch (error) { return apiError(error); }
}
