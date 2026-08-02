import { NextRequest, NextResponse } from "next/server";
import { currentAccount } from "@/lib/auth";
import { db } from "@/lib/db";
import { apiError, AppError, assertSameOrigin } from "@/lib/http";

export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    assertSameOrigin(request);
    const account = await currentAccount(true);
    const { id } = await params;
    const result = db.prepare("UPDATE strategies SET status='cancelled', updated_at=? WHERE id=? AND account_id=? AND status IN ('draft','scheduled')")
      .run(new Date().toISOString(), id, account!.id);
    if (!result.changes) throw new AppError(409, "Only draft or scheduled strategies can be cancelled", "cannot_cancel");
    return NextResponse.json({ success: true });
  } catch (error) { return apiError(error); }
}
