import type { AuthConfig } from "convex/server";

const issuer = "https://xphxxkmeqqgjobkmclso.supabase.co/auth/v1";

export default {
  providers: [{
    type: "customJwt",
    applicationID: "authenticated",
    issuer,
    jwks: `${issuer}/.well-known/jwks.json`,
    algorithm: "ES256",
  }],
} satisfies AuthConfig;
