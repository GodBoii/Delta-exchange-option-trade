import Link from "next/link";
import { ArrowRight } from "@/app/components/icons";
import BtcMarketChart from "@/app/components/BtcMarketChart";
import { Brand } from "@/app/components/ui";

export const metadata = {
  title: "Market analysis"
};

/**
 * Public, read-only market surface. It carries no account context and never
 * touches an authenticated endpoint, so it is safe to share as a link.
 */
export default function MarketPage() {
  return (
    <div className="market-public-page">
      <header className="market-public-header">
        <Link href="/" aria-label="Trade Cognition home"><Brand subtitle="Public market data" /></Link>
        <Link className="button ghost" href="/" aria-label="Open dashboard">
          <span className="phone-label-wide" aria-hidden="true">Open dashboard</span>
          <span className="phone-label-compact" aria-hidden="true">Dashboard</span>
          <ArrowRight aria-hidden="true" />
        </Link>
      </header>
      <main className="workspace">
        <BtcMarketChart />
      </main>
    </div>
  );
}
