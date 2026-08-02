import { NextRequest, NextResponse } from "next/server";
import { ZodError } from "zod";

export class AppError extends Error {
  constructor(public status: number, message: string, public code = "request_failed") {
    super(message);
  }
}

export function assertSameOrigin(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin) return;
  const expected = new URL(request.url).origin;
  if (origin !== expected) throw new AppError(403, "Origin check failed", "invalid_origin");
}

export function apiError(error: unknown) {
  if (error instanceof AppError) {
    return NextResponse.json({ success: false, error: { code: error.code, message: error.message } }, { status: error.status });
  }
  if (error instanceof ZodError) {
    return NextResponse.json(
      { success: false, error: { code: "validation_error", message: "Please correct the highlighted fields", issues: error.issues } },
      { status: 400 }
    );
  }
  const message = error instanceof Error ? error.message : "Unexpected server error";
  console.error(error);
  return NextResponse.json({ success: false, error: { code: "internal_error", message } }, { status: 500 });
}

export async function jsonBody(request: NextRequest): Promise<unknown> {
  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > 256_000) throw new AppError(413, "Request body is too large", "body_too_large");
  try {
    return await request.json();
  } catch {
    throw new AppError(400, "Invalid JSON body", "invalid_json");
  }
}
