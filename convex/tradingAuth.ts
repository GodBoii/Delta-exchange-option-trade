export function authorizeTradingService(secret: string) {
  if (!process.env.CONVEX_TRADING_SECRET || secret !== process.env.CONVEX_TRADING_SECRET) {
    throw new Error("Unauthorized trading service");
  }
}

export function authorizeAccountReader(secret: string) {
  if (process.env.CONVEX_RESEARCH_SECRET && secret === process.env.CONVEX_RESEARCH_SECRET) return;
  authorizeTradingService(secret);
}
