import { NextResponse } from "next/server";
import { currentClient } from "@/lib/auth";
import { apiError } from "@/lib/http";

export async function GET() {
  try {
    const { account, client } = await currentClient();
    const [balances, orders, positions] = await Promise.all([client.balances(), client.openOrders(), client.positions()]);
    return NextResponse.json({ success: true, account: { id: account.delta_user_id, name: account.account_name, environment: account.environment }, balances: balances.result, balanceMeta: balances.meta ?? {}, orders: orders.result, positions: positions.result });
  } catch (error) { return apiError(error); }
}
