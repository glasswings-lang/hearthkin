# SPDX-License-Identifier: CC0-1.0

"""Brave Search provider for the web_search tool.

API docs: https://api.search.brave.com/app/documentation/web-search/get-started

Auth: X-Subscription-Token header. Key resolves via
`llm_backend.resolve_provider_key('brave')` — env var BRAVE_API_KEY first,
then `~/.ai_programs/brave_key.json`. User configures the key in
hearthkin's Preferences → Connections (writes the JSON file).

Pricing: Brave's free tier covers 2000 queries/month, capped at 1 per
second. That's the typical-personal-use shape; this implementation
doesn't try to be clever about rate-limiting because Brave returns 429
with a clear message and the model can wait.

Standard web-search shape only. Brave's specialized llm-context endpoint
exists but isn't implemented here — it's Brave-specific (other providers
won't have an analog), and the regular web search is already enough."""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import llm_backend


_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = 20  # seconds
# Cap the response body read. Brave is a trusted HTTPS endpoint, but an
# unbounded resp.read() would OOM the worker on a MITM'd / buggy oversized
# response (audit H3); fetch_url already caps its body, this brings the
# search path to parity. 8 MB is far beyond any real search-results JSON.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Brave's freshness filter accepts these four discrete short values plus
# arbitrary date-range syntax. The short values map to a recent-window
# filter; the date-range form is YYYY-MM-DDtoYYYY-MM-DD literally.
_FRESHNESS_VALUES = {"day", "week", "month", "year"}
_FRESHNESS_SHORT = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}

# YYYY-MM-DD validation for date_after / date_before. Regex-only — we
# don't fully validate calendar correctness (e.g. Feb 30) because Brave
# will reject obviously wrong dates and the model can fix on the retry.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Brave returns title and description fields with HTML entities encoded
# (&quot;, &amp;, &#x27;, etc.) and frequently wraps matched query terms
# in <strong>...</strong> tags. Both leak through to the model as ugly
# noise if not decoded. _clean_brave_text undoes both.
_STRONG_TAG_RE = re.compile(r"</?strong>", re.IGNORECASE)


def _clean_brave_text(text):
    """Decode HTML entities + strip <strong> highlight tags from Brave's
    title/description text. Returns the cleaned string, stripped of
    leading/trailing whitespace. Empty input → empty output."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _STRONG_TAG_RE.sub("", text)
    return text.strip()


def search(query, *, max_results=5, freshness=None,
           country=None, language=None,
           date_after=None, date_before=None):
    """Run a Brave Search query and return normalized results.

    Returns either a list of {title, url, snippet, site, published} dicts
    or a "web_search: ..." string describing the error. Caller (the
    model-facing tool wrapper) formats the list into text and surfaces
    the error string directly.

    Parameters:
      query: search string. Required.
      max_results: how many results to return (Brave's `count` param,
        clamped 1..20).
      freshness: optional time filter — one of "day", "week", "month",
        "year". Other values are silently dropped. If date_after or
        date_before is provided, those override this.
      country: 2-letter ISO country code (e.g. "US", "GB", "JP"). Biases
        results toward that region. Invalid codes are silently dropped.
      language: 2-letter ISO 639-1 language code (e.g. "en", "fr", "ja").
        Maps to Brave's search_lang param. Invalid values silently
        dropped.
      date_after: YYYY-MM-DD. Only return results published on or after
        this date. Combined with date_before becomes a Brave date-range
        freshness filter.
      date_before: YYYY-MM-DD. Only return results published on or
        before this date.
    """
    if not isinstance(query, str) or not query.strip():
        return "web_search: query was empty."
    key = llm_backend.resolve_provider_key("brave")
    if not key:
        return (
            "web_search: no Brave Search API key configured. Set one in "
            "hearthkin's Preferences → Connections, or via the "
            "BRAVE_API_KEY environment variable."
        )
    count = 5
    try:
        n = int(max_results)
        if n >= 1:
            count = min(20, n)
    except (TypeError, ValueError):
        count = 5
    params = {"q": query.strip(), "count": str(count)}

    # Country filter: Brave expects a 2-letter code like "US" or "GB".
    if isinstance(country, str):
        c = country.strip().upper()
        if len(c) == 2 and c.isalpha():
            params["country"] = c

    # Language filter -> Brave's search_lang param. Short codes only
    # (ISO 639-1: 2-letter). Brave also accepts a few non-639-1 codes
    # like "zh-hans" but we keep this conservative — model passes "en",
    # "fr", "es", etc.
    if isinstance(language, str):
        lang = language.strip().lower()
        if 2 <= len(lang) <= 8 and re.match(r"^[a-z]{2,3}(-[a-z]{2,8})?$", lang):
            params["search_lang"] = lang

    # Date range — overrides freshness when set. Brave accepts the
    # literal string "YYYY-MM-DDtoYYYY-MM-DD" in the freshness slot.
    # Either bound can be omitted; we fill with a permissive default
    # for the missing end so the API still gets a valid range.
    after_ok = isinstance(date_after, str) and _DATE_RE.match(date_after.strip())
    before_ok = isinstance(date_before, str) and _DATE_RE.match(date_before.strip())
    if after_ok or before_ok:
        start = date_after.strip() if after_ok else "1990-01-01"
        end = date_before.strip() if before_ok else "2099-12-31"
        params["freshness"] = f"{start}to{end}"
    elif isinstance(freshness, str):
        f = freshness.strip().lower()
        if f in _FRESHNESS_VALUES:
            params["freshness"] = _FRESHNESS_SHORT[f]
    url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return ("web_search: Brave returned an unexpectedly large "
                        "response; aborted to protect memory.")
            data = json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            return (
                "web_search: Brave rejected the API key (HTTP "
                f"{e.code}). Re-check it in Preferences → Connections."
            )
        if e.code == 429:
            return (
                "web_search: Brave rate-limited the request (HTTP 429). "
                "Free tier is 1 request/second and 2000/month — wait a "
                "moment and retry."
            )
        return f"web_search: Brave HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"web_search: network error reaching Brave: {e.reason}"
    except Exception as e:
        return f"web_search: Brave call failed: {type(e).__name__}: {e}"

    raw_results = (data.get("web") or {}).get("results") or []
    results = []
    for entry in raw_results[:count]:
        if not isinstance(entry, dict):
            continue
        url_val = entry.get("url") or ""
        # Brave returns published date under a few different keys
        # depending on the result type (page_age for news, age for some
        # blogs). Prefer page_age which is the most consistently present
        # and is already a human-readable string ("1 day ago" etc.).
        published = (
            entry.get("page_age")
            or entry.get("age")
            or ""
        )
        results.append({
            "title": _clean_brave_text(entry.get("title") or ""),
            "url": url_val,
            "snippet": _clean_brave_text(entry.get("description") or ""),
            "site": _site_of(url_val),
            "published": str(published).strip(),
        })
    return results


def _site_of(url):
    """Hostname for display. Returns empty string on parse failure rather
    than raising — search results with malformed URLs still come back to
    the model, just without a site label."""
    if not url:
        return ""
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
