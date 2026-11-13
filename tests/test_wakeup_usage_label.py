# SPDX-License-Identifier: CC0-1.0
"""A wake-up delivered into the open desktop chat is logged as a wake-up.

It used to be logged as "desktop", so the usage log couldn't tell the
person's own chatting from a scheduled wake-up landing in the chat. Since
12 Nov 2026 those calls are labelled "cron-in-chat" / "cron-in-chat-tool",
following the log's existing rule that "cron-*" means a scheduled wake-up.

Run: python tests/test_wakeup_usage_label.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


src = (ROOT / "frame" / "chat_send_mixin.py").read_text(encoding="utf-8")
tree = ast.parse(src)
fn = [n for n in ast.walk(tree)
      if isinstance(n, ast.FunctionDef) and n.name == "_is_wake_up_turn"][0]
import textwrap  # noqa: E402
ns = {}
_lines = src.splitlines(keepends=True)[fn.lineno - 1:fn.end_lineno]
exec(textwrap.dedent("".join(_lines)), ns)
is_wake = ns["_is_wake_up_turn"]

wake = [{"role": "user", "content": "[hearthkin: scheduled wake-up — fired at "
         "08:00 on Thursday]\nTend your notes."}]
chat = [{"role": "user", "content": "hi, how are you?"}]
check("a wake-up turn is recognised", is_wake(wake) is True)
check("an ordinary message isn't", is_wake(chat) is False)
check("an empty history isn't", is_wake([]) is False and is_wake(None) is False)

check("the plain chat call is labelled a wake-up when it is one",
      'surface=("cron-in-chat" if self._is_wake_up_turn(messages)' in src)
check("...and so is the tool call",
      'surface=("cron-in-chat-tool" if cron_turn else "desktop-tool")' in src)
check("the tending-tools check and the label use the same test",
      "cron_turn = self._is_wake_up_turn(messages)" in src)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_wakeup_usage_label: all checks passed")
