import BtcMarketChart from "@/app/components/BtcMarketChart";
import Link from "next/link";

export default function MarketPage() {
  return <div className="app market-public-page">
    <header className="connect-header market-public-header">
      <Link className="brand" href="/" aria-label="Delta Strategy Desk home">
        <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <span><strong>Delta</strong><small>Strategy Desk</small></span>
      </Link>
      <Link href="/">Strategy workspace</Link>
    </header>
    <main className="workspace"><BtcMarketChart /></main>
  </div>;
}
