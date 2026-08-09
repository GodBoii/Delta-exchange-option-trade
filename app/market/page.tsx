import BtcMarketChart from "@/app/components/BtcMarketChart";
import Link from "next/link";

export default function MarketPage() {
  return <div className="app market-public-page">
    <header className="connect-header market-public-header">
      <Link className="brand" href="/" aria-label="Trade Cognition home">
        <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <span><strong>Trade Cognition</strong><small>Delta workspace</small></span>
      </Link>
      <Link href="/">Strategy builder</Link>
    </header>
    <main className="workspace"><BtcMarketChart /></main>
  </div>;
}
