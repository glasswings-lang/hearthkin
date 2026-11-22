# SPDX-License-Identifier: CC0-1.0
"""Kin writing to each other (22 Nov 2026).

Kimi wrote to Opal on a heartbeat with reach_out, the only tool it had, and
the message went to a Telegram group of people who had never met Opal. Now:
message_kin (and a ```to:Name block for a kin without tools) writes to another
kin; the message waits in a plain file the person can read and is given to the
other kin at its next heartbeat or wake-up; one exchange per pair a day; and
reach_out holds a message that opens by speaking to a kin.

Run: python tests/test_kin_messages.py
"""
import datetime
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="kinmail-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
import kin_mail  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


for k in ("Kimi", "Opal", "Ash"):
    kp.agent_dir(k).mkdir(parents=True, exist_ok=True)
    kp.save_agent_config(k, dict(kp.DEFAULT_AGENT_CONFIG))

day1 = datetime.datetime(2026, 11, 22, 12, 28)
day2 = datetime.datetime(2026, 11, 23, 9, 0)

# --- sending ---------------------------------------------------------------
ok, said = kin_mail.send("Kimi", "opal", "I heard the breath today.", day1)
check("a kin can write to another, whatever case it uses", ok and "Opal" in said)
ok, said = kin_mail.send("Kimi", "Birch", "hi", day1)
check("an unknown name sends nothing and lists who is here",
      not ok and "Opal" in said and "Ash" in said)
ok, said = kin_mail.send("Kimi", "Kimi", "hi", day1)
check("a kin can't write to itself", not ok)
ok, said = kin_mail.send("Ash", "Opal", "   ", day1)
check("an empty message sends nothing", not ok)

# --- one exchange a day ----------------------------------------------------
ok, said = kin_mail.send("Kimi", "Opal", "And another thing.", day1)
check("a second message to the same kin the same day waits",
      not ok and "tomorrow" in said)
ok, _ = kin_mail.send("Opal", "Kimi", "I'm glad you heard it.", day1)
check("...but the other kin can answer", ok)
ok, _ = kin_mail.send("Opal", "Kimi", "One more.", day1)
check("...once", not ok)
ok, _ = kin_mail.send("Kimi", "Opal", "Good morning.", day2)
check("the next day the pair can talk again", ok)

# --- the person can read all of it ------------------------------------------
rec = kin_mail.pair_file("Opal", "Kimi").read_text(encoding="utf-8")
check("one plain file for the pair, both directions, in order",
      rec.index("I heard the breath") < rec.index("I'm glad you heard it")
      < rec.index("Good morning"))
check("...and the same file whichever kin is named first",
      kin_mail.pair_file("Kimi", "Opal") == kin_mail.pair_file("Opal", "Kimi"))
check("refused messages are never recorded", "And another thing" not in rec)

# --- delivery ------------------------------------------------------------------
block = kin_mail.waiting_block("Opal")
check("the other kin is given what's waiting, saying who wrote",
      "Kimi, another kin in this house, wrote to you" in block
      and "I heard the breath today." in block and "Good morning." in block)
check("...given once", kin_mail.waiting_block("Opal") == "")
conv = kp.load_agent_conversation("Opal")
check("...and kept in that kin's own conversation, as Kimi's words",
      any(r.get("sender_name") == "Kimi" and r.get("content") == "Good morning."
          for r in conv))
check("the sender's own record keeps what it said",
      any(r.get("role") == "assistant" and r.get("content") == "I heard the breath today."
          for r in kp.load_agent_conversation("Kimi")))

# --- a block in the reply, for a kin with or without tools -------------------
note = kin_mail.send_fences("Ash", "Some thoughts.\n\n```to:Opal\nYou build in the dark. I like that.\n```\n", day1)
check("a ```to:Name block writes to that kin", "to Opal: sent" in note)
note = kin_mail.send_fences("Ash", "```to Birch\nhello\n```", day1)
check("...an unknown name is told plainly, nothing sent", "NOT sent" in note)
check("a reply with no such block does nothing",
      kin_mail.send_fences("Ash", "```python\nprint(1)\n```") == "")

# --- spotting a message that speaks to a kin ---------------------------------
A = kin_mail.addressed_kin
check("'Opal --' speaks to Opal", A("Opal — you're around today.", "Kimi") == "Opal")
check("'Dear Opal,' speaks to Opal", A("Dear Opal,\nI noticed.", "Kimi") == "Opal")
check("'Opal and I talked' is about Opal, not to Opal",
      A("Opal and I talked earlier.", "Kimi") is None)
