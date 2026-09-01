# SPDX-License-Identifier: CC0-1.0

"""Run a web search and return ranked results.

Provider-agnostic: dispatches to whatever provider is named on the call
(default 'brave'). Each provider lives in `tools/_search_providers/`
behind a uniform `search(query, *, max_results, freshness)` signature.
Adding more providers is a one-file drop — see that package's docstring.

Result formatting is centralized here so all providers produce the same
shape regardless of upstream differences in field names."""

from . import _search_providers


# Reasonable per-call output cap so a high max_results doesn't blow up
# context. Each formatted result is ~200-400 chars, so 30K covers ~80
# results worst-case — way more than max_results would ever ask for.
_MAX_OUTPUT_CHARS = 30000


def web_search(query: str,
               max_results: int = 5,
               freshness: str = "",
               country: str = "",
               language: str = "",
               date_after: str = "",
               date_before: str = "",
               provider: str = "brave") -> str:
    """Run a web search and return the top results as a numbered list.
    Use this when you need to find current information — news, recent
    docs, anything that isn't in your training data or in your kin
    memory yet. Pair with `fetch_url` to read the full content of a
    promising result.

    Parameters:
      query: what to search for. A few keywords or a natural-language
        question both work. Required.
      max_results: how many results to return. Default 5. Most providers
        cap at 20.
      freshness: optional time filter for recent results. One of "day",
        "week", "month", "year" — anything else is ignored. Use this
        when you specifically want recent content; leave blank for
        general queries. Overridden by date_after / date_before when
        either is set.
      country: optional 2-letter ISO country code (e.g. "US", "GB",
        "JP", "FR") to bias results toward a region. Useful for local
        news, regional services. Leave blank for global results.
      language: optional 2-letter ISO 639-1 language code (e.g. "en",
        "fr", "ja") to prefer results in that language. Leave blank
        for the provider's default.
      date_after: optional YYYY-MM-DD. Only return results published on
        or after this date. Use for "what happened since X" queries.
      date_before: optional YYYY-MM-DD. Only return results published
        on or before this date. Combine with date_after for a window.
      provider: which search backend to use. Default "brave" (Brave
        Search). The user configures the API key for whichever provider
        is selected in hearthkin's Preferences → Connections.

    Returns a numbered list of results — title, URL, site, published
    date when available, and snippet for each — capped at ~30K chars
    of output. On configuration or network failure, returns a brief
    error string starting with "web_search:" explaining what's wrong
    (no exception). When the user has not set the provider's API key,
    the error explicitly says where to configure it.
    """
    if not isinstance(query, str) or not query.strip():
        return "web_search: query was empty."

    provider_fn = _search_providers.get(provider)
    if provider_fn is None:
        available = ", ".join(_search_providers.list_available()) or "(none)"
        return (
            f"web_search: unknown provider {provider!r}. "
            f"Available: {available}."
        )

    result = provider_fn(
        query=query,
        max_results=max_results,
        freshness=(freshness or None),
        country=(country or None),
        language=(language or None),
        date_after=(date_after or None),
        date_before=(date_before or None),
    )
    # Provider already returned an error string — surface it verbatim
    # so the model sees the upstream error message.
    if isinstance(result, str):
        return result
    if not result:
        provider_label = provider.strip().lower() or "the provider"
        return (
            f"web_search: no results for {query!r} from {provider_label}. "
            f"Try fewer or different keywords."
        )

    lines = []
    for i, hit in enumerate(result, 1):
        title = (hit.get("title") or "(no title)").strip()
        url = (hit.get("url") or "").strip()
        snippet = (hit.get("snippet") or "").strip()
        site = (hit.get("site") or "").strip()
        published = (hit.get("published") or "").strip()
        site_tag = f" [{site}]" if site else ""
        published_tag = f" — {published}" if published else ""
        # One result per block: "1. Title [site] — published\n   URL\n   snippet"
        block = f"{i}. {title}{site_tag}{published_tag}\n   {url}"
        if snippet:
            block += f"\n   {snippet}"
        lines.append(block)
    out = "\n\n".join(lines)
    if len(out) > _MAX_OUTPUT_CHARS:
        out = out[:_MAX_OUTPUT_CHARS] + "\n\n[truncated; fewer max_results returns shorter output]"
    return out
