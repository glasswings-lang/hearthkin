# SPDX-License-Identifier: CC0-1.0
"""A kin's own files beside its soul (18 Nov 2026, from Kimi's own list).

  identity.md  rides every turn, right after the soul; a note asks the kin to
               trim past a short page, and nothing is cut
  now.md       NOT sent every turn. Each compaction's note is saved there (the
               one before kept as now.md.previous), and the next compaction
               request shows it back so the kin updates rather than restarts.
               All three surfaces that compact (desktop, Telegram, Discord)
               go through the same two helpers.
  curious.md   never sent; the heartbeat and shared instructions point to it
  the inbox    its real path is filled in wherever {kin_inbox} appears
"""

import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="ownfiles-", dir=os.environ.get("HEARTHKIN_HOME"))

import kin_persistence as K  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "ownkin"
K.create_agent(KIN)
kdir = K.agent_dir(KIN)
TOOLS = ["read_file", "write_file", "edit_file", "memory_search"]


def prompt(tools=TOOLS):
    return K.build_system_prompt("SOUL TEXT", "MEMORY TEXT",
                                 enabled_tools=tools, kin_name=KIN)


# --- identity
check("no identity.md, no identity section", "Who you're becoming" not in prompt())
(kdir / "identity.md").write_text("I am learning to lead.\n", encoding="utf-8")
p = prompt()
check("identity.md is in the prompt", "I am learning to lead." in p)
check("...after the soul and before the memory",
      p.index("SOUL TEXT") < p.index("I am learning to lead.") < p.index("MEMORY TEXT"))
check("...with no trim note while it's short", "Trim it when you can" not in p)
(kdir / "identity.md").write_text("x " * 2000, encoding="utf-8")
p = prompt()
check("a long identity.md gets a trim note", "Trim it when you can" in p)
check("...and is not cut", ("x " * 2000).strip() in p)

# --- now.md stays out of the every-turn prompt
(kdir / "now.md").write_text("We were mid-way through chapter four.\n",
                             encoding="utf-8")
check("now.md is not sent every turn",
      "chapter four" not in prompt())

# --- compaction request shows the last now.md
ask = K.compact_request_text(KIN, "Sam")
check("the request names the person", "Sam" in ask)
check("the request shows the last now.md", "chapter four" in ask)
(kdir / "now.md").unlink()
check("no now.md, nothing extra",
      "now.md" not in K.compact_request_text(KIN, "Sam"))

# --- saving the note
check("a compaction note becomes now.md",
      K.save_now_note(KIN, "Fresh note.") and
      (kdir / "now.md").read_text(encoding="utf-8").strip() == "Fresh note.")
K.save_now_note(KIN, "Newer note.")
check("the one before is kept",
      (kdir / "now.md.previous").read_text(encoding="utf-8").strip()
      == "Fresh note.")
check("an empty note saves nothing", not K.save_now_note(KIN, "  "))

# --- every compacting surface goes through the helpers
for f in ("frame/file_menu_mixin.py", "telegram_bot.py", "discord_bot.py"):
    src = open(os.path.join(HERE, f), encoding="utf-8").read()
    check(f"{f} asks with compact_request_text", "compact_request_text(" in src)
    check(f"{f} saves with save_now_note", "save_now_note(" in src)
    check(f"{f} no longer builds the request by hand",
          'load_app_prompt(\n            "compact_request"' not in src
          and 'load_app_prompt("compact_request"' not in src)

# --- the shared instructions point to the files and the inbox
p = prompt()
check("the own-files line is there for a kin with file tools",
      "`curious.md`" in p)
check("the inbox's real path is filled in",
      str(kdir.parent / "inbox.md") in p and "{kin_inbox}" not in p)
check("a kin with no tools isn't told about files it can't write",
      "`curious.md`" not in prompt(tools=[]))

# --- heartbeat
hb = K.DEFAULT_HEARTBEAT_FRAME
check("the heartbeat offers own work, reaching out and rest",
      "something of your own" in hb and "reach_out" in hb and "Rest:" in hb)
check("the heartbeat points to curious.md and the inbox",
      "curious.md" in hb and "{kin_inbox}" in hb)
check("the heartbeat no longer says 'operator'", "operator" not in hb)
# 24 Nov 2026 (accuracy audit): each part that needs a tool is shown only to
# a kin that has it.
_bare = K.apply_tool_fences(hb, ["reach_out"])
check("a kin with no file tools isn't told about curious.md or the inbox",
      "curious.md" not in _bare and "{kin_inbox}" not in _bare)
check("...nor about message_kin or the to: block when it can't use them",
      "message_kin" not in _bare and "```to:" not in _bare)
_full = K.apply_tool_fences(hb, ["reach_out", "read_file", "message_kin",
                                 "kin_mail"])
check("a kin with them is told all of it",
      "curious.md" in _full and "message_kin" in _full and "```to:" in _full)
check("the heartbeat applies the fences with the kin's real tools",
      "apply_tool_fences(framed" in open(
          os.path.join(HERE, "hearthkin_cron.py"), encoding="utf-8").read())
check("{kin_inbox} is declared for the editing screen",
      "{kin_inbox}" in K.APP_PROMPT_REGISTRY["heartbeat_frame"]["placeholders"])
cron = open(os.path.join(HERE, "hearthkin_cron.py"), encoding="utf-8").read()
check("the heartbeat fills in the inbox path",
      'framed.replace("{kin_inbox}"' in cron)

print()
if _fails:
    print(f"{len(_fails)} FAILED")
    sys.exit(1)
print("all passed")
