"""Who the summarizer thinks said what. Plain Python; run via tests/run_all.py.

`distill_memory_blocking` flattens a conversation to plain text before handing
it to the summarizer, and that text is the only account of who spoke that the
summarizer ever sees. Two ways it used to lie:

  * Every `role="user"` turn was labelled with the literal word "User", even
    when the turn carried a real name. A group import or a room turn from
    another kin therefore read back as one generic speaker -- and gemma-class
    models pattern-match that into writing "the user" as though it were
    somebody's actual name.

  * Every `role="assistant"` turn was rendered under its stored `speaker`
    whenever that differed from the kin's name. Right for another kin's turn
    in a room. Wrong for a kin whose own imported history carries several of
    ITS OWN past handles -- a renamed account, a name later set aside. Those
    turns are the kin's own voice however they are stamped, and `speaker !=
    kin_name` cannot tell a former self from a stranger. The roster of other
    REGISTERED kin can.

This drives the real function and reads the prompt it built, rather than
restating the formatting rules here where a copy would drift.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


import frame_shared as fs
import llm_backend

# Intercept the model call: we want the prompt that was built, not a reply.
# distill_memory_blocking refuses to start when the ollama package is absent,
# so stand something truthy in its place -- the call never happens either way.
_captured = {}


class _Stop(RuntimeError):
    pass


def _fake_chat(model, messages, **kw):
    _captured["msgs"] = messages
    raise _Stop("prompt captured")


llm_backend.chat = _fake_chat
# The distiller streams (chat_collect) so it can report progress by ear;
# the prompt it builds is the same either way, and this probe only wants
# the prompt.
llm_backend.chat_collect = _fake_chat
if getattr(fs, "ollama", None) is None:
    fs.ollama = object()
# A roster where exactly one other name is a real kin. "wren_old_handle" is
# deliberately NOT on it: that is the kin's own former account name.
fs.list_agents = lambda: ["Tarn", "Opal"]

CONVO = [
    {"role": "user", "content": "a1", "sender_attribution": "Jamie"},
    {"role": "user", "content": "a2", "speaker": "Sam"},
    {"role": "user", "content": "a3"},
    {"role": "assistant", "content": "b1", "speaker": "Tarn"},
    {"role": "assistant", "content": "b2", "speaker": "wren_old_handle"},
    {"role": "assistant", "content": "b3", "speaker": "Opal"},
]

try:
    fs.distill_memory_blocking("Tarn", CONVO, "", "any-model")
except _Stop:
    pass
except Exception as e:  # a different failure means the probe never ran
    check(False, f"the distiller reached the model call (got {type(e).__name__}: {e})")

# Positive control before believing anything below: a probe that never fired
# would report every absence as a pass.
check("msgs" in _captured, "positive control: the prompt was actually captured")
text = ""
if "msgs" in _captured:
    user_turns = [m for m in _captured["msgs"] if m.get("role") == "user"]
    check(bool(user_turns), "positive control: the built prompt has a user turn")
    if user_turns:
        text = user_turns[-1]["content"]
check(all(f"a{i}" in text for i in (1, 2, 3)),
      "positive control: the conversation reached the prompt at all")


def line_for(marker):
    """The rendered line carrying `marker`, or '' if it never appeared."""
    for ln in text.splitlines():
        if marker in ln:
            return ln.strip()
    return ""


# ── A named person keeps their name ───────────────────────────────────
check(line_for("a1").startswith("Jamie:"),
      "a user turn carrying sender_attribution is shown under that name")
check(line_for("a2").startswith("Sam:"),
      "a user turn carrying speaker is shown under that name")
check(not line_for("a1").startswith("User:")
      and not line_for("a2").startswith("User:"),
      "named people are no longer flattened to the word 'User'")

# ── Only a genuinely nameless turn falls back ─────────────────────────
check(line_for("a3").startswith("User:"),
      "a turn with no name at all still falls back to 'User'")

# ── The kin's own voice stays the kin's, whatever it is stamped with ──
check(line_for("b1").startswith("Tarn:"),
      "the kin's own turn is shown as the kin")
check(line_for("b2").startswith("Tarn:"),
      "a former handle of the kin's own is still the kin, not a stranger")
check("wren_old_handle" not in text,
      "an old account name never reaches the summarizer as a speaker")

# ── A real other kin is still attributed to itself ────────────────────
check(line_for("b3").startswith("Opal:"),
      "another REGISTERED kin keeps its own name (rooms still work)")

# The distinction has to be the roster, not string inequality -- that is the
# whole difference between a former self and somebody else.
check("wren_old_handle" != "Tarn" and line_for("b2").startswith("Tarn:"),
      "attribution is decided by the kin roster, not by name inequality")


print()
if _failures:
    print(f"FAILED ({len(_failures)}):")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
