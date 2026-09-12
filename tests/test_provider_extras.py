"""A provider added in the dialog gets the shared request shape, not
OpenRouter's extensions — and the rest of the app knows it isn't Ollama.

Two defects, found together.

The request. `_build_openrouter_payload` builds the body for every API
provider, and it always added OpenRouter's own fields: `reasoning` (on EVERY
turn, because a kin's default thinking setting is "off" and "off" was sent
explicitly), top-level `cache_control`, and the `provider` routing block. A
strict provider refuses a field it doesn't recognise, so a provider someone
added could fail on its first message for a reason nothing on screen named.

Everywhere else. About a dozen places asked `model.startswith("openrouter/")`
when they meant "is this hosted?". With a second provider that answer is
wrong, and the app asked the LOCAL Ollama about a model it had never heard
of — which, in the model-swap check, can turn into a false "this model can't
use tools" warning. The structural check at the bottom stops that shape from
coming back: it walks the real syntax tree (so a comment or docstring that
mentions the prefix can never count), and it proves it can see the pattern
on a positive control before a zero is believed.

Same convention as the rest: no pytest, plain check(), exit 1 on failure.

Run:  python tests/test_provider_extras.py
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm_backend as lb  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


_real_api_providers = lb.api_providers


def _with_featherless():
    provs = _real_api_providers()
    provs["featherless"] = {"base": "https://api.featherless.ai/v1",
                            "label": "featherless"}
    return provs


FEATHERLESS = "featherless/Qwen/Qwen3-32B"
OPENROUTER = "openrouter/anthropic/claude-haiku-4.5"
OPTIONS = {"temperature": 0.8, "top_p": 0.9, "top_k": 40, "min_p": 0.0,
           "repeat_penalty": 1.1, "num_predict": 2000, "stop": ["\n["]}
ROUTING = {"order": ["DeepInfra"]}


def _payload(model, effort, show_thinking=True):
    msgs = [{"role": "system", "content": "You are a kin."},
            {"role": "user", "content": "hello"}]
    return lb._build_openrouter_payload(
        model, msgs, OPTIONS, effort, None, True, True,
        show_thinking=show_thinking, cache_ttl="auto",
        provider_routing=ROUTING)


def test_other_provider_gets_no_openrouter_fields():
    p = _payload(FEATHERLESS, "off")
    check("model id sent without our prefix", p["model"] == "Qwen/Qwen3-32B")
    check("default thinking 'off' sends no reasoning field at all",
          "reasoning" not in p and "reasoning_effort" not in p)
    check("no top-level cache_control (Qwen is on the caching list, so "
          "this used to be sent)", "cache_control" not in p)
    check("no OpenRouter provider-routing block", "provider" not in p)
    check("system message left as plain text, not rewritten into cache "
          "blocks", p["messages"][0]["content"] == "You are a kin.")
    check("the kin's own sampling choices still go out",
          p.get("temperature") == 0.8 and p.get("top_k") == 40
          and p.get("max_tokens") == 2000 and p.get("stop") == ["\n["])


def test_other_provider_thinking_uses_the_shared_field():
    for effort in ("low", "medium", "high"):
        p = _payload(FEATHERLESS, effort, show_thinking=False)
        check("thinking '%s' -> reasoning_effort '%s', no OpenRouter "
              "reasoning object" % (effort, effort),
              p.get("reasoning_effort") == effort and "reasoning" not in p)


def test_openrouter_unchanged():
    """Positive control. If this goes red, the gate is catching OpenRouter
    too, and the kin already on OpenRouter would lose caching and its
    explicit thinking-off."""
    p = _payload(OPENROUTER, "off")
    check("openrouter still gets reasoning.enabled=False",
          p.get("reasoning") == {"enabled": False})
    check("openrouter still gets cache_control", "cache_control" in p)
    check("openrouter still gets provider routing", p.get("provider") == ROUTING)
    check("openrouter never gets the plain reasoning_effort field",
          "reasoning_effort" not in p)
    p = _payload(OPENROUTER, "high", show_thinking=False)
    check("openrouter 'high' + hidden thinking still sends exclude",
          p.get("reasoning") == {"effort": "high", "exclude": True})


def test_compat_profile_does_not_ask_ollama_about_hosted_models():
    import compat
    import model_utils
    asked = []
    names = ("_model_context_length", "_model_supports_tools",
             "_model_supports_vision")
    saved = {n: getattr(model_utils, n) for n in names}
    try:
        for n in names:
            setattr(model_utils, n,
                    (lambda nm: (lambda model: asked.append((nm, model))))(n))
        prof = compat._profile_for_model(FEATHERLESS)
        check("hosted model: local Ollama is not asked", asked == [])
        check("hosted model: tool support is unknown, not a false 'no'",
              prof.supports_tools is None)
        check("hosted model: profile names its provider",
              prof.family == "featherless")
        asked.clear()
        compat._profile_for_model("gemma4:31b")
        check("positive control: a local model IS asked about",
              any(model == "gemma4:31b" for _nm, model in asked))
    finally:
        for n, fn in saved.items():
            setattr(model_utils, n, fn)


def test_image_estimate_reads_any_provider_prefix():
    import chat_helpers as ch
    check("a featherless google model is estimated like the same family "
          "on openrouter",
          ch.estimate_image_tokens("featherless/google/gemma-3-27b-it")
          == ch.estimate_image_tokens("openrouter/google/gemma-3-27b-it"))
    check("positive control: a bare local name still gets the Ollama default",
          ch.estimate_image_tokens("gemma4:31b")
          == ch._OLLAMA_IMAGE_TOKENS_DEFAULT)


def test_keep_alive_skips_hosted_models():
    import urllib.request
    real = urllib.request.urlopen
    calls = []
    urllib.request.urlopen = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("no"))
    try:
        check("keep-alive for a hosted model returns without contacting Ollama",
              lb.set_ollama_keep_alive(FEATHERLESS, "30m") is False
              and calls == [])
    finally:
        urllib.request.urlopen = real


# --- structural ratchet ---------------------------------------------------

# Places where the literal prefix is genuinely about OpenRouter itself. Each
# is only ever reached for an openrouter/ model, after the caller already
# asked provider_for_model(). Adding to this list needs that same reason.
_ALLOWED = {
    ("compat.py", "_openrouter_profile"),
    ("compat.py", "_family_from_model_id"),
}


def _prefix_checks(source, filename):
    """(filename, enclosing function) for every `x.startswith("openrouter/")`
    call in real code. Comments and docstrings are not syntax, so they can
    never be counted."""
    found = []
    tree = ast.parse(source, filename=filename)

    def walk(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "openrouter/"):
            found.append((filename, func))
        for child in ast.iter_child_nodes(node):
            walk(child, func)

    walk(tree, None)
    return found


def test_detector_positive_control():
    sample = (
        '"""Mentions model.startswith("openrouter/") in a docstring."""\n'
        '# and model.startswith("openrouter/") in a comment\n'
        'def f(model):\n'
        '    return model.startswith("openrouter/")\n'
    )
    got = _prefix_checks(sample, "sample.py")
    check("detector finds the real call", ("sample.py", "f") in got)
    check("detector ignores the docstring and the comment", len(got) == 1)


def test_no_hand_rolled_hosted_checks():
    offenders = []
    scanned = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        # The root itself is "." — which starts with a dot. The first version
        # of this skipped it as a hidden folder, scanned nothing, and passed
        # on the very code it exists to catch. The scanned-files checks below
        # are there so that can't happen quietly again.
        top = "" if rel_dir == "." else rel_dir.split(os.sep)[0]
        if top in ("tests", "scripts", "build", "dist", "docs", "venv",
                   ".venv", ".git") or top.startswith("."):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith((".py", ".pyw")):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
                hits = _prefix_checks(src, rel)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            scanned.append(rel)
            offenders.extend(h for h in hits if h not in _ALLOWED)
    check("the scan actually read the app (%d files)" % len(scanned),
          len(scanned) > 40)
    for must in ("llm_backend.py", "compat.py", "frame/render_mixin.py",
                 "dialogs/edit_kin.py", "hearthkin.pyw"):
        check("the scan included %s" % must, must in scanned)
    check("no code asks startswith('openrouter/') to mean 'is this hosted' "
          "(use llm_backend.is_hosted_model): %r" % (offenders,),
          offenders == [])


def main():
    lb.api_providers = _with_featherless
    try:
        test_other_provider_gets_no_openrouter_fields()
        test_other_provider_thinking_uses_the_shared_field()
        test_openrouter_unchanged()
        test_compat_profile_does_not_ask_ollama_about_hosted_models()
        test_image_estimate_reads_any_provider_prefix()
        test_keep_alive_skips_hosted_models()
    finally:
        lb.api_providers = _real_api_providers
    test_detector_positive_control()
    test_no_hand_rolled_hosted_checks()
    if _failures:
        print("\nFAILED %d: %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\ntest_provider_extras: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
