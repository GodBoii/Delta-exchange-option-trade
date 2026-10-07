"""Replaceable read-only X collector. No developer keys or account mutations."""

import asyncio
import os
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Target
from .domain import Post


@dataclass(frozen=True)
class Collection:
    posts: list[Post]
    status: str = "ok"
    error_code: str | None = None


class Collector(Protocol):
    async def fetch(self, target: Target, limit: int) -> Collection: ...


def create_api(accounts_path: Path):
    os.environ["TWS_TELEMETRY"] = "0"
    from twscrape import API
    from twscrape.logger import logger

    # Upstream errors can contain account/session context. Our diagnostics use codes only.
    logger.remove()
    accounts_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return API(str(accounts_path), raise_when_no_account=True, wait_timeout=1)


def normalize(tweet) -> Post:
    media = [photo.url for photo in tweet.media.photos]
    media.extend(video.thumbnailUrl for video in tweet.media.videos)
    media.extend(animation.thumbnailUrl for animation in tweet.media.animated)
    return Post.from_dict(
        {
            "id": str(tweet.id),
            "author_id": str(tweet.user.id),
            "author": tweet.user.username,
            "text": tweet.rawContent,
            "published_at": tweet.date.isoformat(),
            "reply_to": str(tweet.inReplyToTweetId) if tweet.inReplyToTweetId else None,
            "quoted_id": str(tweet.quotedTweet.id) if tweet.quotedTweet else None,
            "reposted_id": str(tweet.retweetedTweet.id) if tweet.retweetedTweet else None,
            "media_urls": media,
        }
    )


class XCollector:
    def __init__(self, accounts_path: Path):
        self.api = create_api(accounts_path)
        self.user_ids: dict[str, int] = {}
        self.warning_seen = False
        from twscrape.logger import logger

        self.sink_id = logger.add(self._warning, level="WARNING")

    def _warning(self, message) -> None:
        self.warning_seen = True

    def close(self) -> None:
        from twscrape.logger import logger

        logger.remove(self.sink_id)

    async def fetch(self, target: Target, limit: int) -> Collection:
        self.warning_seen = False
        if target.kind == "user":
            if target.value not in self.user_ids:
                user = await self.api.user_by_login(target.value)
                if user is None:
                    return Collection([], "failed", "user_lookup_failed")
                self.user_ids[target.value] = user.id
            uid = self.user_ids[target.value]
            method = self.api.user_tweets_and_replies if target.include_replies else self.api.user_tweets
            stream = method(uid, limit=limit)
        elif target.kind == "list":
            stream = self.api.list_timeline(int(target.value), limit=limit)
        else:
            stream = self.api.search(target.value, limit=limit, kv={"product": "Latest"})
        posts: dict[str, Post] = {}
        async with aclosing(stream):
            async for tweet in stream:
                # User timelines may include conversation context from other authors.
                if target.kind == "user" and tweet.user.id != self.user_ids[target.value]:
                    continue
                item = normalize(tweet)
                posts[item.id] = item
                if len(posts) >= limit:
                    break
        items = list(posts.values())
        if self.warning_seen:
            return Collection(items, "partial" if items else "failed", "upstream_warning")
        if not items:
            # Twscrape may end an iterator on an upstream error as well as an empty timeline.
            # Do not establish a baseline or report verified healthy coverage in either case.
            return Collection([], "failed", "empty_unverified")
        return Collection(items)


async def add_session(accounts_path: Path, name: str, auth_token: str, ct0: str) -> None:
    api = create_api(accounts_path)
    async with asyncio.timeout(10):
        await api.pool.add_account_cookies(name, f"auth_token={auth_token}; ct0={ct0}")
    accounts_path.chmod(0o600)