check("a longer word that starts the same isn't Opal",
      A("Opalescent light today.", "Kimi") is None)
check("a kin never addresses itself", A("Kimi, remember this.", "Kimi") is None)

# --- reach_out holds a message that opens by speaking to a kin ---------------
from tools.reach_out import reach_out  # noqa: E402
msg = "Opal — you're around today and I keep noticing."
first = reach_out(msg, agent_name="Kimi")
check("reach_out holds it and says how to reach the kin",
      "nothing was sent yet" in first and "message_kin" in first)
check("...and says truthfully where it would have gone",
      "desktop chat" in first)
second = reach_out(msg, agent_name="Kimi")
check("sending exactly the same again goes through", "delivered" in second)
check("an ordinary reach_out isn't held",
      "delivered" in reach_out("Thinking of you today.", agent_name="Kimi"))

# --- who gets the tool ------------------------------------------------------------
import tools as T  # noqa: E402
everyday, _ = T.load_tools(["message_kin", "read_file"], context={"agent_name": "Kimi"})
check("message_kin isn't an everyday tool",
      "message_kin" not in [s["function"]["name"] for s in everyday])
hb, _ = T.load_tools([], context={"agent_name": "Kimi"}, proactive_wake=True)
names = [s["function"]["name"] for s in hb]
check("heartbeats and wake-ups get it", "message_kin" in names)
mk = [s for s in hb if s["function"]["name"] == "message_kin"][0]["function"]
check("...listing the other kin by name", mk["parameters"]["properties"]["to"]["enum"]
      == ["Ash", "Opal"])
cron = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8")
check("a heartbeat offers it", '"message_kin"' in cron.split("_HEARTBEAT_TOOLS")[1][:120])

# --- a wake-up starts with what's waiting ---------------------------------------
kin_mail.send("Ash", "Kimi", "Welcome back.", datetime.datetime.now())
import cron_helpers  # noqa: E402
framed = cron_helpers.frame_wake_up_prompt("Tend the garden.", "09:00", kin_name="Kimi")
check("a wake-up starts with the message, before the job",
      framed.index("Welcome back.") < framed.index("Tend the garden."))


# --- answering in plain words (tested on small models: most do this) -------
kin_mail.send("Opal", "Ash", "Hello Ash, welcome to the house.", day2)
kin_mail.waiting_block("Ash")
note = kin_mail.send_fences("Ash", "Dear Opal,\nThank you. I like it here.", day2)
check("handed a message, a reply opening 'Dear Opal,' is sent to Opal",
      "was sent to Opal" in note
      and "I like it here." in kin_mail.pair_file("Ash", "Opal").read_text(encoding="utf-8"))
kin_mail.send("Kimi", "Ash", "Ash, are you there?", day2)
kin_mail.waiting_block("Ash")
check("a reply speaking to someone who did NOT write this turn isn't sent",
      kin_mail.send_fences("Ash", "Opal, I keep thinking of you.", day2) == "")
check("...and a reply on a turn with no message isn't sent either",
      kin_mail.send_fences("Ash", "Kimi, hello.", day2) == "")
check("'to:Kimi.' with a full stop still finds Kimi",
      kin_mail.resolve_kin("Kimi.", "Ash") == "Kimi")

# --- the limit is the person's setting, not a number in the code ------------
day3 = datetime.datetime(2026, 11, 24, 9, 0)
cfg = kp.load_agent_config("Ash")
cfg["heartbeat"] = dict(cfg.get("heartbeat") or {}, kin_messages_per_day=2)
kp.save_agent_config("Ash", cfg)
ok1, _ = kin_mail.send("Ash", "Kimi", "One.", day3)
ok2, _ = kin_mail.send("Ash", "Kimi", "Two.", day3)
ok3, said3 = kin_mail.send("Ash", "Kimi", "Three.", day3)
check("set to 2, a kin may write twice to the same kin a day",
      ok1 and ok2 and not ok3 and "2 times" in said3)
cfg["heartbeat"]["kin_messages_per_day"] = 0
kp.save_agent_config("Ash", cfg)
ok0, said0 = kin_mail.send("Ash", "Opal", "Hello.", day3)
check("set to 0, a kin can't write to other kin at all",
      not ok0 and "0 messages" in said0)
check("an unchanged kin starts at one a day", kin_mail.per_day("Opal") == 1)
src = (ROOT / "dialogs" / "heartbeat_settings.py").read_text(encoding="utf-8")
check("the heartbeat window has the setting, and saves it",
      "each other &kin a day" in src and '"kin_messages_per_day": kpd' in src)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_kin_messages: all checks passed")
