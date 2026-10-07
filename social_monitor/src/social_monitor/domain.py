"""Validate public posts and extract candidate references without inferring trades."""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
CASHTAG = re.compile(r"(?<!\w)\$([A-Za-z][A-Za-z0-9_]{0,14})\b")
EVM = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![A-Za-z0-9])")
BASE58 = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Post:
    id: str
    author_id: str
    author: str
    text: str
    published_at: str
    url: str
    reply_to: str | None = None
    quoted_id: str | None = None
    reposted_id: str | None = None
    media_urls: tuple[str, ...] = ()
    quoted_text: str | None = None
    reposted_text: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Post":
        for key in ("id", "author_id"):
            if not isinstance(data.get(key), str) or not data[key].isascii() or not data[key].isdigit():
                raise ValueError(f"{key} must be a numeric string")
        if not isinstance(data.get("author"), str) or not HANDLE.fullmatch(data["author"]):
            raise ValueError("invalid author handle")
        if not isinstance(data.get("text"), str) or len(data["text"]) > 100_000:
            raise ValueError("invalid post text")
        if not isinstance(data.get("published_at"), str):
            raise ValueError("published_at must be an ISO timestamp")
        published_at = parse_time(data["published_at"]).isoformat()
        ids = {}
        for key in ("reply_to", "quoted_id", "reposted_id"):
            value = data.get(key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value)):
                raise ValueError(f"invalid {key}")
            ids[key] = value
        media = data.get("media_urls", [])
        if not isinstance(media, (list, tuple)) or any(
            not isinstance(url, str) or not url.startswith("https://") for url in media
        ):
            raise ValueError("media_urls must contain HTTPS URLs")
        context = {}
        for key, id_key in (("quoted_text", "quoted_id"), ("reposted_text", "reposted_id")):
            value = data.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 100_000 or ids[id_key] is None):
                raise ValueError(f"invalid {key}")
            context[key] = value
        return cls(
            id=data["id"],
            author_id=data["author_id"],
            author=data["author"],
            text=data["text"],
            published_at=published_at,
            url=f"https://x.com/{data['author']}/status/{data['id']}",
            media_urls=tuple(media),
            **ids,
            **context,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def references(text: str) -> list[dict[str, str]]:
    found = {("cashtag", tag.upper()) for tag in CASHTAG.findall(text)}
    found.update(("evm_address_candidate", address.lower()) for address in EVM.findall(text))
    for match in BASE58.finditer(text):
        address = match.group()
        number = 0
        for char in address:
            number = number * 58 + ALPHABET.index(char)
        length = (number.bit_length() + 7) // 8 + len(address) - len(address.lstrip("1"))
        if length == 32:
            found.add(("solana_address_candidate", address))
    return [{"kind": kind, "value": value} for kind, value in sorted(found)]


def make_alert(post: Post, observed_at: datetime, max_age: int) -> dict | None:
    age = (observed_at - parse_time(post.published_at)).total_seconds()
    if age < -60 or age > max_age:
        return None
    refs = references(post.text)
    for context_id, text in ((post.quoted_id, post.quoted_text), (post.reposted_id, post.reposted_text)):
        if text:
            refs.extend({**ref, "source_post_id": context_id} for ref in references(text))
    return {
        "event": "post_observed",
        "post": post.to_dict(),
        "observed_at": observed_at.isoformat(),
        "publication_to_receipt_seconds": age,
        "references": refs,
        "interpretation": "candidate_mentions" if refs else "unclassified_post",
        "token_identity_verified": False,
    }
