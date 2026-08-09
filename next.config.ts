import type { NextConfig } from "next";

const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "https://xphxxkmeqqgjobkmclso.supabase.co";
const apiOrigin = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const binanceApiOrigin = process.env.NEXT_PUBLIC_BINANCE_API_URL ?? "http://localhost:8001";
const binanceWsOrigin = binanceApiOrigin.replace(/^http:/, "ws:").replace(/^https:/, "wss:");

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' ${supabaseOrigin} ${apiOrigin} ${binanceApiOrigin} ${binanceWsOrigin} http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:* ws://[::1]:*; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` }
        ]
      }
    ];
  }
};

export default nextConfig;
