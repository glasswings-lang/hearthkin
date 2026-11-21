# SPDX-License-Identifier: CC0-1.0
"""Two sharing switches per group or channel, and one rule every surface asks.

On 20 Nov 2026 the operator talked code with a kin in a Telegram group kept
"shared with desktop", moved to their DM, and the kin had none of it. The
checkbox promised to unify the two; it only stored them together, and every
surface walled the group off again by hand. Agreed that day: two switches,
one per direction.

  private_sees  the private side (desktop, shared DMs, wake-ups) includes a
                group or channel, each message marked with where it was said.
                On for groups shared before the switches existed.
  sees_private  a group or channel includes the private side. Off unless
                chosen.

Pinned here, through the real code paths with a real conversation file:
  * the rule itself (chat_helpers.visible_rows), both directions, the marks,
    and the old-checkbox default;
  * the Telegram DM, the Telegram group, the desktop, a wake-up, and Discord
    (a direct message and a server channel) all following it;
  * a Discord DM's mark surviving a save and reload, and its rows from before
    the mark still reaching it;
  * the check can fail: with the switches off, nothing crosses.

Run: python tests/test_sharing_switches.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="sharing-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_helpers import PRIVATE, visible_rows  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, load_agent_config,  # noqa: E402
                             load_agent_conversation, save_agent_config,
                             save_agent_conversation)
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


ME, GROUP, CHAN, DMCH = "7", "-100", "555", "777"
GTAG, CTAG = f"telegram:group:{GROUP}", f"discord:{CHAN}"


def rows():
    return [
        {"role": "user", "content": "desktop hello", "ts": "2026-11-20T10:00:00"},
        {"role": "assistant", "content": "desktop reply", "ts": "2026-11-20T10:00:01"},
        {"role": "user", "content": "group code talk", "source": GTAG,
         "sender_id": ME, "sender_attribution": "Wren (@wren)",
         "ts": "2026-11-20T11:00:00"},
        {"role": "assistant", "content": "group reply about code",
         "source": GTAG, "ts": "2026-11-20T11:00:01"},
        {"role": "user", "content": "channel chatter", "source": CTAG,
         "ts": "2026-11-20T11:30:00"},
        {"role": "user", "content": "old discord dm", "source": f"discord:{DMCH}",
         "ts": "2026-11-20T11:40:00"},
        {"role": "user", "content": "new discord dm", "source": f"discord:{DMCH}",
         "dm": True, "ts": "2026-11-20T11:50:00"},
        {"role": "user", "content": "dm hello", "source": f"telegram:{ME}",
         "ts": "2026-11-20T12:00:00"},
    ]


def words(view):
    return [str(r.get("content") or "") for r in view]


def has(view, text):
    return any(text in w for w in words(view))


def cfg_with(share=None, ps=None, sp=None, cps=None, csp=None):
    tg = {"groups": {GROUP: {"label": "Code club", "policy": "always"}},
          "user_share_desktop": {ME: True}, "allow_from": [ME]}
    if share is not None:
        tg["group_share_desktop"] = {GROUP: share}
    if ps is not None:
        tg["group_private_sees"] = {GROUP: ps}
    if sp is not None:
        tg["group_sees_private"] = {GROUP: sp}
    dc = {"channel_labels": {CHAN: "general"}}
    if cps is not None:
        dc["channel_private_sees"] = {CHAN: cps}
    if csp is not None:
        dc["channel_sees_private"] = {CHAN: csp}
    return {"telegram": tg, "discord": dc}


print("--- the rule ---")
v = visible_rows(rows(), PRIVATE, cfg_with(share=True))
check("a group shared before the switches reaches the private side",
      has(v, "group code talk"))
check("...marked with where it was said",
      has(v, '(said in the Telegram group "Code club") group code talk'))
check("...the kin's own reply there comes too, unmarked",
      "group reply about code" in words(v))
v = visible_rows(rows(), PRIVATE, cfg_with(share=True, ps=False))
check("switching it off keeps the group out of the private side",
      not has(v, "group code talk"))
v = visible_rows(rows(), PRIVATE, cfg_with())
check("the private side has its own rows and the Telegram DM's",
      has(v, "desktop hello") and has(v, "dm hello"))
check("...a marked Discord DM counts as private",
      has(v, "new discord dm"))
check("...an unmarked old Discord row is not assumed private",
      not has(v, "old discord dm"))
check("...and a Discord channel stays out unless switched on",
      not has(v, "channel chatter"))
v = visible_rows(rows(), PRIVATE, cfg_with(cps=True))
check("a Discord channel switched on reaches the private side, named",
      has(v, '(said in the Discord channel "general") channel chatter'))

g = visible_rows(rows(), GTAG, cfg_with(share=True))
check("a group sees its own rows", has(g, "group code talk"))
check("...but not the private side unless switched on",
      not has(g, "desktop hello") and not has(g, "dm hello"))
g = visible_rows(rows(), GTAG, cfg_with(share=True, sp=True))
check("switched on, the group gets the private side, marked as private",
      has(g, "(said in your private chats) desktop hello"))
check("...and still never another group's or channel's rows",
      not has(g, "channel chatter"))

v = visible_rows(rows(), PRIVATE, cfg_with(), own_place=f"discord:{DMCH}")
check("a Discord DM keeps its own rows from before DMs were marked, as "
      "they are", "old discord dm" in words(v))

print("--- through the real code, with a real conversation file ---")
KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)


def setup(**kw):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["model"] = "stub-model"
    cfg.update(cfg_with(**kw))
    save_agent_config(KIN, cfg)
    save_agent_conversation(KIN, rows())


import telegram_bot as _tb  # noqa: E402


def tg_bot():
    b = _tb.TelegramBot.__new__(_tb.TelegramBot)
    b.__init__(KIN, get_config=lambda: load_agent_config(KIN)["telegram"],
               get_soul=lambda: "", get_memory=lambda: "",
               get_model_options=lambda: ("stub-model", {}),
               on_status=lambda *a: None)
    return b


setup(share=True)
dm = tg_bot()._load_history_for(int(ME))
check("Telegram DM: the shared group's talk is there (the reported case)",
      has(dm, "group code talk"))
check("...marked with the group's name", has(dm, 'Telegram group "Code club"'))
grp = tg_bot()._load_group_history(int(GROUP))
check("Telegram group: its own talk, and none of the private side",
      has(grp, "group code talk") and not has(grp, "desktop hello"))
setup(share=True, sp=True)
grp = tg_bot()._load_group_history(int(GROUP))
check("Telegram group switched to get the private side: it does",
      has(grp, "desktop hello"))
setup(share=True, ps=False)
dm = tg_bot()._load_history_for(int(ME))
check("Telegram DM with the switch off: no group talk", not has(dm, "group code talk"))

from frame.render_mixin import RenderMixin  # noqa: E402


class _Frame(RenderMixin):
    def __init__(self, cfg):
        self.agent_cfg = cfg


setup(share=True)
desk = _Frame(load_agent_config(KIN))._private_view(load_agent_conversation(KIN))
check("desktop: the shared group's talk is there, marked",
      has(desk, 'Telegram group "Code club"'))
entries = [_Frame({})._history_entry_for_model(m) for m in desk]
check("...and it survives into what the kin is sent",
      any("group code talk" in str((e or {}).get("content")) for e in entries))

import hearthkin_cron  # noqa: E402
msgs = hearthkin_cron._build_messages(KIN, "wake up")
check("a wake-up: the shared group's talk is there, marked",
      has(msgs, 'Telegram group "Code club"'))
check("...and a Discord channel not switched on is not",
      not has(msgs, "channel chatter"))

from discord_bot import DiscordBot  # noqa: E402


def dc_bot():
    d = DiscordBot.__new__(DiscordBot)
    d.agent_name = KIN
    d.get_config = lambda: load_agent_config(KIN)
    d._dm_channels = {DMCH}
    return d


setup(share=True)
dmh = dc_bot()._channel_history(DMCH, True)
check("Discord DM: gets the private side, the shared group included",
      has(dmh, "desktop hello") and has(dmh, "group code talk"))
check("...and its own messages from before DMs were marked",
      has(dmh, "old discord dm"))
chh = dc_bot()._channel_history(CHAN, True)
check("Discord channel: only itself", has(chh, "channel chatter")
      and not has(chh, "desktop hello"))
setup(share=True, csp=True)
chh = dc_bot()._channel_history(CHAN, True)
check("Discord channel switched to get the private side: it does",
      has(chh, "desktop hello"))
check("a Discord channel with a switch on is kept with the desktop",
      dc_bot()._shares_desktop("999", CHAN) is True
      and dc_bot()._shares_desktop("999", "123") is False)

print("--- a Discord DM's mark survives a reload ---")
setup()
back = load_agent_conversation(KIN)
check("the dm mark is kept on reading back",
      any(r.get("dm") is True for r in back if r.get("content") == "new discord dm"))

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_sharing_switches: all checks passed")
