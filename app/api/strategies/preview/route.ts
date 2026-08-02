import { NextRequest, NextResponse } from "next/server";
import { currentClient } from "@/lib/auth";
import { previewStrategy } from "@/lib/engine";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

export async function POST(request: NextRequest) {
  try {
    assertSameOrigin(request);
    const { client } = await currentClient();
    return NextResponse.json({ success: true, ...(await previewStrategy(client, await jsonBody(request))) });
  } catch (error) { return apiError(error); }
}
