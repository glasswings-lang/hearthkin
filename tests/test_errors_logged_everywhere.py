# SPDX-License-Identifier: CC0-1.0
"""A model error is kept on disk and said in plain words on every surface.

Telegram and Discord already logged every failed reply and told the person
in plain words. The desktop said it in plain words but kept no record, and
rooms did neither: they showed the raw error text and wrote nothing down
(audit, 21 Nov 2026). Driven through the real handlers with a stand-in
window.

Run: python tests/test_errors_logged_everywhere.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="errlog-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from frame.chat_stream_mixin import ChatStreamMixin  # noqa: E402
from frame.rooms_mixin import RoomsMixin  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


class Display:
    def __init__(self):
        self.text = ""

    def AppendText(self, t):
        self.text += t


class Fake:
    """Anything the handler pokes that isn't set here is a quiet no-op."""

    def __init__(self):
        self.chat_display = Display()
        self.status = []
        self._stream_id = 1
        self._stream_buf = ""
        self._think_buf = ""
        self._stream_user_text = ""
        self._paint_cursor = 0
        self._streaming = True
        self.agent_cfg = None
        self.current_agent = "Wren"
        self.current_room = "Den"
        self._current_room_speaker = "Wren"
        self._room_round_index = 0

    def _set_status(self, msg, speak=False):
        self.status.append(msg)

    def __getattr__(self, name):
        return Noop()


class Noop:
    def __call__(self, *a, **k):
        return None

    def __getattr__(self, name):
        return Noop()


RAW = "HTTPConnectionPool: Max retries exceeded (connection refused)"


def log_text(name):
    p = Path(kp.LOGS_DIR) / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


f = Fake()
ChatStreamMixin._on_stream_error(f, 1, RAW)
check("desktop: the error is written to its log",
      "Wren" in log_text("desktop_failures.log")
      and "connection refused" in log_text("desktop_failures.log"))
check("desktop: and said in plain words",
      any("Couldn't reach" in s for s in f.status))

import frame.rooms_mixin as rm  # noqa: E402
rm.wx.CallLater = lambda *a, **k: None
f = Fake()
RoomsMixin._on_room_kin_error(f, 1, RAW)
check("room: the error is written to its log, with the room and the kin",
      "Den" in log_text("room_failures.log")
      and "kin=Wren" in log_text("room_failures.log"))
check("room: and said in plain words",
      any("Couldn't reach" in s and "Wren" in s for s in f.status))
check("room: the transcript still shows what happened",
      "error from Wren" in f.chat_display.text)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_errors_logged_everywhere: all checks passed")
