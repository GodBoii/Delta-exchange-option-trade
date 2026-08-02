import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { currentAccount } from "@/lib/auth";
import { db } from "@/lib/db";
import { saveStrategy } from "@/lib/engine";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

export async function GET() {
  try {
    const account = await currentAccount(true);
    const rows = db.prepare(`SELECT id, name, status, entry_at as entryAt, exit_at as exitAt, last_error as lastError, created_at as createdAt
      FROM strategies WHERE account_id=? ORDER BY created_at DESC LIMIT 100`).all(account!.id);
    return NextResponse.json({ success: true, result: rows });
  } catch (error) { return apiError(error); }
}

export async function POST(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const body = z.object({ strategy: z.unknown(), status: z.enum(["draft", "scheduled"]) }).parse(await jsonBody(request));
    const result = saveStrategy(account!.id, body.strategy, body.status);
    return NextResponse.json({ success: true, result }, { status: 201 });
  } catch (error) { return apiError(error); }
}
