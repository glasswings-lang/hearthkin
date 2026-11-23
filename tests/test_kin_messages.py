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
      any(r.get("sender_name") == "Kimi, another kin in this house"
          and r.get("content") == "Good morning."
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
# --- in a conversation with the person: asked for, so not counted -----------
cfg["heartbeat"]["kin_messages_per_day"] = 1
kp.save_agent_config("Ash", cfg)
day4 = datetime.datetime(2026, 11, 25, 9, 0)
before = len(kp.load_agent_conversation("Ash"))
okp1, _ = kin_mail.send("Ash", "Kimi", "Sam asked me to say hi.", day4, prompted=True)
okp2, _ = kin_mail.send("Ash", "Kimi", "And once more, for Sam.", day4, prompted=True)
check("asked for in a conversation, it doesn't count against the limit",
      okp1 and okp2 and kin_mail.can_send("Ash", "Kimi", day4)[0])
check("...and the record says it was in a conversation with the person",
      "in a conversation with their person" in
      kin_mail.pair_file("Ash", "Kimi").read_text(encoding="utf-8"))
check("...and it isn't recorded twice in the sender's own conversation",
      len(kp.load_agent_conversation("Ash")) == before)
cfg["heartbeat"]["kin_messages_per_day"] = 0
kp.save_agent_config("Ash", cfg)
check("0 still means none, even when asked",
      not kin_mail.send("Ash", "Kimi", "hi", day4, prompted=True)[0])
cfg["heartbeat"]["kin_messages_per_day"] = 1
kp.save_agent_config("Ash", cfg)

present, ex = T.load_tools([], context={"agent_name": "Ash"}, person_present=True)
pnames = [s["function"]["name"] for s in present]
check("in the person's own chat, the kin can write to another kin",
      pnames == ["message_kin"])
pschema = present[0]["function"]["parameters"]["properties"]
check("...and whether it counts is the framework's, never the model's",
      "kin_mail_prompted" not in pschema)
# Counted by what the tool actually adds, not by whether today happens to be free:
# day2 above IS 23 Nov 2026, and on that real date the earlier test message filled
# today's one (found 23 Nov 2026, when the suite ran on that day).
def _ash_sent_today():
    st = kin_mail._load_state().get(kin_mail._pair_key("Ash", "Opal")) or {}
    if st.get("day") != kin_mail._today(datetime.datetime.now()):
        return 0
    return sum(1 for s in st.get("sent_by") or [] if s.casefold() == "ash")
_before_tool = _ash_sent_today()
ex["message_kin"]({"to": "Opal", "message": "From the desktop."})
check("...and what it writes there isn't counted",
      _ash_sent_today() == _before_tool)
fence_note = kin_mail.send_fences("Ash", "```to:Opal\nhi from chat\n```", day4,
                                  prompted=True)
check("a to: block in the person's chat is sent, uncounted",
      ": sent;" in fence_note and kin_mail.can_send("Ash", "Opal", day4)[0])

kin_mail.send("Opal", "Ash", "Are you there?", day4)
chat_block = kin_mail.waiting_block("Ash", in_chat=True)
check("handed over in a chat with the person",
      "Are you there?" in chat_block)
check("...where no answer is ever sent by guessing",
      kin_mail.send_fences("Ash", "Opal, yes I'm here.", day4, prompted=True) == "")
for name, f in (("the desktop", "frame/chat_send_mixin.py"),
                ("Telegram", "telegram_bot.py"), ("Discord", "discord_bot.py")):
    code = (ROOT / f).read_text(encoding="utf-8")
    check(f"{name} offers it when it's the person, and hands messages over",
          "person_present" in code and "kin_mail" in code
          and ("take_waiting" in code or "waiting_block" in code
               or "reply_actions.before_message" in code))
tg = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("Telegram only for the person's own account, never a group",
      'not str(chat_id).startswith("-")' in tg and "self._user_is_me(user_id)" in tg)

# --- the notes are editable, like every other note ----------------------------
for slug in ("kin_message_waiting", "kin_message_waiting_limit",
             "kin_message_receipt", "kin_message_answer_sent",
             "kin_message_held", "kin_message_house_hint"):
    check(f"{slug} is an editable prompt", slug in kp.APP_PROMPT_REGISTRY)
own = kp.agent_dir("Opal") / "prompts"
own.mkdir(parents=True, exist_ok=True)
(own / "kin_message_waiting.md").write_text(
    "[from {from} at {time}] {message} -- answer if you like.", encoding="utf-8")
kin_mail.take_waiting("Opal")  # anything still queued from the checks above
kin_mail.send("Kimi", "Opal", "Testing the wording.", datetime.datetime(2026, 11, 26, 9))
got = kin_mail.waiting_block("Opal")
check("a kin's own wording of the note is what it's given",
      got == "[from Kimi at 2026-11-26 09:00] Testing the wording. -- answer if you like.")
(own / "kin_message_waiting.md").unlink()
note, n = kin_mail.handle_reply("Kimi", "```to:Opal\nhi\n```", datetime.datetime(2026, 11, 27, 9))
check("whether a message went is counted, never read from the wording", n == 1)

src = (ROOT / "dialogs" / "heartbeat_settings.py").read_text(encoding="utf-8")
check("the heartbeat window has the setting, and saves it",
      "each other &kin a day" in src and '"kin_messages_per_day": kpd' in src)

if _fails:
    print(f"{len(_fails)} FAILED: " + "; ".join(_fails))
    sys.exit(1)
print("test_kin_messages: all checks passed")
