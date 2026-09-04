"""Render the actual agent images for visual review, without running an agent or placing orders."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from automation_agent.market import MarketIntelligenceTools
from automation_agent.team import _chart_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--binance-url", help="Market service URL, e.g. http://127.0.0.1:8001 outside Docker")
    parser.add_argument("--packet", type=Path, help="Reuse a saved public market snapshot for before/after comparisons")
    args = parser.parse_args()
    packet = (
        json.loads(args.packet.read_text(encoding="utf-8"))
        if args.packet
        else MarketIntelligenceTools(binance_url=args.binance_url).collect_btc_market_packet()
    )
    packet.setdefault("capturedAt", int(datetime.now(UTC).timestamp() * 1000))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "market.json").write_text(json.dumps(packet), encoding="utf-8")
    for chart in _chart_artifacts(packet):
        path = args.output_dir / f"{chart.id}.png"
        path.write_bytes(chart.content)
        print(path.resolve())


if __name__ == "__main__":
    main()
