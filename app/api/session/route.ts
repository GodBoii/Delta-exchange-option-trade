import { NextRequest, NextResponse } from "next/server";
import { currentAccount, disconnectDelta, requireAppUser } from "@/lib/auth";
import { apiError, assertSameOrigin } from "@/lib/http";

export async function GET() {
  try {
    const account = await currentAccount(false);
    return NextResponse.json({ success: true, authenticated: Boolean(account), connected: Boolean(account?.connection_id), user: account ? {
      id: account.id,
      email: account.app_email,
      displayName: account.display_name,
      avatarUrl: account.avatar_url
    } : null, account: account?.connection_id ? {
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
    const user = await requireAppUser();
    await disconnectDelta(user.id);
    return NextResponse.json({ success: true });
  } catch (error) { return apiError(error); }
}
