import { NextRequest, NextResponse } from "next/server";
import { DeltaClient } from "@/lib/delta";
import { apiError } from "@/lib/http";

export async function GET(request: NextRequest) {
  try {
    const search = request.nextUrl.searchParams;
    const result = await new DeltaClient().products({
      contract_types: search.get("contractTypes") ?? "perpetual_futures,futures,call_options,put_options",
      states: "live,upcoming",
      expiry: search.get("expiry") ?? undefined,
      page_size: Math.min(100, Math.max(1, Number(search.get("pageSize") ?? 100)))
    });
    return NextResponse.json(result);
  } catch (error) { return apiError(error); }
}
