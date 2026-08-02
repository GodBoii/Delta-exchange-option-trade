import crypto from "node:crypto";

export type DeltaEnvironment = "production" | "testnet";

export function getDeltaEnvironment(): DeltaEnvironment {
  return process.env.DELTA_ENV === "testnet" ? "testnet" : "production";
}

export function getDeltaBaseUrl(environment: DeltaEnvironment = getDeltaEnvironment()) {
  return environment === "testnet"
    ? process.env.DELTA_TESTNET_URL ?? "https://cdn-ind.testnet.deltaex.org"
    : process.env.DELTA_PRODUCTION_URL ?? "https://api.india.delta.exchange";
}

export function getEncryptionKey(): Buffer {
  const encoded = process.env.APP_ENCRYPTION_KEY;
  if (!encoded) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("APP_ENCRYPTION_KEY is required in production");
    }
    return crypto.createHash("sha256").update("delta-desk-development-only-key").digest();
  }
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32) throw new Error("APP_ENCRYPTION_KEY must be 32 bytes encoded as base64");
  return key;
}
