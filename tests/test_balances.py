# SPDX-License-Identifier: CC0-1.0
"""Guard test: Tools → Balances says what's left on every service.

OpenRouter's balance used to be one button in Preferences, and nothing else
had one. Adding Ollama's cloud as a second paid service would have meant a
second place to look, so every balance now comes from one place:
`balances.check_all`, one plain line per service.

Pinned here, with the network replaced by a stand-in (no key, no connection):
  - OpenRouter's answer reads as money left, spent and total;
  - Ollama's credit-plan answer reads dollars, the allowance and the renewal
    date, and the bought credit separately; its older percentage shape reads too;
  - Ollama is recognised by ADDRESS, whatever the person named it;
  - every added provider gets a line, including the ones with no balance
    check, so a paid service never vanishes from the list;
  - a refused key and a dead connection each read as a sentence someone can
    act on, never a traceback;
  - the old Preferences button is gone and the menu item is wired.

Run: python tests/test_balances.py
"""

import os
import sys
import tempfile
import urllib.error
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="balances-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import balances      # noqa: E402
import llm_backend   # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


OR_ANSWER = {"data": {"total_credits": 20.0, "total_usage": 15.88}}
OLLAMA_ANSWER = {"included": {"balance_usd": 7.25, "allowance_usd": 20,
                              "period": {"from": "2026-10-15T09:30:00Z",
                                         "until": "2026-11-15T09:30:00Z"}},
                 "purchased": {"balance_usd": 5}}
OLLAMA_LEGACY = {"included": {"session": {"remaining_percent": 80, "resets_at": "2026-10-28T01:00:00Z"},
                              "weekly": {"remaining_percent": 42.5, "resets_at": "2026-11-02T00:00:00Z"}}}


def with_setup(providers, keys, fetch):
    saved = (llm_backend.api_providers, llm_backend.resolve_provider_key)
    llm_backend.api_providers = lambda: providers
    llm_backend.resolve_provider_key = lambda name: keys.get(name, "")
    try:
        return balances.check_all(fetch=fetch)
    finally:
        llm_backend.api_providers, llm_backend.resolve_provider_key = saved


def test_descriptions():
    check("openrouter reads left, spent and total",
          balances.describe_openrouter(OR_ANSWER) == "$4.12 left ($15.88 spent of $20.00).")
    text = balances.describe_ollama(OLLAMA_ANSWER)
    check("ollama reads dollars of the allowance", "$7.25 of $20.00 included credit left" in text)
    check("ollama names the renewal day", "renews 15 November" in text)
    check("ollama reads bought credit separately", "$5.00 bought credit." in text)
    payg = balances.describe_ollama({"included": {"balance_usd": 0, "allowance_usd": 0,
                                                  "period": {"until": "2026-11-27T00:00:00Z"}},
                                     "purchased": {"balance_usd": 4.81}})
    check("pay as you go: just the bought credit", payg == "$4.81 bought credit.")
    legacy = balances.describe_ollama(OLLAMA_LEGACY)
    check("older plan: session and week as percentages",
          "80% left this session" in legacy and "42.5% left this week" in legacy)
    check("an unrecognised answer says so",
          "nothing I recognise" in balances.describe_ollama({}))


def test_recognised_by_address():
    check("ollama.com is Ollama", balances.is_ollama_cloud("https://ollama.com/v1"))
    check("a subdomain is too", balances.is_ollama_cloud("https://api.ollama.com/v1"))
    check("a look-alike is not", not balances.is_ollama_cloud("https://notollama.com/v1"))
    check("featherless is not", not balances.is_ollama_cloud("https://api.featherless.ai/v1"))
    check("an empty address is not", not balances.is_ollama_cloud(""))


def test_every_service_gets_a_line():
    providers = {"openrouter": {"base": "https://openrouter.ai/api/v1"},
                 "glm": {"base": "https://ollama.com/v1"},
                 "featherless": {"base": "https://api.featherless.ai/v1"}}
    keys = {"openrouter": "k1", "glm": "k2"}
    seen = []

    def fetch(url, key):
        seen.append((url, key))
        return OR_ANSWER if "openrouter" in url else OLLAMA_ANSWER

    lines = with_setup(providers, keys, fetch)
    check("one line per service", len(lines) == 3)
    check("openrouter first", lines[0].startswith("OpenRouter: $4.12 left"))
    check("added services alphabetical", lines[1].startswith("featherless:")
          and lines[2].startswith("glm (Ollama cloud):"))
    check("ollama found under any name, with its own key",
          (balances.OLLAMA_BALANCE_URL, "k2") in seen)
    check("a service with no check says so, not silence",
          "doesn't offer a balance check" in lines[1])


def test_failures_read_as_sentences():
    providers = {"openrouter": {"base": "x"}, "ollama-cloud": {"base": "https://ollama.com/v1"}}

    def refused(url, key):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    lines = with_setup(providers, {"openrouter": "k", "ollama-cloud": "k"}, refused)
    check("a refused key is named", all("key was refused" in l for l in lines))

    def offline(url, key):
        raise urllib.error.URLError("no route")

    lines = with_setup(providers, {"openrouter": "k", "ollama-cloud": "k"}, offline)
    check("no connection is named", all("couldn't reach it" in l for l in lines))

    lines = with_setup(providers, {}, offline)
    check("no key: nothing is fetched, and it says why",
          all("no key saved" in l for l in lines))


def test_wiring():
    prefs = (ROOT / "frame" / "prefs_mixin.py").read_text(encoding="utf-8")
    menus = (ROOT / "frame" / "menus_mixin.py").read_text(encoding="utf-8")
    check("the old Preferences balance button is gone",
          "_on_show_provider_balance" not in prefs and "with_balance_button" not in prefs)
    check("Tools has a Balances item", "mnu_balances = tools_menu.Append" in menus)
    check("...and it is bound", "self._on_balances()" in menus and "self.mnu_balances)" in menus)


def main():
    test_descriptions()
    test_recognised_by_address()
    test_every_service_gets_a_line()
    test_failures_read_as_sentences()
    test_wiring()
    print()
    if _fails:
        print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
        return 1
    print("test_balances: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
