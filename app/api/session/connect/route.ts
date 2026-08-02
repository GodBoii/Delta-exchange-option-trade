import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { createConnection, setSessionCookie } from "@/lib/auth";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

const connectSchema = z.object({
  apiKey: z.string().trim().min(16).max(128),
  apiSecret: z.string().trim().min(24).max(256),
  environment: z.enum(["production", "testnet"]).default("production")
});

export async function POST(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const input = connectSchema.parse(await jsonBody(request));
    const connection = await createConnection(input.apiKey, input.apiSecret, input.environment);
    const response = NextResponse.json({ success: true, account: connection.account });
    setSessionCookie(response, connection.token, connection.expiresAt);
    return response;
  } catch (error) { return apiError(error); }
}
