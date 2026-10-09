"""Bounded incremental X reads with quota protection and safe diagnostics."""

import asyncio
import os
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import Target
from .diagnostics import ACCESS_ERRORS, INFORMATIONAL_WARNINGS, warning_code
from .domain import Post


@dataclass(frozen=True)
class Collection:
    posts: list[Post]
    status: str = "ok"
    error_code: str | None = None
    details: dict = field(default_factory=dict)
    retry_after_seconds: float = 0


class Collector(Protocol):
    async def fetch(
        self,
        target: Target,
        limit: int,
        *,
        known_ids: frozenset[str] = frozenset(),
        reconcile: bool = True,
    ) -> Collection: ...


def create_api(accounts_path: Path):
    os.environ["TWS_TELEMETRY"] = "0"
    from twscrape import API
    from twscrape.logger import logger

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
            "quoted_text": tweet.quotedTweet.rawContent if tweet.quotedTweet else None,
            "reposted_text": tweet.retweetedTweet.rawContent if tweet.retweetedTweet else None,
        }
    )


class XCollector:
    def __init__(self, accounts_path: Path, *, page_size: int = 20, max_pages: int = 4):
        self.api = create_api(accounts_path)
        self.user_ids: dict[str, int] = {}
        self.warning_codes: set[str] = set()
        self.not_before = 0.0
        self.page_size, self.max_pages = page_size, max_pages
        from twscrape.logger import logger
        from twscrape.models import parse_tweets

        self.parse_page = parse_tweets
        self.sink_id = logger.add(self._warning, level="WARNING")

    def _warning(self, message) -> None:
        self.warning_codes.add(warning_code(message.record))

    def close(self) -> None:
        from twscrape.logger import logger

        logger.remove(self.sink_id)

    async def _stream(self, target: Target):
        options = {"limit": -1, "kv": {"count": self.page_size}}
        if target.kind == "user":
            if target.value not in self.user_ids:
                user = await self.api.user_by_login(target.value)
                if user is None:
                    return None
                self.user_ids[target.value] = user.id
            uid = self.user_ids[target.value]
            method = self.api.user_tweets_and_replies_raw if target.include_replies else self.api.user_tweets_raw
            return method(uid, **options)
        if target.kind == "list":
            return self.api.list_timeline_raw(int(target.value), **options)
        options["kv"]["product"] = "Latest"
        return self.api.search_raw(target.value, **options)

    def _quota(self, response, details: dict) -> None:
        for header, key in (
            ("x-rate-limit-limit", "quota_limit"),
            ("x-rate-limit-remaining", "quota_remaining"),
            ("x-rate-limit-reset", "quota_reset_at"),
        ):
            value = response.headers.get(header)
            if value is not None and str(value).isdigit():
                details[key] = int(value)
        if details.get("quota_remaining", 100) <= 5 and details.get("quota_reset_at", 0) > time.time():
            self.not_before = max(self.not_before, details["quota_reset_at"] + 1)
        if response.status_code == 429:
            retry = response.headers.get("retry-after", "60")
            seconds = int(retry) if str(retry).isdigit() else 60
            self.not_before = max(self.not_before, time.time() + max(1, seconds))

    def _result(self, posts: dict[str, Post], details: dict, error: str | None = None) -> Collection:
        details["warnings"] = sorted(self.warning_codes)
        problems = self.warning_codes - INFORMATIONAL_WARNINGS
        for code in sorted(ACCESS_ERRORS):
            if code in problems:
                error = code
        if error is None and problems:
            error = sorted(problems)[0]
        items = sorted(posts.values(), key=lambda post: post.published_at)
        if items:
            details["newest_published_at"] = max(post.published_at for post in items)
        if not items and error is None:
            error = "empty_unverified"
        status = "partial" if error and items else "failed" if error else "ok"
        return Collection(items, status, error, details, max(0, self.not_before - time.time()))

    async def fetch(
        self,
        target: Target,
        limit: int,
        *,
        known_ids: frozenset[str] = frozenset(),
        reconcile: bool = True,
    ) -> Collection:
        from twscrape.accounts_pool import NoAccountError

        self.warning_codes.clear()
        details = {"pages": 0, "mode": "reconcile" if reconcile else "incremental", "known_overlap": False}
        posts: dict[str, Post] = {}
        if self.not_before > time.time():
            return self._result(posts, details, "rate_limited")
        try:
            stream = await self._stream(target)
            if stream is None:
                return self._result(posts, details, "user_lookup_failed")
            async with aclosing(stream):
                async for response in stream:
                    details["pages"] += 1
                    self._quota(response, details)
                    if response.status_code != 200:
                        code = {401: "authentication_required", 403: "access_blocked", 429: "rate_limited"}
                        return self._result(posts, details, code.get(response.status_code, "http_error"))
                    data = response.json()
                    if not isinstance(data, dict):
                        return self._result(posts, details, "invalid_response")
                    errors = data.get("errors") or []
                    if not isinstance(errors, list):
                        return self._result(posts, details, "invalid_response")
                    codes = [item.get("code") for item in errors if isinstance(item, dict)]
                    if any(code in {32, 89, 326} for code in codes):
                        return self._result(posts, details, "authentication_required")
                    if not isinstance(data.get("data"), dict):
                        return self._result(posts, details, "invalid_response")
                    if codes:
                        self.warning_codes.add("upstream_error")
                    page = []
                    for tweet in self.parse_page(data):
                        if target.kind == "user" and tweet.user.id != self.user_ids[target.value]:
                            continue
                        item = normalize(tweet)
                        page.append(item)
                        posts[item.id] = item
                        if len(posts) >= limit:
                            break
                    overlap = sum(post.id in known_ids for post in page)
                    details["known_overlap"] = details["known_overlap"] or overlap >= 3
                    if len(posts) >= limit:
                        details["stop_reason"] = "batch_limit"
                        break
                    if not reconcile and details["known_overlap"]:
                        details["stop_reason"] = "known_overlap"
                        break
                    if details["pages"] >= self.max_pages:
                        details["stop_reason"] = "page_limit"
                        break
                    if self.not_before > time.time():
                        details["stop_reason"] = "quota_reserve"
                        break
        except NoAccountError:
            accounts = await self.api.pool.get_all()
            if any(account.active for account in accounts):
                locks = [lock.timestamp() for account in accounts if account.active for lock in account.locks.values()]
                self.not_before = max(
                    self.not_before, min((lock for lock in locks if lock > time.time()), default=time.time() + 30)
                )
                return self._result(posts, details, "rate_limited")
            return self._result(posts, details, "authentication_required")
        return self._result(posts, details)


async def add_session(accounts_path: Path, name: str, auth_token: str, ct0: str) -> None:
    api = create_api(accounts_path)
    async with asyncio.timeout(10):
        await api.pool.add_account_cookies(name, f"auth_token={auth_token}; ct0={ct0}")
    accounts_path.chmod(0o600)
