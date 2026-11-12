# SPDX-License-Identifier: CC0-1.0
"""Per-person approval for shell commands asked for on Telegram and Discord.

Before 12 Nov 2026 one kin-wide box ("Run remote (Telegram/Discord) exec
without asking") decided for everyone: on, and every Full person's commands
ran unasked; off, and on Telegram the person who asked approved their own
command in the chat. Now each person has a setting: the kin's setting, ask
first, or run without asking. One rule serves both surfaces, and "ask first"
always asks the operator, never the person who wanted the command.

Driven through the real wrappers each surface uses. The command itself is a
stand-in that records whether it ran; nothing touches a shell.

Run: python tests/test_per_person_exec_approval.py
"""

import ast
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="execperson-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from tools._buckets import resolve_exec_approval  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


# --- the shared rule ------------------------------------------------------
ON = {"remote_unattended_exec": True, "tool_trust": "full"}
OFF = {"remote_unattended_exec": False, "tool_trust": "full"}
check("no setting for the person: the kin box on means run",
      resolve_exec_approval({}, "7", ON) == "auto")
check("no setting for the person: the kin box off means ask",
      resolve_exec_approval({}, "7", OFF) == "ask")
check("the kin box on but the kin untrusted still asks (as before)",
      resolve_exec_approval({}, "7", {"remote_unattended_exec": True,
                                      "tool_trust": "untrusted"}) == "ask")
check("the person's 'ask' wins over the kin box",
      resolve_exec_approval({"7": "ask"}, "7", ON) == "ask")
check("the person's 'run' wins over the kin box",
      resolve_exec_approval({"7": "auto"}, "7", OFF) == "auto")
check("a setting for someone else doesn't leak",
      resolve_exec_approval({"8": "ask"}, "7", ON) == "auto")
check("junk in the settings falls back to the kin box",
      resolve_exec_approval({"7": "sometimes"}, "7", OFF) == "ask")


# --- Telegram, through its real wrapper ------------------------------------
from telegram_bot import TelegramBot, _DENY_RESULTS  # noqa: E402

KIN = "Wren"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
OPERATOR, FRIEND, SHARER = "100", "200", "300"


def telegram_run(kin_box, per_user, who, desktop_answer="allow",
                 chat_answer="allow", desktop=True, command="dir"):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["remote_unattended_exec"] = kin_box
    cfg["tool_trust"] = "full"
    save_agent_config(KIN, cfg)
    bot = TelegramBot.__new__(TelegramBot)
    bot.agent_name = KIN
    tg = {"user_is_operator": {OPERATOR: True},
          "user_share_desktop": {OPERATOR: True, SHARER: True},
          "user_exec_approval": per_user}
    bot.get_config = lambda: tg
    seen = {"ran": False, "chat": 0, "desktop": 0, "sent": []}

    def chat_ask(*a, **k):
        seen["chat"] += 1
        return chat_answer

    def desk_ask(kin, cmd, why):
        seen["desktop"] += 1
        return desktop_answer

    bot._request_exec_approval_telegram = chat_ask
    bot._send_chunked = lambda chat_id, text: seen["sent"].append(text)
    bot.request_desktop_exec_approval = desk_ask if desktop else None

    def inner(args):
        seen["ran"] = True
        return "ok"

    wrapped = bot._wrap_exec_for_telegram(inner, "full", who, 555)
    seen["result"] = wrapped({"command": command})
    return seen


s = telegram_run(True, {}, FRIEND)
check("Telegram: kin box on, nothing set for them: runs, nobody asked",
      s["ran"] and s["chat"] == 0 and s["desktop"] == 0)
s = telegram_run(True, {FRIEND: "ask"}, FRIEND)
check("Telegram: 'ask first' for a friend asks YOUR desktop, not them",
      s["desktop"] == 1 and s["chat"] == 0 and s["ran"])
check("...and tells them it's waiting on you",
      any("operator" in t for t in s["sent"]))
s = telegram_run(True, {OPERATOR: "ask"}, OPERATOR)
check("Telegram: 'ask first' for you ('This is me') asks you in the chat",
      s["chat"] == 1 and s["desktop"] == 0 and s["ran"])
s = telegram_run(True, {SHARER: "ask"}, SHARER)
check("Telegram: someone you SHARE with, but who isn't you, is asked on "
      "your desktop, not in their chat",
      s["desktop"] == 1 and s["chat"] == 0)
_b = TelegramBot.__new__(TelegramBot)
_b.agent_name = KIN
_b.get_config = lambda: {"user_is_operator": {OPERATOR: True},
                         "user_share_desktop": {SHARER: True},
                         "user_tools": {SHARER: "write"}}
