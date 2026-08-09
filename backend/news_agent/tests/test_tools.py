from __future__ import annotations

import pytest

from news_agent.tools import (
    UnsafeUrlError,
    canonicalize_url,
    classify_source_url,
    parse_article_html,
    validate_public_url,
)

ARTICLE_HTML = """
<!doctype html>
<html>
  <head>
    <title>Fallback title</title>
    <meta property="og:site_name" content="Example News">
    <meta property="og:image" content="/images/lead.jpg?utm_source=test">
    <link rel="canonical" href="https://news.example.com/story?id=7&utm_campaign=test">
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Bitcoin reacts to a policy announcement",
        "datePublished": "2026-08-09T10:30:00Z",
        "dateModified": "2026-08-09T10:45:00Z",
        "author": {"@type": "Person", "name": "A. Reporter"},
        "image": {"url": "https://cdn.example.com/lead.jpg", "width": 1200, "height": 630}
      }
    </script>
  </head>
  <body>
    <nav>This navigation must not be extracted.</nav>
    <article>
      <p>Bitcoin moved after officials published a material policy announcement affecting digital assets.</p>
      <p>Market participants were still waiting for the complete legal text and its effective date.</p>
      <img src="/images/chart.png" alt="Bitcoin market chart" width="800" height="450">
    </article>
  </body>
</html>
"""


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    value = canonicalize_url("HTTPS://Example.com/story?utm_source=x&id=7#section")
    assert value == "https://example.com/story?id=7"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost/",
        "http://[::1]/",
        "file:///etc/passwd",
        "https://user:password@example.com/article",
        "https://example.com:8443/article",
    ],
)
def test_validate_public_url_blocks_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url)


def test_parse_article_extracts_metadata_text_and_images() -> None:
    article = parse_article_html(ARTICLE_HTML, "https://news.example.com/original", 5_000)
    assert article["title"] == "Bitcoin reacts to a policy announcement"
    assert article["canonical_url"] == "https://news.example.com/story?id=7"
    assert article["publisher"] == "Example News"
    assert article["author"] == "A. Reporter"
    assert article["published_at"] == "2026-08-09T10:30:00Z"
    assert "material policy announcement" in article["text"]
    assert "navigation" not in article["text"]
    assert article["images"][0]["image_url"] == "https://cdn.example.com/lead.jpg"
    assert any(image["alt_text"] == "Bitcoin market chart" for image in article["images"])


def test_source_classification_is_transparent_and_subdomain_aware() -> None:
    assert classify_source_url("https://www.whitehouse.gov/news/")["source_class"] == "primary_official"
    assert classify_source_url("https://markets.reuters.com/article")["source_class"] == "licensed_financial_news"
    assert classify_source_url("https://random.example/article")["source_class"] == "unknown"
