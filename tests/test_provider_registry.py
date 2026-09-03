"""Regression tests for the API-provider registry in llm_backend.

Adding a provider is meant to be data, not code: a name, a base URL and a
key. These tests pin the parts of that which are easy to get subtly wrong.

The one that matters most is the `hf.co/` case. A provider prefix looks
exactly like the first segment of a model name, and this person's local
Ollama models are routinely called

    hf.co/TheDrummer/Cydonia-24B-v4.3-GGUF:Q4_K_M

so a "split on the first slash" rule would try to route that to a provider
called "hf.co" and the model would stop working with no useful error. The
rule must be "match the registry", and this file is here to keep it that way.

Same convention as the rest: no pytest, plain check(), exit 1 on failure.

Run:  python tests/test_provider_registry.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_backend as lb  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


def test_registry_shape():
    provs = lb.api_providers()
    check("openrouter is registered", "openrouter" in provs)
    check("its base URL is a full https endpoint",
          provs["openrouter"]["base"].startswith("https://"))
    check("the base has no trailing slash to double up on",
          not provs["openrouter"]["base"].endswith("/"))
    check("unknown provider returns None rather than raising",
          lb.api_provider_spec("no-such-provider") is None)
    provs["openrouter"]["base"] = "https://evil.example"
    check("api_providers() hands back a copy, so callers can't corrupt it",
          lb.api_providers()["openrouter"]["base"] != "https://evil.example")


def test_splitting():
    check("a registered prefix splits off",
          lb.split_provider_model("openrouter/anthropic/claude-sonnet-4")
          == ("openrouter", "anthropic/claude-sonnet-4"))
    check("case in the prefix doesn't matter",
          lb.split_provider_model("OpenRouter/anthropic/x")[0] == "openrouter")
    check("a bare ollama name is left alone",
          lb.split_provider_model("gemma4:31b") == (None, "gemma4:31b"))


def test_hf_co_is_not_a_provider():
    """The landmine. hf.co looks like a prefix and is not one."""
    m = "hf.co/TheDrummer/Cydonia-24B-v4.3-GGUF:Q4_K_M"
    check("hf.co model is NOT treated as a hosted provider",
          lb.split_provider_model(m) == (None, m))
    check("...so it routes to Ollama", not lb._is_openrouter_model(m))
    check("...and its name is passed through untouched",
          lb._openrouter_model_id(m) == m)


def test_unregistered_prefix_is_not_hosted():
    """Until a provider is added, its prefix means nothing. This is what
    stops a typo'd or half-configured provider from being silently sent
    somewhere - it falls through to Ollama, which says it can't find the
    model, rather than being POSTed to a URL that doesn't exist."""
    check("an unregistered prefix is not hosted",
          not lb._is_openrouter_model("featherless/some/model"))


def test_degenerate_input():
    for bad in (None, "", "/", "openrouter/", 17, []):
        check("no crash on %r" % (bad,),
              lb.split_provider_model(bad)[0] in (None, "openrouter"))
    check("a prefix with nothing after it is not hosted",
          not lb._is_openrouter_model("openrouter/"))


def main():
    test_registry_shape()
    test_splitting()
    test_hf_co_is_not_a_provider()
    test_unregistered_prefix_is_not_hosted()
    test_degenerate_input()
    if _failures:
        print("\nFAILED %d: %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\ntest_provider_registry: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
