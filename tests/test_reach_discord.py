# SPDX-License-Identifier: CC0-1.0
"""A kin can reach someone on Discord (12 Nov 2026).

reach_out, a wake-up's "Send this message to", and the heartbeat's default
place all knew Telegram only, so a kin whose person was on Discord could
reach nobody there. They now share one list of places and one sender
(reach_places), with Discord people and channels in both.

Checks: what the list holds (and never holds), that two places can't share a
name, that reach_out sends to a Discord person or channel it was opened to
and nowhere else, that a send which can't happen keeps the words and says
why, and that a wake-up's chosen places include Discord. Nothing leaves the
machine: the network calls are stubbed.

Run: python tests/test_reach_discord.py
"""

import os
import sys
import tempfile

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="reachdc-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord_mirror  # noqa: E402
import hearthkin_cron  # noqa: E402
import kin_persistence  # noqa: E402
import reach_places  # noqa: E402
import telegram_bot  # noqa: E402
from tools import reach_out  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


CFG = {
    "telegram": {"enabled": True, "bot_token": "T",
                 "allow_from": ["111", "*"], "user_labels": {"111": "Wren"},
                 "groups": {"-100": {"label": "Book club"}}},
    "discord": {"enabled": True, "bot_token": "D",
                "allow_from": ["222", "333", "*"],
                "user_labels": {"222": "Wren", "333": "Ash"},
                "channels": ["444"]},
}

# --- the list -------------------------------------------------------------
rows = reach_places.place_rows(CFG)
by = {(r["surface"], r["id"]): r for r in rows}
check("Discord people are places", ("discord_dm", "333") in by
      and by[("discord_dm", "333")]["label"] == "Ash")
check("...and the channels the kin answers in",
      ("discord_channel", "444") in by)
check("'anyone' is never a place on either app",
      not any(r["id"] == "*" for r in rows))
check("Telegram's places are still there",
      ("telegram_dm", "111") in by and ("telegram_group", "-100") in by)
check("two people called Wren get names a kin can tell apart",
      by[("telegram_dm", "111")]["label"] == "Wren on Telegram"
      and by[("discord_dm", "222")]["label"] == "Wren on Discord")
check("the settings window line says which app",
      by[("discord_dm", "333")]["display"] == "Discord DM: Ash (333)")
twins = reach_places.place_rows({"telegram": {"groups": {
    "-1": {"label": "Book club"}, "-2": {"label": "Book club"}}}})
check("two groups with one name are told apart by their ID",
      len({r["label"] for r in twins}) == 2)

# --- reach_out ------------------------------------------------------------
sent = []


def fake_dm(token, uid, text, kin="", timeout=15):
    sent.append(("dm", token, uid, text))
    return True


def fake_channel(token, cid, text, kin="", timeout=15):
    sent.append(("channel", token, cid, text))
    return True


def fake_tg(token, method, payload, timeout=None):
    sent.append(("telegram", token, payload.get("chat_id"), payload.get("text")))
    return {"ok": True}


recorded = []
cfg_now = {}
discord_mirror.send_dm = fake_dm
discord_mirror.send_to_channel = fake_channel
telegram_bot.telegram_api_call = fake_tg
kin_persistence.load_agent_config = lambda name: cfg_now
kin_persistence.append_agent_conversation_turn = (
    lambda name, msg, **k: recorded.append(msg))


def ask(to, msg, cfg):
    cfg_now.clear()
    cfg_now.update(cfg)
    sent.clear()
    recorded.clear()
    return reach_out(msg, to=to, agent_name="Bracken")


opened = dict(CFG, heartbeat={
    "destination": {"surface": "discord_dm", "id": "333"},
    "allowed_destinations": [
        {"label": "Wren on Discord", "surface": "discord_dm", "id": "222"},
        {"label": "444", "surface": "discord_channel", "id": "444"}]})

out = ask("", "I found the blue feather.", opened)
check("with Discord as the default place, a reach-out goes to that person",
      sent == [("dm", "D", "333", "I found the blue feather.")])
check("...the kin is told it went to Discord", "Discord" in out)
check("...and it's in the kin's own history", len(recorded) == 1)

out = ask("Wren on Discord", "Morning!", opened)
check("naming an opened Discord person reaches them",
      sent == [("dm", "D", "222", "Morning!")] and "Discord" in out)
out = ask("444", "Hello, channel.", opened)
check("naming an opened channel posts there",
      sent == [("channel", "D", "444", "Hello, channel.")])
out = ask("Ash", "psst", opened)
check("a person who wasn't opened can't be reached by name",
      sent == [] and "nothing was sent" in out)

closed = dict(opened, discord=dict(CFG["discord"], enabled=False))
out = ask("Wren on Discord", "Still there?", closed)
check("with Discord off, nothing is sent and the kin is told why",
      sent == [] and "Discord isn't fully set up" in out)
check("...and the words are kept, not lost", len(recorded) == 1)

# --- a wake-up's chosen places ------------------------------------------
sent.clear()
hearthkin_cron._deliver_to_destinations("Bracken", CFG, "Good morning.", [
    {"surface": "desktop"},
    {"surface": "discord_dm", "id": "333"},
    {"surface": "discord_channel", "id": "444"},
    {"surface": "telegram_group", "id": "-100"}])
check("a wake-up sent to Discord places reaches them, Telegram too",
      sent == [("dm", "D", "333", "Good morning."),
               ("channel", "D", "444", "Good morning."),
               ("telegram", "T", "-100", "Good morning.")])
sent.clear()
hearthkin_cron._deliver_to_destinations(
    "Bracken", dict(CFG, telegram={"enabled": False}), "Hi.",
    [{"surface": "discord_dm", "id": "333"}])
check("Telegram being off no longer stops a Discord send",
      sent == [("dm", "D", "333", "Hi.")])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_reach_discord: all checks passed")
