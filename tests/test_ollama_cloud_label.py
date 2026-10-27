"""An Ollama CLOUD model is never described as local or free.

Ollama's cloud models (`glm-5.2:cloud`, the older `gpt-oss:120b-cloud`)
appear in the same `ollama list` as models on the machine. The model
browser was written before they existed, so it described every Ollama
entry as "Local model — no pricing, no network. Content stays on this
machine", with "0.0 GB on disk" and a price of zero. For a cloud model
every part of that is false: the conversation leaves the computer, and
each reply spends the account's credit.

Pinned here:

  - the name rule: `:cloud` and `-cloud` tags are cloud, an ordinary
    tag that merely contains the word elsewhere is not;
  - the list entry carries the flag and no zero price;
  - the one-line label says CLOUD, because someone arrowing through the
    list with a screen reader hears that line and may never open the
    detail card;
  - the detail card says the conversation leaves the computer, and does
    not claim it stays on it;
  - a local model's text is unchanged (positive control).

No widgets are built; the methods are called on a stand-in.

    python tests/test_ollama_cloud_label.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_backend  # noqa: E402
import model_browser as mb  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


class _FakeOllama:
    def list(self):
        return {"models": [
            {"model": "glm-5.2:cloud", "size": 384,
             "details": {"family": "glm5", "parameter_size": "",
                         "quantization_level": ""}},
            {"model": "gpt-oss:120b-cloud", "size": 384, "details": {}},
            {"model": "mistral:latest", "size": 4_400_000_000,
             "details": {"family": "llama", "parameter_size": "7.2B",
                         "quantization_level": "Q4_0"}},
        ]}


class _Stand:
    """Just enough of ModelBrowserDialog for the two render methods."""
    def __init__(self):
        self._ollama_caps_cache = {}

    def _spawn_ollama_caps_fetch(self, mid):
        pass


def test_name_rule():
    for n in ("glm-5.2:cloud", "gpt-oss:120b-cloud", "GLM-5.3:CLOUD",
              "hf.co/someone/model:cloud"):
        check(f"{n} is cloud", llm_backend.is_ollama_cloud_model(n))
    for n in ("mistral:latest", "cloudy-model:7b", "glm-5.2", "", None,
              "cloud", "hf.co/cloud-org/model:q4"):
        check(f"{n!r} is not cloud", not llm_backend.is_ollama_cloud_model(n))


def _listed():
    saved = llm_backend.ollama
    llm_backend.ollama = _FakeOllama()
    try:
        return {m["id"]: m for m in llm_backend.list_ollama_models()}
    finally:
        llm_backend.ollama = saved


def test_list_entries():
    by = _listed()
    glm = by["glm-5.2:cloud"]
    check("cloud entry is flagged", glm.get("_ollama_cloud") is True)
    check("cloud entry carries no zero price", glm.get("pricing") == {})
    check("cloud description says it leaves the machine",
          "leaves the machine" in glm["description"])
    check("cloud description never says local",
          "Local" not in glm["description"])
    check("older -cloud tag is flagged too", by["gpt-oss:120b-cloud"]["_ollama_cloud"])
    loc = by["mistral:latest"]
    check("local entry is not flagged", loc.get("_ollama_cloud") is False)
    check("local entry keeps its old description",
          loc["description"].startswith("Local Ollama model."))
    check("local entry keeps its zero price",
          loc["pricing"] == {"prompt": "0", "completion": "0"})


def test_labels_and_card():
    by = _listed()
    stand = _Stand()
    fmt = mb.ModelBrowserDialog._format_entry
    card = mb.ModelBrowserDialog._render_ollama_detail

    line = fmt(stand, by["glm-5.2:cloud"])
    check("list line says CLOUD", "CLOUD" in line)
    check("list line names the credit", "credit" in line)
    check("list line has no disk size", "GB on disk" not in line)

    text = card(stand, by["glm-5.2:cloud"])
    check("card names Ollama cloud", "Ollama cloud" in text)
    check("card says the conversation leaves", "leaves this computer" in text)
    check("card does not say it stays", "stays on this machine" not in text)
    check("card does not claim no pricing", "no pricing" not in text)

    # Positive control: the local wording is what the cloud one replaced.
    local_line = fmt(stand, by["mistral:latest"])
    local_text = card(stand, by["mistral:latest"])
    check("local line keeps disk size", "GB on disk" in local_line)
    check("local line does not say CLOUD", "CLOUD" not in local_line)
    check("local card still says it stays local", "stays on this machine" in local_text)

    # The card names the model's ceiling, and admits when it isn't known.
    known = dict(by["mistral:latest"], context_length=32768)
    check("card states a known ceiling", "Max context:  32,768 tokens" in card(stand, known))
    check("card admits an unknown ceiling", "Max context:  unknown" in local_text)
    check("cloud card states it too", "Max context:" in text)


def test_added_provider_price_is_unknown_not_free():
    """The same cloud models reached with an API key arrive as an added
    provider, through a different path, and a missing price read as free."""
    stand = _Stand()
    stand._overrides = {}
    fmt = mb.ModelBrowserDialog._format_entry
    card = mb.ModelBrowserDialog._render_detail

    added = {"id": "ollama-cloud/glm-5.2"}
    line = fmt(stand, added)
    check("added provider: list says price unknown", "price unknown" in line)
    check("added provider: list never says free", "free" not in line)
    text = card(stand, added)
    check("added provider: card says pricing unknown", "Pricing:     unknown" in text)

    # Positive controls: OpenRouter's real prices, including a real zero.
    paid = {"id": "openrouter/x/paid", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    free = {"id": "openrouter/x/free", "pricing": {"prompt": "0", "completion": "0"}}
    check("openrouter priced model keeps its price", "in $1.00/M, out $2.00/M" in fmt(stand, paid))
    check("openrouter free model still says free", "in free, out free" in fmt(stand, free))


def main():
    test_name_rule()
    test_list_entries()
    test_labels_and_card()
    test_added_provider_price_is_unknown_not_free()
    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        return 1
    print("test_ollama_cloud_label: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
