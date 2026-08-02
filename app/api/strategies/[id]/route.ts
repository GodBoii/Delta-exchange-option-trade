import { NextRequest, NextResponse } from "next/server";
import { currentAccount } from "@/lib/auth";
import { apiError, AppError, assertSameOrigin } from "@/lib/http";
import { getSupabaseAdmin } from "@/lib/supabase/admin";

export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const { id } = await params;
    const { data, error } = await getSupabaseAdmin().from("strategies").update({ status: "cancelled" })
      .eq("id", id).eq("user_id", account!.id).in("status", ["draft", "scheduled"]).select("id").maybeSingle();
    if (error) throw error;
    if (!data) throw new AppError(409, "Only draft or scheduled strategies can be cancelled", "cannot_cancel");
    return NextResponse.json({ success: true });
  } catch (error) { return apiError(error); }
}
