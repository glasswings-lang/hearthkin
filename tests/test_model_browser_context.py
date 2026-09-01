"""The model browser reports each local model's context ceiling.

Until now that number was reachable one kin at a time, from the Settings
dialog, which is the wrong place to compare models against each other —
choosing a model is exactly when you want to see all the ceilings at
once. OpenRouter models had shown theirs in the list for a long time;
local Ollama models showed family, parameter count and disk size, and
not the one number that decides whether a conversation fits.

What's pinned here is the part that isn't obvious:

  - the cache is keyed by (host, model), because the browser has its own
    Machine dropdown and can be listing a different daemon than the one
    the app is configured against. A model-only cache would confidently
    report the wrong box's answer with nothing to show for it.
  - a model that doesn't publish a ceiling reads as unknown, never as a
    number, and never gets dropped from the list.
  - the lookup happens on the loader thread, before the list is drawn.
    A list that rewrites itself underneath someone arrowing through it
    with a screen reader is worse than a list that took a moment longer.

No widgets are built here — only the module-level helpers, which is
where all the logic deliberately lives.

    python tests/test_model_browser_context.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend  # noqa: E402
import model_browser as mb  # noqa: E402

H = "http://box-a:11434"
MAC = "http://box-mac:11434"
PC = "http://box-pc:11434"

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


def test_label_formatting():
    check("a million-plus ceiling reads in M", mb.context_length_label(2_000_000) == "2M ctx")
    check("a normal ceiling reads in K", mb.context_length_label(131072) == "131K ctx")
    check("a tiny ceiling reads plainly", mb.context_length_label(900) == "900 ctx")
    # The three ways "we don't know" arrives. None of them may render as
    # a number — a made-up ceiling is worse than an admitted gap, because
    # the number is used to choose num_ctx.
    for missing in (None, 0, "?"):
        check(f"unknown ceiling ({missing!r}) says so rather than inventing one",
              mb.context_length_label(missing) == "ctx unknown")


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_show(answers):
    """Stand in for Ollama's /api/show at the HTTP layer.

    Patched at `urllib.request.urlopen` rather than at
    `llm_backend._ollama_show_raw`, because the browser deliberately
    makes its own request — it needs the HTTP STATUS, and the shared
    helper collapses every failure to None. A stub aimed at the shared
    helper silently stops intercepting anything, which is how this file
    first reported passes for code it was no longer exercising.

    `answers` maps (host, model) to a context length, None for a model
    that answers but publishes no ceiling, or "404" for a tag the daemon
    lists but cannot load."""
    calls = []

    def fake_urlopen(req, timeout=None):
        import json as _json
        import urllib.error
        host = req.full_url[:-len("/api/show")]
        name = _json.loads(req.data.decode("utf-8"))["name"]
        calls.append((host, name))
        ctx = answers.get((host, name), "absent")
        if ctx == "404":
            raise urllib.error.HTTPError(req.full_url, 404, "not found", None, None)
        if ctx == "absent" or ctx is None:
            return _Resp(_json.dumps({"model_info": {}}).encode())
        # The arch prefix genuinely varies by family; the real code scans
        # for the suffix rather than naming architectures, so the stub
        # uses a prefix nothing hard-codes.
        return _Resp(_json.dumps(
            {"model_info": {"somearch.context_length": ctx}}).encode())
    return fake_urlopen, calls


def _with_stub(answers):
    """Context manager: install the stub, restore afterwards."""
    import contextlib
    import urllib.request

    @contextlib.contextmanager
    def _cm():
        stub, calls = _stub_show(answers)
        real = urllib.request.urlopen
        urllib.request.urlopen = stub
        try:
            yield calls
        finally:
            urllib.request.urlopen = real
    return _cm()


def test_annotates_from_the_daemon():
    mb.clear_ollama_context_cache()
    with _with_stub({(H, "big"): 131072, (H, "small"): 4096}) as calls:
        models = [{"id": "big", "_ollama_local": True},
                  {"id": "small", "_ollama_local": True}]
        out = mb.annotate_ollama_context_lengths(models, host=H)
    check("each model gets its ceiling", [m.get("context_length") for m in out] == [131072, 4096])
    check("stored under the same key OpenRouter models already use",
          "context_length" in out[0])
    check("one lookup per model", len(calls) == 2)
    check("...against the host it was told to ask", calls and all(h == H for h, _ in calls))


def test_a_model_with_no_ceiling_is_kept():
    mb.clear_ollama_context_cache()
    with _with_stub({(H, "quiet"): None}):
        out = mb.annotate_ollama_context_lengths(
            [{"id": "quiet", "_ollama_local": True}], host=H)
    check("a model that publishes no ceiling stays in the list", len(out) == 1)
    check("...with no invented number", not out[0].get("context_length"))
    check("...and reads as unknown",
          mb.context_length_label(out[0].get("context_length")) == "ctx unknown")


def test_a_dead_daemon_does_not_lose_the_list():
    mb.clear_ollama_context_cache()

    import urllib.request

    def boom(req, timeout=None):
        raise OSError("no route to host")
    real = urllib.request.urlopen
    urllib.request.urlopen = boom
    try:
        out = mb.annotate_ollama_context_lengths(
            [{"id": "a"}, {"id": "b"}], host=H)
    finally:
        urllib.request.urlopen = real
    check("an unreachable daemon still returns every model", len(out) == 2)
    check("...just without ceilings",
          not any(m.get("context_length") for m in out))


def test_cache_is_per_host():
    # The browser can be listing a machine that isn't the one the app
    # chats with. The SAME model name on two boxes can be two different
    # pulls with two different ceilings, and answering for the wrong box
    # would be invisible.
    mb.clear_ollama_context_cache()
    with _with_stub({(MAC, "m"): 262144, (PC, "m"): 8192}) as calls:
        a = mb.annotate_ollama_context_lengths([{"id": "m"}], host=MAC)
        b = mb.annotate_ollama_context_lengths([{"id": "m"}], host=PC)
    check("the same model on a second host is asked again, not assumed",
          len(calls) == 2)
    check("...and each host gets its own answer",
          (a[0]["context_length"], b[0]["context_length"]) == (262144, 8192))


def test_cache_saves_the_second_call_and_refresh_clears_it():
    mb.clear_ollama_context_cache()
    with _with_stub({(H, "m"): 40960}) as calls:
        mb.annotate_ollama_context_lengths([{"id": "m"}], host=H)
        first = len(calls)
        mb.annotate_ollama_context_lengths([{"id": "m"}], host=H)
        check("control: the first pass really did query the daemon", first == 1)
        check("a second pass is served from cache", len(calls) == first)
        # Refresh Models has to reach this cache too, or a re-pulled model
        # with a changed ceiling reports the old one forever.
        mb.clear_ollama_context_cache()
        mb.annotate_ollama_context_lengths([{"id": "m"}], host=H)
        check("clearing the cache makes it ask again", len(calls) == first + 1)


def test_an_already_known_ceiling_is_not_refetched():
    # OpenRouter models arrive with context_length already set. Asking a
    # local daemon about them would be a wasted round trip per model, per
    # open.
    mb.clear_ollama_context_cache()
    with _with_stub({}) as calls:
        out = mb.annotate_ollama_context_lengths(
            [{"id": "known", "context_length": 128000}], host=H)
    check("a model that already knows its ceiling is left alone", not calls)
    check("...and keeps it", out[0]["context_length"] == 128000)


def test_a_listed_but_unloadable_model_is_flagged():
    # A tag can survive an interrupted pull: /api/tags reports it with a
    # digest and a size on disk, and /api/show 404s. Picking one gives a
    # kin that fails on every message, and the only symptom anywhere was
    # that it published no capabilities and no ceiling. Two of twelve
    # models on a real machine were in this state on 2026-08-06.
    mb.clear_ollama_context_cache()
    with _with_stub({(H, "ghost"): "404", (H, "real"): 8192}):
        out = mb.annotate_ollama_context_lengths(
            [{"id": "ghost"}, {"id": "real"}], host=H)
        _ctx, status_missing = mb.probe_ollama_model("ghost", host=H)

    check("a 404 from the daemon is reported as missing, not as unknown",
          status_missing == "missing")
    check("the unloadable model is flagged", out[0].get("_ollama_missing") is True)
    check("...and is NOT hidden — you need to know the tag is there",
          len(out) == 2)
    check("a healthy model beside it is untouched",
          not out[1].get("_ollama_missing") and out[1]["context_length"] == 8192)


def test_a_dead_daemon_is_not_reported_as_broken_models():
    # The failure mode to avoid: the Mac goes to sleep and every model on
    # it reads "NOT USABLE, remove it". That would send you deleting a
    # working library.
    mb.clear_ollama_context_cache()
    import urllib.request
    real = urllib.request.urlopen

    def dead(req, timeout=None):
        raise OSError("no route to host")

    urllib.request.urlopen = dead
    try:
        _ctx, status = mb.probe_ollama_model("m", host=H)
        out = mb.annotate_ollama_context_lengths([{"id": "m"}], host=H)
    finally:
        urllib.request.urlopen = real
    check("an unreachable daemon is 'unreachable', never 'missing'",
          status == "unreachable")
    check("...so no model gets marked for deletion",
          not out[0].get("_ollama_missing"))


def test_the_refresh_button_reaches_this_cache():
    # Wiring check, by reading the loader: Refresh Models must clear the
    # ceiling cache the same way it already clears the model list and the
    # tool-capability cache. Easy to add a cache and forget this.
    import inspect
    src = inspect.getsource(mb.ModelBrowserDialog._load_models_async)
    check("the loader clears the ceiling cache on a forced refresh",
          "clear_ollama_context_cache()" in src)
    check("...and annotates the list before it is drawn",
          "annotate_ollama_context_lengths" in src)


def main():
    test_label_formatting()
    test_annotates_from_the_daemon()
    test_a_model_with_no_ceiling_is_kept()
    test_a_dead_daemon_does_not_lose_the_list()
    test_cache_is_per_host()
    test_cache_saves_the_second_call_and_refresh_clears_it()
    test_an_already_known_ceiling_is_not_refetched()
    test_a_listed_but_unloadable_model_is_flagged()
    test_a_dead_daemon_is_not_reported_as_broken_models()
    test_the_refresh_button_reaches_this_cache()

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        return 1
    print("test_model_browser_context: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
