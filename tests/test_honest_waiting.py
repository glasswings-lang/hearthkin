# SPDX-License-Identifier: CC0-1.0
"""Two ways the app claimed to know something it didn't, both fixed here.

Both cost a real afternoon on 2026-08-07, and both did it the same way: they
pointed at the wrong thing confidently, so the time went into checking
something that was already fine.

  1. A tool-calling turn announced "Typing" the instant it dispatched the
     request — before a single byte came back. A request that never started at
     the far end was indistinguishable from a reply arriving. Observed: forty
     minutes of "Typing…", chime still sounding, for a model that was queued
     behind another one and never loaded.

  2. A READ timeout was reported as "couldn't reach the machine". The machine
     was up, answering, and had 19 GB free the whole time; the model simply
     hadn't started. That sends someone to check a network that was never the
     problem.

No widgets are built here — the status change is checked at the source, which
is where the wrong word actually lived.
"""

import ast
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="honest-"))

from chat_helpers import humanize_error  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# ── 1. A read timeout is not an unreachable machine ──────────────────────────

for raw in ("HTTPConnectionPool(host='100.x.x.x', port=11434): Read timed out. "
            "(read timeout=2400)",
            "ReadTimeout",
            "requests.exceptions.ReadTimeout: read timeout"):
    msg = humanize_error(raw, kin="Bracken", host="http://mac:11434")
    ok = ("didn't start replying in time" in msg
          and "Is it on and reachable?" not in msg)
    check(f"1 read timeout is not reported as unreachable ({raw[:26]!r})", ok)

msg = humanize_error("Read timed out.", kin="Bracken")
check("1 it says plainly that the machine itself is fine",
      "machine itself is fine" in msg)
check("1 ...and names what actually causes it",
      "holding it" in msg or "loading" in msg)

# Positive control: a genuinely unreachable machine must STILL say so, or this
# fix has just moved the misdirection somewhere else.
for raw in ("[Errno 111] Connection refused",
            "Failed to establish a new connection",
            "getaddrinfo failed",
            "No route to host — network is unreachable",
            "connection timed out"):
    m = humanize_error(raw, kin="Bracken")
    check(f"1-control still unreachable for {raw[:24]!r}",
          "Is it on and reachable?" in m)

# And the other branches didn't move.
check("1-control a missing model still reads as a missing model",
      "isn't loaded" in humanize_error("model 'x' not found", kin="Vesper"))
check("1-control rate limits unchanged",
      "rate-limiting" in humanize_error("429 too many requests", kin="Vesper"))


# ── 2. "Typing" is only said when words have actually arrived ────────────────

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "frame", "chat_send_mixin.py")
src = open(SRC, encoding="utf-8").read()
tree = ast.parse(src)


def phases_spoken_in(func_name):
    """Every literal passed to _speak_status_phase inside one function."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "_speak_status_phase"
                        and sub.args
                        and isinstance(sub.args[0], ast.Constant)):
                    out.append(sub.args[0].value)
    return out

# The dispatch path for a tool turn must not claim words are arriving.
dispatch = phases_spoken_in("_run_tool_loop_inline")
check("2 the tool-turn dispatch announces something", bool(dispatch))
check("2 ...but never 'Typing' — nothing has arrived yet",
      "Typing" not in dispatch)
check("2 ...it says it is waiting", any("Wait" in p for p in dispatch))

# The ORDINARY case — a plain chat reply, no tools — is most of the traffic.
# It never claimed "Typing", but it left "Sending…" up for the whole wait,
# which describes the one part that had already finished. Both paths must now
# use the same words, or the Activity field means different things depending
# on whether the kin happens to have tools.
plain = phases_spoken_in("_run_streaming_inline")
check("2 a plain chat reply also announces waiting", any("Wait" in p for p in plain))
check("2 ...and never claims Typing at dispatch either", "Typing" not in plain)
check("2 both paths use the SAME phase word", set(plain) == set(dispatch))

# The chunk handler — where words really do arrive — must still say Typing.
chunk = phases_spoken_in("_on_stream_chunk")
check("2-control the chunk handler still announces Typing", "Typing" in chunk)
check("2-control a reasoning model can still announce Thinking",
      "Thinking" in phases_spoken_in("_on_stream_thinking_chunk"))

# Whole-file control: "Typing" survives somewhere, so this test can tell
# "moved to the right place" from "deleted entirely".
check("2-control 'Typing' is still said somewhere in the file",
      '"Typing"' in src or "'Typing'" in src)

print()
if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("all honest-waiting checks passed")
