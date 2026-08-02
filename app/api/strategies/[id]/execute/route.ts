import { NextRequest, NextResponse } from "next/server";
import { currentAccount } from "@/lib/auth";
import { db } from "@/lib/db";
import { executeEntry } from "@/lib/engine";
import { apiError, AppError, assertSameOrigin } from "@/lib/http";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const { id } = await params;
    const owned = db.prepare("SELECT id FROM strategies WHERE id=? AND account_id=?").get(id, account!.id);
    if (!owned) throw new AppError(404, "Strategy not found", "strategy_not_found");
    return NextResponse.json({ success: true, result: await executeEntry(id) });
  } catch (error) { return apiError(error); }
}
