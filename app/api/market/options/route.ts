import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { DeltaClient } from "@/lib/delta";
import { deltaExpiry } from "@/lib/strategy";
import { apiError } from "@/lib/http";

const querySchema = z.object({ underlying: z.enum(["BTC", "ETH"]), expiry: z.string().date() });

export async function GET(request: NextRequest) {
  try {
    const query = querySchema.parse(Object.fromEntries(request.nextUrl.searchParams));
    return NextResponse.json(await new DeltaClient().optionChain(query.underlying, deltaExpiry(query.expiry)));
  } catch (error) { return apiError(error); }
}
