# SPDX-License-Identifier: CC0-1.0

"""Web-search provider registry.

Each provider lives in its own file under this directory and exposes a
single function `search(query, *, max_results, freshness, **opts)` that
returns either:

  - a list of dicts shaped {"title": str, "url": str, "snippet": str,
    "site": str}, or
  - a string starting with "web_search:" describing why the call failed
    (no key configured, HTTP error, etc.) — surfaced verbatim to the
    model so it knows what's wrong.

The registry below maps provider name → callable. Adding a provider:
  1. drop `<name>.py` in this directory with a top-level `search`
     function matching the signature above
  2. add it to `_REGISTRY` here
That's it — no changes needed elsewhere in the tools/ tree. The
model-facing tool in `tools/web_search.py` reads `_REGISTRY` to dispatch."""

from . import brave


_REGISTRY = {
    "brave": brave.search,
}


def get(provider_name):
    """Return the provider callable for `provider_name`, or None if no
    such provider is registered."""
    if not isinstance(provider_name, str):
        return None
    return _REGISTRY.get(provider_name.strip().lower())


def list_available():
    """Names of all registered providers, sorted alphabetically. Used by
    the Settings dropdown when a per-kin provider selector lands."""
    return sorted(_REGISTRY.keys())
