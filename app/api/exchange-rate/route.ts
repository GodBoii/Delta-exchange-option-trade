import { NextResponse } from "next/server";

const RATE_URL = "https://api.frankfurter.dev/v2/rate/usd/inr";

type RateResponse = {
  date?: unknown;
  rate?: unknown;
};

export async function GET() {
  try {
    const response = await fetch(RATE_URL, { next: { revalidate: 3_600 } });
    if (!response.ok) throw new Error(`Exchange-rate provider returned ${response.status}`);

    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object") throw new Error("Exchange-rate provider returned invalid data");

    const { date, rate } = payload as RateResponse;
    if (typeof rate !== "number" || !Number.isFinite(rate) || rate <= 0) {
      throw new Error("Exchange-rate provider returned an invalid rate");
    }

    return NextResponse.json({
      base: "USD",
      quote: "INR",
      rate,
      date: typeof date === "string" ? date : null
    });
  } catch {
    return NextResponse.json(
      { message: "The USD to INR display rate is temporarily unavailable." },
      { status: 503 }
    );
  }
}
