# SPDX-License-Identifier: CC0-1.0
"""Guard test: opening a Search across kin result lands where the match is.

Search results carry where the match was found: a kin's soul, its memory, a
one-to-one conversation, or a room. Opening a result called back into the frame
with only "kin" or "room" and a name, so the frame could not tell a soul match
from a conversation match — and it opened that kin's Settings for every one of
them. A conversation match lives in the chat that had just loaded; Settings was
the wrong room, and closing it was one more thing to do for nothing.

The REAL handler is run unbound against just enough frame, the way the other
frame-handler tests do it.

Run: python tests/test_search_open_target.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="searchopen-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


import frame.lifecycle_mixin as LM  # noqa: E402

LM.list_agents = lambda: ["Fenwick"]
LM.list_rooms = lambda: ["Hearth"]


class _Frame:
    _open_search_target = LM.LifecycleMixin._open_search_target

    def __init__(self):
        self.calls = []

    def _load_agent(self, name):
        self.calls.append(("load_kin", name))

    def _load_room(self, name):
        self.calls.append(("load_room", name))

    def _on_edit_kin(self, _event):
        self.calls.append(("settings",))


def opened(*args):
    f = _Frame()
    f._open_search_target(*args)
    return f.calls


check("a soul match loads the kin and opens Settings",
      opened("kin", "Fenwick", "soul") == [("load_kin", "Fenwick"), ("settings",)])
check("a memory match loads the kin and opens Settings",
      opened("kin", "Fenwick", "memory") == [("load_kin", "Fenwick"), ("settings",)])
check("a conversation match loads the kin and does NOT open Settings",
      opened("kin", "Fenwick", "convo") == [("load_kin", "Fenwick")])
check("a room match loads the room",
      opened("room", "Hearth", "room") == [("load_room", "Hearth")])
check("an older caller that passes no source still gets the old behaviour",
      opened("kin", "Fenwick") == [("load_kin", "Fenwick"), ("settings",)])
check("a kin that no longer exists opens nothing",
      opened("kin", "Nobody", "soul") == [])

# The dialog side: it must pass the source it already has. Checked on the
# syntax tree so a comment mentioning it can't satisfy the check.
import ast  # noqa: E402

tree = ast.parse((ROOT / "dialogs" / "search.py").read_text(encoding="utf-8"))
passes_source = False
for node in ast.walk(tree):
    if (isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "on_open_target"
            and len(node.args) >= 3):
        passes_source = True
check("the Search window passes where the match was", passes_source)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_search_open_target: all checks passed")
