import { NextRequest, NextResponse } from "next/server";
import { currentAccount, destroyCurrentSession } from "@/lib/auth";
import { apiError, assertSameOrigin } from "@/lib/http";

export async function GET() {
  try {
    const account = await currentAccount(false);
    return NextResponse.json({ success: true, connected: Boolean(account), account: account ? {
      id: account.delta_user_id,
      accountName: account.account_name,
      email: account.email_masked,
      environment: account.environment
    } : null });
  } catch (error) { return apiError(error); }
}

export async function DELETE(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const response = NextResponse.json({ success: true });
    await destroyCurrentSession(response);
    return response;
  } catch (error) { return apiError(error); }
}
