# SPDX-License-Identifier: CC0-1.0

"""balances — every service's remaining money, in one place.

Tools → Balances asks each service that can say how much is left, all at
once, and gets back one plain sentence per service. Before this, OpenRouter's
balance was a button in Preferences and nothing else had one, so a second
paid service meant a second place to look — and nobody can remember which
window holds which number.

Every registered provider gets a line, even the ones with no balance check,
so nothing goes missing silently: a service you pay for that the list never
mentions reads exactly like a service that costs nothing.

Ollama is recognised by its ADDRESS, not its name. The name is whatever the
person typed into Manage providers, and "ollama-cloud", "ollama" and "glm"
are all reasonable choices for the same service.

No wx here, and the network call is passed in, so the tests can drive every
answer and every failure without a key or a connection.
"""

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

import llm_backend

OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OLLAMA_BALANCE_URL = "https://ollama.com/api/balance"


def _fetch_json(url, key, timeout=15):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def is_ollama_cloud(base):
    """True when a provider's address is Ollama's own cloud."""
    host = (urllib.parse.urlparse(base or "").hostname or "").lower()
    return host == "ollama.com" or host.endswith(".ollama.com")


def _money(x):
    try:
        return f"${float(x):.2f}"
    except (TypeError, ValueError):
        return None


def _day(iso):
    """'2026-11-15T09:30:00Z' -> '15 November'. None when unreadable."""
    try:
        d = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{d.day} {d.strftime('%B')}"


def describe_openrouter(data):
    """OpenRouter's /credits: {data: {total_credits, total_usage}}."""
    d = (data or {}).get("data") or {}
    total = float(d.get("total_credits") or 0.0)
    used = float(d.get("total_usage") or 0.0)
    return (f"{_money(total - used)} left "
            f"({_money(used)} spent of {_money(total)}).")


def describe_ollama(data):
    """Ollama's /api/balance. Two shapes: the credit plans report dollars
    (`included.balance_usd` of `allowance_usd`, renewing at `period.until`),
    and the older plans report percentages per session and per week."""
    data = data or {}
    parts = []
    inc = data.get("included") or {}
    no_plan = not float(inc.get("allowance_usd") or 0) and not float(inc.get("balance_usd") or 0)
    if "balance_usd" in inc and no_plan:
        # Pay as you go: no plan credit at all. "$0.00 of $0.00 included
        # credit left" is true and says nothing; the bought credit is the
        # whole answer.
        pass
    elif "balance_usd" in inc:
        left = _money(inc.get("balance_usd"))
        of = _money(inc.get("allowance_usd"))
        text = f"{left} of {of} included credit left" if of else f"{left} included credit left"
        renews = _day((inc.get("period") or {}).get("until"))
        if renews:
            text += f", renews {renews}"
        parts.append(text + ".")
    else:
        for label, key in (("this session", "session"), ("this week", "weekly")):
            block = inc.get(key) or {}
            if "remaining_percent" in block:
                text = f"{block['remaining_percent']:g}% left {label}"
                resets = _day(block.get("resets_at"))
                if resets:
                    text += f", resets {resets}"
                parts.append(text + ".")
    bought = _money((data.get("purchased") or {}).get("balance_usd"))
    if bought:
        parts.append(f"{bought} bought credit.")
    return " ".join(parts) or "it answered, but with nothing I recognise as a balance."


def _failure(e):
    """A failed check, in words someone can act on."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "the key was refused. Check it in Manage providers or Preferences."
        return f"the service answered with an error ({e.code})."
    if isinstance(e, urllib.error.URLError):
        return "couldn't reach it. Check the internet connection."
    return f"the check failed ({type(e).__name__})."


def left_openrouter(data):
    """Dollars left on OpenRouter, or None when the answer can't say."""
    try:
        d = (data or {}).get("data") or {}
        return float(d.get("total_credits") or 0) - float(d.get("total_usage") or 0)
    except (TypeError, ValueError, AttributeError):
        return None


def left_ollama(data):
    """Dollars left on Ollama's cloud: plan credit plus bought credit. None
    for the older percentage plans, which don't say in money."""
    try:
        data = data or {}
        inc = data.get("included") or {}
        bought = (data.get("purchased") or {}).get("balance_usd")
        if "balance_usd" not in inc and bought is None:
            return None
        return float(inc.get("balance_usd") or 0) + float(bought or 0)
    except (TypeError, ValueError, AttributeError):
        return None


def check_all(fetch=_fetch_json):
    """One line per service, OpenRouter first, then every added provider in
    alphabetical order (the order never shifts, so the lines stay where a
    screen reader left them). Never raises."""
    return [r["line"] for r in check_all_detailed(fetch)]


def check_all_detailed(fetch=_fetch_json):
    """The same check, keeping the amount beside each line:
    [{"label", "line", "left"}], where `left` is dollars or None when the
    service doesn't say (no key, no balance check, a failed check, or a
    plan that reports percentages). The background warning reads `left`;
    the window reads `line`."""
    results = []

    def run(label, url, key, describe, left_of):
        if not key:
            results.append({"label": label, "left": None,
                            "line": f"{label}: no key saved, so there's nothing to check."})
            return
        try:
            data = fetch(url, key)
            results.append({"label": label, "left": left_of(data),
                            "line": f"{label}: {describe(data)}"})
        except Exception as e:
            results.append({"label": label, "left": None,
                            "line": f"{label}: {_failure(e)}"})

    run("OpenRouter", OPENROUTER_CREDITS_URL,
        llm_backend.resolve_provider_key("openrouter"), describe_openrouter,
        left_openrouter)

    try:
        providers = llm_backend.api_providers()
    except Exception:
        providers = {}
    for name in sorted(n for n in providers if n != "openrouter"):
        base = (providers[name] or {}).get("base", "")
        if is_ollama_cloud(base):
            try:
                key = llm_backend.resolve_provider_key(name)
            except Exception:
                key = ""
            run(f"{name} (Ollama cloud)", OLLAMA_BALANCE_URL, key, describe_ollama,
                left_ollama)
        else:
            results.append({"label": name, "left": None,
                            "line": f"{name}: this service doesn't offer a balance check. "
                                    f"Look on its own website."})
    return results


def low_warnings(results, threshold, warned):
    """Which services to warn about, said once each.

    Returns (messages, still_warned), where still_warned maps each service
    to the level last said: "low" or "out". Each level is said once, and
    not again until the service has been back above the line — a warning
    every half hour about the same dollar would be the nagging that teaches
    someone to stop listening. Going from low to out is a new level, so it
    is said. A service whose amount is unknown keeps whatever state it had.
    `threshold` of 0 or less turns the low warning off; running out
    entirely is always said, because that is when replies start failing."""
    messages = []
    still = dict(warned or {})
    for r in results:
        label, left = r.get("label"), r.get("left")
        if left is None:
            continue
        if left <= 0.005:
            level = "out"
            text = (f"{label} is out of credit. Replies from it will fail "
                    f"until you add more.")
        elif threshold and threshold > 0 and left < threshold:
            level = "low"
            text = (f"{label} is down to {_money(left)}. "
                    f"Replies stop when it runs out.")
        else:
            still.pop(label, None)
            continue
        if still.get(label) != level:
            messages.append(text)
            still[label] = level
    return messages, still