check("Telegram: only 'This is me' can wipe the kin's memory with /reset, "
      "not someone you share with",
      _b._user_is_privileged(OPERATOR) and not _b._user_is_privileged(SHARER))
s = telegram_run(False, {}, FRIEND)
check("Telegram: kin box off, a friend still can't approve their own",
      s["chat"] == 0 and s["desktop"] == 1)
s = telegram_run(True, {FRIEND: "ask"}, FRIEND, desktop_answer="deny")
check("Telegram: you say no on the desktop: it doesn't run",
      not s["ran"] and s["result"] == _DENY_RESULTS["deny"])
s = telegram_run(True, {FRIEND: "ask"}, FRIEND, desktop=False)
check("Telegram: no desktop to ask: it doesn't run, and nobody 'refused'",
      not s["ran"] and s["result"] == _DENY_RESULTS["unavailable"]
      and "obody refused" in s["result"])
s = telegram_run(False, {FRIEND: "auto"}, FRIEND)
check("Telegram: 'run without asking' for one person, kin box off: runs",
      s["ran"] and s["chat"] == 0 and s["desktop"] == 0)
s = telegram_run(True, {FRIEND: "auto"}, FRIEND, command="format c:")
check("Telegram: the never-run list still wins over 'run without asking'",
      not s["ran"])


# --- Discord, through the frame's real remote wrapper ----------------------
from frame.cron_exec_mixin import CronExecMixin  # noqa: E402
import frame.cron_exec_mixin as cem  # noqa: E402


def discord_run(kin_box, per_user, who, answer="allow", command="dir"):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["remote_unattended_exec"] = kin_box
    cfg["tool_trust"] = "full"
    cfg["discord"] = {"user_exec_approval": per_user}
    save_agent_config(KIN, cfg)
    seen = {"ran": False, "desktop": 0}

    class FakeFrame:
        def _request_exec_approval(self, kin, cmd, why):
            seen["desktop"] += 1
            return {"allow": cem.ExecApprovalDialog.DECISION_ALLOW,
                    "deny": cem.ExecApprovalDialog.DECISION_DENY}[answer]

    def inner(args):
        seen["ran"] = True
        return "ok"

    ex = CronExecMixin._wrap_exec_for_remote(
        FakeFrame(), {"exec": inner}, KIN, "discord", user_id=who)
    ex["exec"]({"command": command})
    return seen


s = discord_run(True, {}, FRIEND)
check("Discord: kin box on, nothing set: runs, nobody asked",
      s["ran"] and s["desktop"] == 0)
s = discord_run(True, {FRIEND: "ask"}, FRIEND)
check("Discord: 'ask first' asks your desktop", s["desktop"] == 1 and s["ran"])
s = discord_run(True, {FRIEND: "ask"}, FRIEND, answer="deny")
check("Discord: you say no: it doesn't run", not s["ran"])
s = discord_run(False, {FRIEND: "auto"}, FRIEND)
check("Discord: 'run without asking' for one person: runs",
      s["ran"] and s["desktop"] == 0)
s = discord_run(True, {FRIEND: "auto"}, FRIEND, command="rm -rf /")
check("Discord: the never-run list still wins", not s["ran"])

# The Discord bot has to say WHO asked, or the per-person setting can't apply.
t = ast.parse((ROOT / "discord_bot.py").read_text(encoding="utf-8"))
calls = [n for n in ast.walk(t) if isinstance(n, ast.Call)
         and getattr(n.func, "attr", "") == "_wrap_exec"]
check("Discord bot passes the person to the approval wrapper",
      bool(calls) and all(any(k.arg == "user_id" for k in c.keywords)
                          for c in calls))
t = ast.parse((ROOT / "frame" / "bot_integration_mixin.py").read_text(
    encoding="utf-8"))
kws = {k.arg for n in ast.walk(t) if isinstance(n, ast.Call)
       for k in n.keywords}
check("the desktop hands Telegram a way to ask you",
      "request_desktop_exec_approval" in kws)

# The settings window saves and clears it for both surfaces.
# Since 12 Nov 2026 that's the shared person list, used by both surfaces.
from tools._person_settings import save_person, load_person, remove_person  # noqa: E402
src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
ok = "_person_window_flow" in src and "remove_person" in src
for sf in ("telegram", "discord"):
    sec = save_person({}, sf, None, "1",
                      dict(load_person({}, sf, ""), exec_approval="ask"))
    ok = ok and load_person(sec, sf, "1")["exec_approval"] == "ask"
    ok = ok and "1" not in remove_person(sec, sf, "1")["user_exec_approval"]
check("the settings window saves the choice (Telegram and Discord, add and "
      "edit) and clears it when a person is removed", ok)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_per_person_exec_approval: all checks passed")
