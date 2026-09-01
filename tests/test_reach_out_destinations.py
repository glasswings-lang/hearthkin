"""reach_out addressing: a kin can reach only where the operator opened.

Plain Python; run via tests/run_all.py.

Why this exists: Opal spent six consecutive days writing finished, addressed
letters to people it had no channel to — 9 of 11 morning wake-ups — because
reach_out could only ever reach the operator. The allowlist is the fix AND the
entire security model, so it gets a test that tries to break out of it.

The kin must also be able to SEE the list: an allowlist it can't read is the
same as no channel at all, which is the bug it's fixing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


import tools
# NB: `tools.reach_out` is the FUNCTION — tools/__init__.py does
# `from .reach_out import reach_out`, which shadows the module of that name.
from tools import reach_out as _reach_out


class RO:
    reach_out = staticmethod(_reach_out)

SPEAKEREIGHT = {"label": "SpeakerSix and Opal", "surface": "telegram_group", "id": "-1001234567890"}
OTHER = {"label": "the weekend group", "surface": "telegram_group", "id": "-1009876543210"}

CFG = {
    "telegram": {"enabled": True, "bot_token": "T", "groups": {}},
    "heartbeat": {"destination": {"surface": "desktop"},
                  "allowed_destinations": [SPEAKEREIGHT, OTHER]},
}

sent = []
RO_cfg = {"cfg": CFG}


def _fake_load_agent_config(name):
    return RO_cfg["cfg"]


def _fake_api(token, method, payload, timeout=None):
    sent.append((method, payload.get("chat_id"), payload.get("text")))
    return {"ok": True}


def _fake_append(name, msg):
    pass


# Patch the framework reach_out imports lazily inside the function body.
import kin_persistence
import telegram_bot
kin_persistence.load_agent_config = _fake_load_agent_config
kin_persistence.append_agent_conversation_turn = _fake_append
telegram_bot.telegram_api_call = _fake_api


# ── The allowlist is the security model ──────────────────────────────
sent.clear()
r = RO.reach_out("hello SpeakerSix", to="SpeakerSix and Opal", agent_name="Opal")
check(len(sent) == 1 and sent[0][1] == "-1001234567890",
      "a named, opened place receives the message")
check("SpeakerSix and Opal" in r, "the kin is told where it landed")

sent.clear()
r = RO.reach_out("psst", to="SpeakerFifteen's mum", agent_name="Opal")
check(not sent, "a place that was NEVER opened receives nothing")
check("SpeakerSix and Opal" in r and "the weekend group" in r,
      "...and the kin is told the real options instead of failing blindly")

# Case/whitespace forgiveness — the kin retypes a label from its schema; a
# capital letter must not be the thing that stops a message (the forgiving-
# contract convention: heal the fumble, never the wrong action).
sent.clear()
RO.reach_out("hi", to="  speakersix AND opal ", agent_name="Opal")
check(len(sent) == 1 and sent[0][1] == "-1001234567890",
      "a label match is case- and whitespace-forgiving")

# No `to` -> the operator, exactly as before this feature existed.
sent.clear()
r = RO.reach_out("just you", agent_name="Opal")
check(not sent and "desktop" in r,
      "no `to` still means the operator (unchanged default)")

# A kin with NO allowlist cannot address anywhere, and is told so plainly.
sent.clear()
RO_cfg["cfg"] = {"telegram": {"enabled": True, "bot_token": "T"},
                 "heartbeat": {"destination": {"surface": "desktop"}}}
r = RO.reach_out("hello?", to="SpeakerSix and Opal", agent_name="Opal")
check(not sent, "a kin with an empty allowlist reaches nowhere")
check("hasn't opened any place" in r,
      "...and is told that, rather than that it got the name wrong")
RO_cfg["cfg"] = CFG

sent.clear()
r = RO.reach_out("   ", to="SpeakerSix and Opal", agent_name="Opal")
check(not sent, "an empty message sends nothing")


# ── The kin must be able to SEE what's open to it ────────────────────
schemas, executor = tools.load_tools(
    [], context={"agent_name": "Opal"}, cron_turn=True)
ro = [s for s in schemas if (s.get("function") or {}).get("name") == "reach_out"]
check(len(ro) == 1, "a cron turn grants reach_out")
desc = (ro[0]["function"]["description"] if ro else "")
check("SpeakerSix and Opal" in desc and "the weekend group" in desc,
      "the schema NAMES the open places (an unreadable allowlist is no channel)")
props = ((ro[0]["function"].get("parameters") or {}).get("properties") or {}) if ro else {}
check(props.get("to", {}).get("enum") == ["SpeakerSix and Opal", "the weekend group"],
      "the `to` parameter is constrained to the open places")

# A kin with no allowlist gets no list appended — and no broken promise.
RO_cfg["cfg"] = {"heartbeat": {}}
schemas2, _ = tools.load_tools([], context={"agent_name": "Nobody"}, cron_turn=True)
ro2 = [s for s in schemas2 if (s.get("function") or {}).get("name") == "reach_out"]
d2 = (ro2[0]["function"]["description"] if ro2 else "")
check("Places your operator has opened" not in d2,
      "a kin with nowhere open is not shown an empty list")

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
    sys.exit(1)
print("test_reach_out_destinations.py: all checks passed")
