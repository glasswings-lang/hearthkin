# SPDX-License-Identifier: CC0-1.0
"""The desktop says "cleared", "undid", "compacted" or "new conversation"
only when it was saved (24 Nov 2026, message audit).

All four ran the save, which can refuse (a copy that can't be kept refuses
the save on purpose), then put a success line over the failure warning, with
the conversation emptied on screen and untouched on disk. Now, on a failed
save, the conversation is put back as it was and the person is told plainly.
And "New conversation" named "the kin's conversations folder", which is
wrong: the copy goes in the conversations folder of the .hearthkin folder.
Pinned:

  * a failed save puts the conversation (and its open file) back;
  * the status says nothing changed and where the details are;
  * a good save changes nothing here;
  * all four actions use it (goes looking at the source);
  * "New conversation" no longer names the kin's folder.

Run: python tests/test_desktop_says_only_what_saved.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="desksaved-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


try:
    from frame.chat_stream_mixin import ChatStreamMixin as M
except Exception:
    import frame.chat_stream_mixin as _m
    M = next(v for v in vars(_m).values()
             if isinstance(v, type) and hasattr(v, "_put_back_if_unsaved"))


class Fake:
    _put_back_if_unsaved = M._put_back_if_unsaved

    def __init__(self, failed):
        self._last_save_failed = failed
        self.conversation = []
        self.current_convo_file = None
        self.status = ""

    def _render_conversation(self):
        pass

    def _update_token_display(self):
        pass

    def _set_status(self, s):
        self.status = s


before = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
f = Fake(True)
put = f._put_back_if_unsaved(before, "snap.json", "clear the conversation")
check("a failed save puts the conversation back", put and f.conversation == before)
check("...and the open file", f.current_convo_file == "snap.json")
check("...and says nothing changed, and where the details are",
      "nothing changed" in f.status and "save_failures.log" in f.status
      and "clear the conversation" in f.status)
g = Fake(False)
check("a good save changes nothing", g._put_back_if_unsaved(before, None, "x") is False
      and g.conversation == [])

src = ((ROOT / "frame" / "chat_stream_mixin.py").read_text(encoding="utf-8")
       + (ROOT / "frame" / "file_menu_mixin.py").read_text(encoding="utf-8"))
for what in ("clear the conversation", "undo", "compact", "start a new conversation"):
    check(f"'{what}' checks its save", f'"{what}"' in src)
check("New conversation no longer names the kin's own folder",
      "kin's conversations folder" not in src
      and "conversations folder of your .hearthkin folder" in src)

print()
if _fails:
    print(f"test_desktop_says_only_what_saved: {len(_fails)} FAILED")
    for x in _fails:
        print("  - " + x)
    sys.exit(1)
print("test_desktop_says_only_what_saved: all checks passed")
