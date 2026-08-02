import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { currentClient } from "@/lib/auth";
import { apiError, assertSameOrigin, jsonBody } from "@/lib/http";

export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  try {
    assertSameOrigin(request);
    const { client } = await currentClient();
    const { id } = await params;
    const body = z.object({ productId: z.number().int().positive(), confirm: z.literal(true) }).parse(await jsonBody(request));
    return NextResponse.json(await client.cancelOrder(Number(id), body.productId));
  } catch (error) { return apiError(error); }
}
