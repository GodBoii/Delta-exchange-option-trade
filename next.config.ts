import type { NextConfig } from "next";

const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "https://xphxxkmeqqgjobkmclso.supabase.co";
const apiOrigin = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
// The live portfolio stream is a WebSocket on the same trading API origin.
const apiWsOrigin = apiOrigin.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
const binanceApiOrigin = process.env.NEXT_PUBLIC_BINANCE_API_URL ?? "http://localhost:8001";
const binanceWsOrigin = binanceApiOrigin.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
const apiUrl = new URL(apiOrigin);

const nextConfig: NextConfig = {
  poweredByHeader: false,
  images: {
    // Agent charts are signed, short-lived links served by the trading API.
    remotePatterns: [{
      protocol: apiUrl.protocol === "http:" ? "http" : "https",
      hostname: apiUrl.hostname,
      pathname: "/api/charts/**"
    }]
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: ${apiOrigin}; connect-src 'self' ${supabaseOrigin} ${apiOrigin} ${apiWsOrigin} ${binanceApiOrigin} ${binanceWsOrigin} http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:* ws://[::1]:*; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` }
        ]
      }
    ];
  }
};

export default nextConfig;
