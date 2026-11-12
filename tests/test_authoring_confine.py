# SPDX-License-Identifier: CC0-1.0
"""Guard test: a fenced-block save on Telegram obeys the same folder limit as
the file tools there.

On Telegram, write_file and edit_file are confined to the kin's own folder by
default (confine_paths, audit D1): an absolute path is refused, so a remote
person — or text injected into the conversation — can't get the kin to write
anywhere on the host. But a kin can also save by writing a fenced block
(```write:path```), and that went through authoring_bridge.commit_authoring_writes
with no confinement at all. The same kin, for the same person, could reach with
a fence exactly where the tool had been stopped.

Checked here: with confine on, an absolute path and a `..` escape are refused
and nothing is written; a relative path still lands inside the kin folder. The
positive control writes the same absolute path with confine off, so a pass means
the check can see the difference. And the Telegram call site must actually pass
the flag, checked on the real syntax tree so a comment can't satisfy it.

Run: python tests/test_authoring_confine.py
"""

import ast
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="authconfine-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import authoring_bridge as ab  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Fenwick"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
outside = Path(tempfile.mkdtemp(prefix="authconfine-outside-"))
W = ab.AuthoringWrite

# --- confined: what a Telegram kin gets by default ----------------------
target_abs = outside / "escaped.txt"
res = ab.commit_authoring_writes(
    KIN, [W(str(target_abs), "should not land", "write")], confine=True)
check("confined: an absolute path is refused", res and res[0][1] is False)
check("...and nothing was written there", not target_abs.exists())

res = ab.commit_authoring_writes(
    KIN, [W("../escaped-up.txt", "should not land", "write")], confine=True)
check("confined: a .. escape is refused", res and res[0][1] is False)
check("...and nothing was written above the kin folder",
      not (kin_dir(KIN).parent / "escaped-up.txt").exists())

res = ab.commit_authoring_writes(
    KIN, [W("notes/fence.md", "kept", "write")], confine=True)
inside = kin_dir(KIN) / "notes" / "fence.md"
check("confined: a relative path still saves inside the kin folder",
      res and res[0][1] is True and inside.exists()
      and inside.read_text(encoding="utf-8").strip() == "kept")

# --- positive control: unconfined, the desktop's rule --------------------
target_ctrl = outside / "allowed-on-desktop.txt"
res = ab.commit_authoring_writes(
    KIN, [W(str(target_ctrl), "desktop reach", "write")], confine=False)
check("control: unconfined, the same kind of absolute path IS written",
      res and res[0][1] is True and target_ctrl.exists())

# --- the Telegram call site passes the flag ------------------------------
tree = ast.parse((ROOT / "telegram_bot.py").read_text(encoding="utf-8"))
calls = [n for n in ast.walk(tree)
         if isinstance(n, ast.Call)
         and getattr(n.func, "attr", getattr(n.func, "id", "")) == "commit_authoring_writes"]
check("telegram_bot.py calls commit_authoring_writes", bool(calls))
check("...and every call passes confine=",
      bool(calls) and all(any(k.arg == "confine" for k in c.keywords)
                          for c in calls))

# --- end to end: Telegram's real fence path follows the SAME checkbox ------
# The kin Settings checkbox "Let remote (Telegram/Discord) file tools reach
# outside the kin folder" saves remote_unconfined_files (dialogs/tool_settings.py).
# A fence save must obey that one checkbox, not a switch of its own. So this
# drives TelegramBot._run_authoring_bridge_telegram — the method a real
# Telegram reply goes through — with the setting off and then on.
from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from telegram_bot import TelegramBot  # noqa: E402

bot = TelegramBot.__new__(TelegramBot)   # no network, no threads: just the method
bot.agent_name = KIN


def _fence_save_through_telegram(target, checkbox_on):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["remote_unconfined_files"] = checkbox_on
    save_agent_config(KIN, cfg)
    reply = "Here it is.\n```write:%s\nfrom telegram\n```\n" % target.as_posix()
    return bot._run_authoring_bridge_telegram(
        reply, ["write_file", "edit_file"], [])


