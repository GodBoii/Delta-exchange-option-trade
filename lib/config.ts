export type DeltaEnvironment = "production" | "testnet";

export function getDeltaEnvironment(): DeltaEnvironment {
  return process.env.DELTA_ENV === "testnet" ? "testnet" : "production";
}

export function getDeltaBaseUrl(environment: DeltaEnvironment = getDeltaEnvironment()) {
  return environment === "testnet"
    ? process.env.DELTA_TESTNET_URL ?? "https://cdn-ind.testnet.deltaex.org"
    : process.env.DELTA_PRODUCTION_URL ?? "https://api.india.delta.exchange";
}
