"""Bounded keyless Atom collection. Live use requires permission for this research route."""

import hashlib
import html
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .domain import FULLNAME, normalize, timestamp
from .storage import Store

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class Page:
    children: list[dict]
    after: str | None
    received_at: str
    provenance: dict
    window_only: bool = False


class CollectionError(Exception):
    def __init__(self, code: str, cooldown_until: float = 0):
        super().__init__(code)
        self.code = code
        self.cooldown_until = cooldown_until


def parse_atom(payload: bytes, subreddit: str, received_at: str, endpoint: str) -> Page:
    if len(payload) > 4_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise CollectionError("invalid_feed")
    try:
        root = ET.fromstring(payload)
        if root.tag != ATOM + "feed":
            raise ValueError("not Atom")
        children = []
        for entry in root.findall(ATOM + "entry"):
            native = entry.findtext(ATOM + "id", "")
            if not FULLNAME.fullmatch(native) or not native.startswith(("t1_", "t3_")):
                raise ValueError("missing native ID")
            published = entry.findtext(ATOM + "published")
            updated = entry.findtext(ATOM + "updated")
            if not published and not updated:
                raise ValueError("missing timestamp")
            link = entry.find(ATOM + "link")
            url = urllib.parse.urlsplit(link.get("href", "") if link is not None else "")
            if url.scheme != "https" or url.netloc not in {"www.reddit.com", "reddit.com"}:
                raise ValueError("invalid link")
            author = entry.findtext(ATOM + "author/" + ATOM + "name")
            content = entry.findtext(ATOM + "content", "")
            body = html.unescape(re.sub(r"<[^>]*>", " ", content))
            parts = url.path.split("/")
            thread = "t3_" + parts[4] if len(parts) > 4 and parts[3] == "comments" else None
            data = {
                "name": native,
                "id": native.split("_", 1)[1],
                "subreddit": subreddit,
                "subreddit_id": None,
                "author_fullname": None,
                "author": author,
                "permalink": url.path,
                "created_utc": timestamp(published or updated).timestamp(),
                "title": entry.findtext(ATOM + "title", ""),
                "body": body,
                "link_id": thread,
                "parent_id": None,
                "feed_updated_at": updated,
                "publication_time_verified": bool(published),
                "raw_atom_entry": ET.tostring(entry, encoding="unicode"),
            }
            children.append({"kind": native[:2], "data": data})
        after = None
        for link in root.findall(ATOM + "link"):
            if link.get("rel") == "next":
                url = urllib.parse.urlsplit(link.get("href", ""))
                if url.scheme != "https" or url.netloc != "www.reddit.com" or url.path != endpoint:
                    raise ValueError("unsafe continuation")
                after = urllib.parse.parse_qs(url.query).get("after", [None])[0]
                if not isinstance(after, str) or not FULLNAME.fullmatch(after):
                    raise ValueError("invalid continuation")
        return Page(
            children,
            after,
            received_at,
            {
                "mode": "rss",
                "endpoint": endpoint,
                "response_sha256": hashlib.sha256(payload).hexdigest(),
                "timestamp_source": "atom_published_or_updated",
                "author_ids_available": False,
                "subreddit_ids_available": False,
                "thread_context_complete": False,
            },
            window_only=True,
        )
    except (ET.ParseError, ValueError, TypeError, OverflowError, IndexError):
        raise CollectionError("invalid_feed") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("redirect_refused")


