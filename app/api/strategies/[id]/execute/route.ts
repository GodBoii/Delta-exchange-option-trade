import { NextRequest, NextResponse } from "next/server";
import { currentAccount } from "@/lib/auth";
import { executeEntry } from "@/lib/engine";
import { apiError, AppError, assertSameOrigin } from "@/lib/http";
import { getSupabaseAdmin } from "@/lib/supabase/admin";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const { id } = await params;
    const { data: owned } = await getSupabaseAdmin().from("strategies").select("id").eq("id", id).eq("user_id", account!.id).maybeSingle();
    if (!owned) throw new AppError(404, "Strategy not found", "strategy_not_found");
    return NextResponse.json({ success: true, result: await executeEntry(id) });
  } catch (error) { return apiError(error); }
}
