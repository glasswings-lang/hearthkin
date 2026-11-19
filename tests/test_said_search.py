# SPDX-License-Identifier: CC0-1.0
"""memory_search finds what was actually said, within what this person may
reach (18 Nov 2026).

What this file pins:
  * the kin's own person (and the desktop) can find moments from every
    conversation, each with where, when, who and their words, by line number
    where the conversation can be opened by line
  * people are named as the operator named them, not by platform handle
  * tool output and Hearthkin's own bracketed notes are never searched
  * someone else reaches only their own conversation and the kin's own notes:
    not the operator's words, not other people's chats, not staging notes
  * ...unless the operator has spoken in that conversation, or ticked the
    person's "search your own conversations" box, or the person is the
    operator ("This is me")
  * a "*" (anyone) entry can never open it
  * an unknown scope reaches nothing, never everything
  * the scope is set by the app; the model can't see or override it
"""

import json
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="saidsearch-", dir=os.environ.get("HEARTHKIN_HOME"))

import kin_persistence as K  # noqa: E402
from tools.memory_search import memory_search  # noqa: E402
from tools._person_settings import resolve_said_scope  # noqa: E402
import tools as kin_tools  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "saidkin"
K.create_agent(KIN)
kd = K.agent_dir(KIN)
ME, STRANGER, GROUP_ME, GROUP_NOT = "111", "222", "-333", "-444"

cfg = K.load_agent_config(KIN)
cfg["telegram"] = dict(cfg.get("telegram") or {},
                       user_labels={ME: "Morgan", STRANGER: "Sam"},
                       user_is_operator={ME: True, "*": True})
K.save_agent_config(KIN, cfg)
TG = cfg["telegram"]


def row(role, content, ts="2026-11-17T10:00:00", **kw):
    return dict({"role": role, "content": content, "ts": ts}, **kw)


convo = [
    row("user", "The lighthouse keeper story is mine, keep it private.",
        sender_id=int(ME), sender_name="@someHandle", source=f"telegram:{ME}"),
    row("assistant", "I'll hold the lighthouse keeper story close."),
    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "read_file", "arguments": {}}}]},
    {"role": "tool", "content": "lighthouse keeper appears in this tool output"},
    row("user", "[hearthkin: lighthouse keeper harness note]"),
]
with open(kd / "conversation.jsonl", "w", encoding="utf-8") as f:
    for m in convo:
        f.write(json.dumps(m) + "\n")
(kd / "telegram_history.json").write_text(json.dumps({"histories": {
    STRANGER: [row("user", "Tell me about the marmalade jar.",
                   sender_id=int(STRANGER), sender_name="Sam"),
               row("assistant", "The marmalade jar sits by the window.")],
    f"group:{GROUP_ME}": [row("user", "Hello group, it's me.",
                              sender_id=int(ME)),
                          row("user", "Hi, marmalade fans",
                              sender_id=int(STRANGER))],
    f"group:{GROUP_NOT}": [row("user", "marmalade again",
                               sender_id=int(STRANGER))],
}}), encoding="utf-8")
(kd / "staging").mkdir(exist_ok=True)
(kd / "staging" / "desktop.md").write_text(
    "Morgan told me the lighthouse keeper story.\n", encoding="utf-8")
(kd / "memory").mkdir(exist_ok=True)
(kd / "memory" / "garden.md").write_text("The marigolds came up.\n",
                                         encoding="utf-8")

# --- the kin's own person: everything
r = memory_search("lighthouse keeper", agent_name=KIN)
said = r.split("From what was said:")[-1] if "From what was said:" in r else ""
check("the person's words are found", "keep it private" in said)
check("...named as the operator named them",
      "Morgan said" in said and "@someHandle" not in said)
check("...with a line number to open", "conversation.jsonl line 1" in said)
check("...and the date", "17 Nov 2026" in said)
check("the kin's own reply is 'you said'", "you said" in said)
check("tool output is never searched", "tool output" not in said)
check("harness notes are never searched", "harness note" not in said)
r2 = memory_search("marmalade", agent_name=KIN)
check("other people's chats are found for the kin's own person",
      "Sam said" in r2 and "Telegram chat" in r2)

# --- someone else: their own conversation only
sam = resolve_said_scope(TG, "telegram", STRANGER, f"telegram:{STRANGER}")
check("a stranger gets a scope, not everything", isinstance(sam, dict))
r = memory_search("lighthouse keeper", agent_name=KIN, said_scope=sam)
check("a stranger can't find the operator's words", "keep it private" not in r)
check("...nor the staging notes that copy them",
      "staging" not in r and "lighthouse keeper story" not in r)
r = memory_search("marmalade jar", agent_name=KIN, said_scope=sam)
check("a stranger finds their own conversation", "marmalade jar" in r)
r = memory_search("marigolds", agent_name=KIN, said_scope=sam)
check("...and the kin's own notes", "marigolds" in r)

grp = resolve_said_scope(TG, "telegram", STRANGER,
                         f"telegram:group:{GROUP_NOT}")
r = memory_search("lighthouse keeper", agent_name=KIN, said_scope=grp)
check("a group the operator isn't in stays closed", "keep it private" not in r)
grp_me = resolve_said_scope(TG, "telegram", STRANGER,
                            f"telegram:group:{GROUP_ME}")
r = memory_search("lighthouse keeper", agent_name=KIN, said_scope=grp_me)
check("a group the operator has spoken in is open",
      "keep it private" in r)

# --- the operator's switches
check("'This is me' reaches everything",
      resolve_said_scope(TG, "telegram", ME, f"telegram:{ME}") is None)
tg2 = dict(TG, user_reach_said={STRANGER: True})
check("the per-person switch opens it",
      resolve_said_scope(tg2, "telegram", STRANGER,
                         f"telegram:{STRANGER}") is None)
tg3 = dict(TG, user_reach_said={"*": True})
check("a '*' entry can't open it",
      isinstance(resolve_said_scope(tg3, "telegram", STRANGER,
                                    f"telegram:{STRANGER}"), dict))
check("'*' marked as me doesn't count either",
      isinstance(resolve_said_scope(TG, "telegram", "999",
                                    "telegram:999"), dict))
dc = {"user_is_operator": {ME: True}}
check("Discord follows the same rule",
      isinstance(resolve_said_scope(dc, "discord", STRANGER, "discord:55"),
                 dict))

# --- unknown scope reaches nothing
r = memory_search("lighthouse keeper", agent_name=KIN, said_scope="nonsense")
check("an unknown scope reaches no conversation", "keep it private" not in r)

# --- set by the app, not the model
schemas, ex = kin_tools.load_tools(
    ["memory_search"], context={"agent_name": KIN, "said_scope": sam})
props = schemas[0]["function"]["parameters"]["properties"]
check("the model never sees said_scope", "said_scope" not in props)
r = ex["memory_search"]({"query": "lighthouse keeper", "said_scope": None})
check("a model-sent said_scope can't widen it", "keep it private" not in r)

# --- the person window has the switch
pw = open(os.path.join(HERE, "dialogs", "person_window.py"),
          encoding="utf-8").read()
check("the person window shows the switch",
      "def _build_reach_said" in pw and '"reach_said"' in pw)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