tg_off = outside / "via-telegram-checkbox-off.txt"
note_off, _chat_off = _fence_save_through_telegram(tg_off, checkbox_on=False)
check("Telegram, checkbox OFF: a fence to a path outside the kin folder is "
      "refused", not tg_off.exists())
check("...and the kin is told it could not save", bool(note_off)
      and "could NOT save" in note_off)

tg_on = outside / "via-telegram-checkbox-on.txt"
note_on, _chat_on = _fence_save_through_telegram(tg_on, checkbox_on=True)
check("Telegram, checkbox ON: the same kind of fence save lands outside, "
      "exactly as the file tools would", tg_on.exists())

# --- per person (12 Nov 2026): the person's own files setting wins ---------
from tools._person_settings import save_person, load_person  # noqa: E402

tg_sec = save_person({}, "telegram", None, "500",
                     dict(load_person({}, "telegram", ""), file_reach="outside"))
bot.get_config = lambda: tg_sec
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["remote_unconfined_files"] = False
save_agent_config(KIN, cfg)
tg_person = outside / "via-telegram-person-outside.txt"
bot._run_authoring_bridge_telegram(
    "Here.\n```write:%s\nper person\n```\n" % tg_person.as_posix(),
    ["write_file", "edit_file"], [], user_id="500")
check("Telegram: a person set to 'allow outside' saves outside, kin box off",
      tg_person.exists())
tg_other = outside / "via-telegram-other-person.txt"
bot._run_authoring_bridge_telegram(
    "Here.\n```write:%s\nnot them\n```\n" % tg_other.as_posix(),
    ["write_file", "edit_file"], [], user_id="501")
check("...while someone without that setting still can't",
      not tg_other.exists())
del bot.get_config

# --- Discord: the same rule, through its own path ---------------------------
# Discord saves fences via turn_steering.authoring_bridge_notes. It used to
# call that without confine=, so a fence on Discord reached anywhere while
# Discord's file tools were confined (found by the 12 Nov 2026 surface audit).
import turn_steering  # noqa: E402

for path_rel in ("turn_steering.py", "discord_bot.py"):
    t = ast.parse((ROOT / path_rel).read_text(encoding="utf-8"))
    want = ("commit_authoring_writes" if path_rel == "turn_steering.py"
            else "authoring_bridge_notes")
    cs = [n for n in ast.walk(t) if isinstance(n, ast.Call)
          and getattr(n.func, "attr", getattr(n.func, "id", "")) == want]
    check(f"{path_rel}: every {want} call passes confine=",
          bool(cs) and all(any(k.arg == "confine" for k in c.keywords) for c in cs))

FENCE = "Here.\n```write:%s\nfrom discord\n```\n"
dc_out = outside / "via-discord-confined.txt"
note_dc, _h = turn_steering.authoring_bridge_notes(
    KIN, FENCE % dc_out.as_posix(),
    ["write_file", "edit_file"], confine=True)
check("Discord-style confined fence to a path outside the kin folder is refused",
      not dc_out.exists())
dc_in = outside / "via-discord-unconfined.txt"
turn_steering.authoring_bridge_notes(
    KIN, FENCE % dc_in.as_posix(),
    ["write_file", "edit_file"], confine=False)
check("...and with the checkbox on (unconfined) the same save lands",
      dc_in.exists())

# Control for the syntax check: a call without the keyword must be seen as
# missing it, or the check above proves nothing.
probe = ast.parse("authoring_bridge.commit_authoring_writes(kin, writes)")
probe_calls = [n for n in ast.walk(probe) if isinstance(n, ast.Call)]
check("control: a call without confine= is detected as missing it",
      not any(k.arg == "confine" for k in probe_calls[0].keywords))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_authoring_confine: all checks passed")
