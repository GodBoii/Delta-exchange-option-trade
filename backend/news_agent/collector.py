"""Collect public evidence independently of model runs; never schedule an analysis or trade."""

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .budget import ResearchBudget
from .config import NewsAgentSettings
from .sources import PublicSourceTools

logger = logging.getLogger(__name__)


def write_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def initial_collection(asset: str, now: datetime) -> str:
    state = os.getenv("NEWS_STATE_DIR")
    if not state:
        return ""
    path = Path(state) / "collected" / f"{asset}.json"
    try:
        if path.stat().st_size > 2000000:
            return ""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise ValueError("invalid collection structure")
        at = datetime.fromisoformat(payload["collected_at"])
        if not 0 <= (now - at).total_seconds() <= 180:
            return ""
    except FileNotFoundError:
        return ""
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Ignoring invalid public collection snapshot asset=%s", asset)
        return ""
    # Do not inject arbitrary stored payloads or full documents into the prompt.
    cards = payload.get("result", {}).get("sources", [])
    if not isinstance(cards, list) or not all(isinstance(card, dict) for card in cards):
        return ""
    return "\nRecently collected public discovery leads, not verified claims:\n" + json.dumps(
        cards[:120], ensure_ascii=False
    )


async def collect_once(settings: NewsAgentSettings, directory: Path) -> None:
    async def collect(asset):
        tool = PublicSourceTools(settings, ResearchBudget(seconds=120))
        result = json.loads(await tool.curate_public_sources(asset, []))
        write_snapshot(
            directory / "collected" / f"{asset}.json",
            {
                "collected_at": datetime.now(UTC).isoformat(),
                "result": result,
            },
        )

    await asyncio.gather(*(collect(asset) for asset in ("BTC", "ETH")))


async def main() -> None:
    directory = Path(os.environ["NEWS_STATE_DIR"])
    settings = NewsAgentSettings.load()
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await collect_once(settings, directory)
        except Exception:
            logger.exception("Public news collection cycle failed")
        await asyncio.sleep(max(5, 60 - (asyncio.get_running_loop().time() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