class RSSClient:
    def __init__(
        self,
        approval_reference: str,
        user_agent: str,
        spacing: float = 2,
        clock=time.time,
        sleep=time.sleep,
        opener=None,
    ):
        if not approval_reference.strip():
            raise ValueError("research_route_approval_required")
        if not user_agent or "reddit-monitor" not in user_agent or len(user_agent) > 200 or "\n" in user_agent:
            raise ValueError("descriptive_user_agent_required")
        self.user_agent, self.spacing = user_agent, spacing
        self.clock, self.sleep = clock, sleep
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.next_request = 0.0

    def fetch(self, subreddit: str, stream: str, after: str | None, size: int) -> Page:
        endpoint = f"/r/{subreddit}/{'new' if stream == 'posts' else 'comments'}/.rss"
        params = {"limit": size}
        if after:
            params["after"] = after
        url = "https://www.reddit.com" + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            self.sleep(max(0, self.next_request - self.clock()))
            self.next_request = self.clock() + self.spacing
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "application/atom+xml"}
            )
            try:
                with self.opener.open(request, timeout=20) as response:
                    payload = response.read(4_000_001)
                    receipt = datetime.fromtimestamp(self.clock(), UTC).isoformat()
                    return parse_atom(payload, subreddit, receipt, endpoint)
            except urllib.error.HTTPError as error:
                code = error.code
                if code == 429:
                    retry = error.headers.get("Retry-After", "120")
                    try:
                        delay = max(120, float(retry))
                        if not math.isfinite(delay):
                            delay = 86400
                    except ValueError:
                        try:
                            delay = max(120, parsedate_to_datetime(retry).timestamp() - self.clock())
                        except (ValueError, TypeError, OverflowError):
                            delay = 3600
                    raise CollectionError("quota_cooldown", self.clock() + delay) from None
                if code in {401, 403}:
                    raise CollectionError("access_denied") from None
                if code in {404, 410}:
                    raise CollectionError("unavailable") from None
                if code < 500:
                    raise CollectionError("http_rejected") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                pass  # Bounded transport retry. No exception text or URL is retained.
            if attempt < 2:
                self.sleep(2 ** (attempt + 1))
        raise CollectionError("transport_failed", self.clock() + 120)


def collect(store: Store, client, subreddit: str, stream: str, page_limit: int, size: int, now: str) -> str:
    name = f"{subreddit.lower()}:{stream}"
    state = store.target(name, now)
    if max(state["cooldown_until"], store.global_cooldown()) > timestamp(now).timestamp():
        return "cooldown"
    state = store.begin(name, now)
    cursor = state["cursor"] if state["pending"] else None
    seen_cursors = {cursor}
    received = now
    saved = 0
    try:
        for _ in range(page_limit):
            page = client.fetch(subreddit, stream, cursor, size)
            received = page.received_at
            try:
                received = timestamp(received).isoformat()
            except (ValueError, TypeError, AttributeError):
                received = now
                raise CollectionError("invalid_receipt_time") from None
            if not isinstance(page.children, list) or len(page.children) > 100:
                raise CollectionError("invalid_page")
            if page.after is not None and (not isinstance(page.after, str) or not FULLNAME.fullmatch(page.after)):
                raise CollectionError("invalid_cursor")
            if page.after is not None and page.after in seen_cursors:
                raise CollectionError("pagination_stalled")
            normalized, invalid = [], False
            for child in page.children:
                try:
                    envelope = normalize(child, subreddit, received, page.provenance)
                    normalized.append(envelope)
                except (ValueError, TypeError, OverflowError, OSError):
                    invalid = True
            store.save_page(name, normalized, received, cursor, cursor if invalid else page.after, page.provenance)
            saved += len(normalized)
            if invalid:
                raise CollectionError("invalid_items")
            if page.after is None:
                status = "window_unverified" if page.window_only else "bounded_read"
                if not page.children:
                    status = "empty_unverified"
                store.finish(name, received, status, True)
                return status
            cursor = page.after
            seen_cursors.add(cursor)
        store.finish(name, received, "page_limit", False)
        return "page_limit"
    except CollectionError as error:
        status = ("partial_" if saved else "failed_") + error.code
        store.finish(name, received, status, False, error.cooldown_until)
        if error.code == "access_denied":
            raise
        return status


class ReplayClient:
    """Synthetic response tapes exercise the same page checkpoint path without network."""

    def __init__(self, path):
        with path.open(encoding="utf-8") as handle:
            self.records = [json.loads(line) for line in handle if line.strip()]
        self.position = 0

    def fetch(self, subreddit: str, stream: str, after: str | None, size: int) -> Page:
        if self.position >= len(self.records):
            raise CollectionError("replay_exhausted")
        record = self.records[self.position]
        self.position += 1
        if (
            record["subreddit"].lower() != subreddit.lower()
            or record["stream"] != stream
            or record.get("cursor_in") != after
        ):
            raise CollectionError("replay_cursor_mismatch")
        if "error" in record:
            if record["error"] not in {"transport_failed", "quota_cooldown", "access_denied", "unavailable"}:
                raise CollectionError("invalid_replay_error")
            raise CollectionError(record["error"], record.get("cooldown_until", 0))
        return Page(
            record["children"],
            record.get("after"),
            record["received_at"],
            {
                "mode": "synthetic_replay",
                "tape_record": self.position,
                "endpoint": f"/r/{subreddit}/{stream}",
                "fixture_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
            },
        )
