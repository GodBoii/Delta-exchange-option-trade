import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { createConnection, requireAppUser } from "@/lib/auth";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

const connectSchema = z.object({
  apiKey: z.string().trim().min(16).max(128),
  apiSecret: z.string().trim().min(24).max(256),
});

export async function POST(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const user = await requireAppUser();
    const input = connectSchema.parse(await jsonBody(request));
    const connection = await createConnection(user.id, input.apiKey, input.apiSecret);
    return NextResponse.json({ success: true, account: connection.account });
  } catch (error) { return apiError(error); }
}
