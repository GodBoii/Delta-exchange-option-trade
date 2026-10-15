"""Conservative, explainable text screening. Relevance is not a trade direction."""

import re

from .domain import Post, references

VERSION = 1
# These identify named assets for research, not a verified chain or contract.
ASSETS = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana",),
    "BNB": ("bnb", "bnbchain"),
    "DOGE": ("dogecoin", "doge"),
    "SHIB": ("shiba inu", "shib"),
    "PEPE": (),
    "WIF": ("dogwifhat",),
    "BONK": (),
    "FLOKI": (),
}
TOPICS = {
    "crypto": r"\b(?:crypto(?:currency|currencies)?|blockchain|onchain|on-chain|web3|defi|memecoins?|meme coins?)\b",
    "protocol": r"\b(?:zkevm|zksync|erc-?20|eip-?\d{3,}|staking|stablecoins?|tokenomics|airdrops?)\b",
    "venues": r"\b(?:binance|coinbase|uniswap|hyperliquid|pump\.fun|raydium)\b",
}
EVENTS = {
    "security": r"\b(?:hack(?:ed)?|exploit(?:ed)?|scam|rug pull|stolen|breach)\b",
    "listing": r"\b(?:list(?:ing|ed)?|delist(?:ing|ed)?)\b",
    "regulation_etf": r"\b(?:etfs?|sec|approval|regulat\w*)\b",
    "flows": r"\b(?:inflows?|outflows?|deposit\w*|withdraw\w*|bought|sold|purchas\w*|liquidat\w*)\b",
}


def classify(post: Post) -> dict:
    evidence = []
    assets: set[str] = set()
    unresolved: set[str] = set()
    categories: set[str] = set()
    for source, text in (
        (post.id, post.text),
        (post.quoted_id, post.quoted_text),
        (post.reposted_id, post.reposted_text),
    ):
        if not text:
            continue
        # URLs and @handles alone are not evidence of an asset mention.
        clean = re.sub(r"https?://\S+|@[A-Za-z0-9_]+", " ", text)
        refs = references(clean)
        tags = {ref["value"] for ref in refs if ref["kind"] == "cashtag"}
        hits = []
        for symbol, aliases in ASSETS.items():
            if symbol in tags or any(
                re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", clean, re.I) for alias in aliases
            ):
                assets.add(symbol)
                hits.append(symbol)
        topic_hits = [name for name, pattern in TOPICS.items() if re.search(pattern, clean, re.I)]
        addresses = [ref for ref in refs if ref["kind"] != "cashtag"]
        unresolved.update(tags - ASSETS.keys())
        if hits or topic_hits or addresses:
            evidence.append(
                {"source_post_id": source, "assets": hits, "topics": topic_hits, "address_candidates": addresses}
            )
            categories.update(name for name, pattern in EVENTS.items() if re.search(pattern, clean, re.I))
    relevant = bool(evidence)
    return {
        "version": VERSION,
        "relevance": "crypto"
        if relevant
        else "uncertain"
        if unresolved or post.media_urls or post.reply_to
        else "unrelated",
        "assets": sorted(assets),
        "unresolved_cashtags": sorted(unresolved),
        "evidence": evidence,
        "categories": sorted(categories) or (["commentary"] if relevant else []),
        "direction": "unclear",
        "direction_reason": "Text screening does not infer market direction or endorsement",
        "token_identity_verified": False,
    }
