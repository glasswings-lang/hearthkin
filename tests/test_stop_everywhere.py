# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for stopping a kin from the desktop, wherever it's
working (no network, no windows).

Two holes, both found the night a kin ran sixty commands in one reply:

  - Stop on the desktop stopped SHOWING the reply but never told the tool
    loop, which carried on running the kin's calls unseen. The desktop and
    room tool loops must pass should_stop.
  - A reply running on Telegram or Discord had no stop the operator could
    reach from the desktop. Chat -> "Stop every reply, on every surface"
    (Ctrl+Shift+X) now reaches them, and says aloud what it stopped.
"""

import os
import re
import sys
import tempfile
import threading

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk-stopall-"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def loop_call(text, surface):
    """The run_tool_loop(...) call that names this surface."""
    for m in re.finditer(r"run_tool_loop\(", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        call = text[m.start():i]
        # The label may be chosen in place (since 12 Nov 2026 a wake-up in
        # the chat is "cron-in-chat-tool"), so match the quoted name.
        if f'"{surface}"' in call and "surface=" in call:
            return call
    return ""


def main():
    # The desktop and room loops are told about Stop.
    for rel, surface in (("frame/chat_send_mixin.py", "desktop-tool"),
                         ("frame/rooms_mixin.py", "room-tool")):
        call = loop_call(src(rel), surface)
        check(f"{surface}: the tool loop call was found", bool(call))
        check(f"{surface}: it passes should_stop", "should_stop=" in call)
        check(f"{surface}: ...tied to this turn's generation",
              "my_gen != self._stream_id" in call)

    # The menu item exists, has its shortcut, and is bound.
    menus = src("frame/menus_mixin.py")
    check("Chat menu has 'Stop every reply, on every surface'",
          "Stop every reply, on every surface\\tCtrl+Shift+X" in menus)
    check("...and it is bound to its handler",
          "self._on_stop_everywhere, self.mnu_stop_everywhere" in menus)
    shortcuts = re.findall(r"\\t((?:Ctrl|Alt|Shift)[^\"]*)\"", menus)
    check("Ctrl+Shift+X is used by nothing else in the menus",
          shortcuts.count("Ctrl+Shift+X") == 1)

    # The bots can be stopped from outside, whoever the reply is for.
    import telegram_bot as T
    import discord_bot as D

    class TG(T.TelegramBot):
        def __init__(self):
            self._turn_lock = threading.RLock()
            self._active_turn = None
            self._cancelled_turns = set()
            self.agent_name = "Wren"

        def get_config(self):
            return {"user_labels": {"7": "Juniper"}}

    tg = TG()
    check("Telegram: nothing running -> nothing to stop", tg.stop_any_turn() is None)
    tg._begin_turn(7, 7)
    said = tg.stop_any_turn()
    check("Telegram: a running reply is stopped", tg._turn_cancelled(7) is True)
    check("Telegram: and it says whose", said == "Wren's reply to Juniper on Telegram")

    class DC(D.DiscordBot):
        def __init__(self):
            self._turn_lock = threading.RLock()
            self._active_turns = {}
            self.agent_name = "Wren"

    dc = DC()
    check("Discord: nothing running -> nothing to stop", dc.stop_any_turn() is None)
    dc._active_turns[11] = {"who": "Juniper", "stop": False}
    dc._active_turns[12] = {"who": "Rook", "stop": False}
    said = dc.stop_any_turn()
    check("Discord: every running reply is stopped",
          all(e["stop"] for e in dc._active_turns.values()))
    check("Discord: and it says so in words", bool(said) and "Wren" in said)

    # The desktop handler, with a stand-in frame: stops what's running and
    # says it aloud.
    from frame import lifecycle_mixin as L
    spoken, status = [], []
    L.nvda_speak = spoken.append

    class Frame:
        _streaming = False
        _room_active = False

        def _set_status(self, m):
            status.append(m)

        def _on_stop(self, e):
            pass
    f = Frame()
    tg2 = TG()
    tg2._begin_turn(7, 7)
    f.bots = {"Wren": tg2}
    f.discord_bots = {}
    L.LifecycleMixin._on_stop_everywhere(f)
    check("the command stops a Telegram reply from the desktop", tg2._turn_cancelled(7))
    check("...and says what it stopped, aloud",
          spoken and spoken[-1] == "Stopping Wren's reply to Juniper on Telegram.")
    check("...and on the Activity line", status and status[-1] == spoken[-1])
    f.bots = {}
    L.LifecycleMixin._on_stop_everywhere(f)
    check("with nothing running it says so",
          spoken[-1] == "Nothing was running, so there was nothing to stop.")

    # The ceiling for other people, and the spoken warning.
    tgsrc = src("telegram_bot.py")
    check("Telegram passes a call ceiling to the tool loop",
          "max_tool_calls=_remote_cap or None" in tgsrc)
    check("...and sharing the desktop conversation doesn't lift it",
          "is_operator=" not in tgsrc
          and "is_operator=" not in src("discord_bot.py"))
    check("Discord always passes the ceiling",
          "max_tool_calls=_remote_cap or None" in src("discord_bot.py"))

    heard = []
    tg3 = TG()
    tg3.on_long_reply = lambda *a: heard.append(a)
    tg3._announce_long_reply(7, 7, 10)
    check("Telegram tells the desktop who and how many",
          heard == [("Wren", "Telegram", "Juniper", 10)])
    heard.clear()
    dc2 = DC()
    dc2.on_long_reply = lambda *a: heard.append(a)
    dc2._active_turns[11] = {"who": "Rook", "stop": False}
    dc2._announce_long_reply(11, 10)
    check("Discord tells the desktop who and how many",
          heard == [("Wren", "Discord", "Rook", 10)])

    from frame import bot_integration_mixin as BI
    spoken2 = []
    BI.nvda_speak = spoken2.append
    BI.BotIntegrationMixin._notify_long_remote_reply(f, "Wren", "Discord", "Rook", 10)
    check("the desktop says it aloud, with how to stop it",
          spoken2 and "Wren has run 10 tool calls in one reply to Rook on "
          "Discord" in spoken2[-1] and "Control Shift X" in spoken2[-1])

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
