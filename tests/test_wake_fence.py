# SPDX-License-Identifier: CC0-1.0
"""Wake-ups and heartbeats keep their file tools inside the kin's folder
(23 Nov 2026).

A friend's review found it: Telegram and Discord fence a kin's file tools to its
own folder, but a wake-up never did, and a wake-up can carry fetch_url beside
them. Nobody watches a wake-up, so a web page could steer a kin into reading any
file on the computer and sending it out. The same review found heartbeats saying
"work on something of your own, your curious.md" with no way to open it.
Pinned:

  * a wake-up refuses a path outside the kin's folder, and reads one inside;
  * the kin inbox, outside every kin's folder, stays reachable, both by its
    full path and as "../inbox.md" (the heartbeat and the base prompt name it);
  * Telegram's box (direct paths in chat) does NOT open wake-ups; their own
    box "Let file tools on wake-ups and heartbeats reach outside the kin
    folder" does, so the check can fail;
  * a file written out as a block in a wake-up's reply follows the same fence;
  * a heartbeat gets the kin's own file tools, only those it has in chat, fenced;
  * a kin that writes in its own files at a heartbeat isn't nudged to send.

Run: python tests/test_wake_fence.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="wakefence-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hearthkin_cron as hc  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from kin_persistence import (DEFAULT_AGENT_CONFIG, save_agent_config,  # noqa: E402
                             save_kin_tools)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
home = kin_dir(KIN)
home.mkdir(parents=True, exist_ok=True)
(home / "curious.md").write_text("moths and lamps\n", encoding="utf-8")
inbox = home.parent / "inbox.md"
inbox.write_text("a note from another kin\n", encoding="utf-8")
outside = Path(tempfile.mkdtemp(prefix="elsewhere-")) / "secret.txt"
outside.write_text("not for the kin\n", encoding="utf-8")


def setup(unconfined, remote=True, hb=None):
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["tool_trust"] = "full"
    cfg["model"] = "test-model"
    # Telegram's box is ticked throughout: direct paths in chat must not
    # open wake-ups. Only the wake-up box decides.
    cfg["remote_unconfined_files"] = remote
    cfg["wakeup_unconfined_files"] = unconfined
    cfg["heartbeat_unconfined_files"] = hb if hb is not None else unconfined
    save_agent_config(KIN, cfg)
    return cfg


# --- wake-ups ---
save_kin_tools(KIN, ["read_file", "write_file", "edit_file", "list_directory"])
setup(False)
_, ex, _ = hc._load_cron_tools(KIN)
r = ex["read_file"](dict(path=str(outside)))
check("a wake-up refuses a file outside the kin's folder", "not for the kin" not in r)
check("...and says why, naming wake-ups", "wake-ups" in r)
check("a wake-up reads its own curious.md", "moths and lamps" in ex["read_file"](dict(path="curious.md")))
check("a wake-up reads the kin inbox by its full path",
      "a note from another kin" in ex["read_file"](dict(path=str(inbox))))
check("...and as ../inbox.md", "a note from another kin" in ex["read_file"](dict(path="../inbox.md")))
ex["write_file"](dict(path=str(inbox), content="a note from another kin\nhello back\n"))
check("a wake-up can write in the kin inbox", "hello back" in inbox.read_text(encoding="utf-8"))
check("a wake-up still can't climb out another way",
      "not for the kin" not in ex["read_file"](dict(path="../../" + outside.name)))

setup(False, remote=False)
_, ex, _ = hc._load_cron_tools(KIN)
check("with both boxes off, a wake-up is fenced too",
      "not for the kin" not in ex["read_file"](dict(path=str(outside))))
setup(True)
_, ex, _ = hc._load_cron_tools(KIN)
check("the check can fail: with the wake-up box ticked, a wake-up reads outside",
      "not for the kin" in ex["read_file"](dict(path=str(outside))))

# --- a file written out as a block in a wake-up's reply ---
setup(False)
escape = outside.parent / "escaped.md"
hc._maybe_authoring_bridge_cron(
    KIN, "```write:" + str(escape) + "\nout\n```", ["write_file"], None)
check("a block in a wake-up's reply can't save outside the folder", not escape.exists())
hc._maybe_authoring_bridge_cron(
    KIN, "```write:notes.md\ninside\n```", ["write_file"], None)
check("...but saves inside it", (home / "notes.md").exists())
setup(True)
hc._maybe_authoring_bridge_cron(
    KIN, "```write:" + str(escape) + "\nout\n```", ["write_file"], None)
check("the check can fail: with the box ticked, it saves outside", escape.exists())

# --- heartbeats ---
seen = {}


def fake_load_tools(names, context=None, **kw):
    seen["names"] = list(names)
    seen["context"] = dict(context or {})
    raise RuntimeError("stop here")


real_load = hc.kin_tools.load_tools
hc.kin_tools.load_tools = fake_load_tools
try:
    cfg = setup(False)
    hc.run_heartbeat(KIN, cfg)
    check("a heartbeat gets the kin's own file tools",
          all(t in seen["names"] for t in ("read_file", "write_file", "edit_file", "list_directory")))
    check("...still with reach_out", "reach_out" in seen["names"])
    check("...fenced to the kin's folder", seen["context"].get("confine_paths") is True)
    save_kin_tools(KIN, ["read_file"])
    hc.run_heartbeat(KIN, cfg)
    check("only the file tools the kin has in chat",
          "read_file" in seen["names"] and "write_file" not in seen["names"])
    save_kin_tools(KIN, [])
    hc.run_heartbeat(KIN, cfg)
    check("a kin with no file tools gets none at a heartbeat",
          "read_file" not in seen["names"] and "reach_out" in seen["names"])
    cfg = setup(True, hb=False)
    hc.run_heartbeat(KIN, cfg)
    check("the wake-up box doesn't open heartbeats", seen["context"].get("confine_paths") is True)
    cfg = setup(False, hb=True)
    hc.run_heartbeat(KIN, cfg)
    check("the heartbeat box lifts the heartbeat's fence", seen["context"].get("confine_paths") is False)
finally:
    hc.kin_tools.load_tools = real_load

save_kin_tools(KIN, ["read_file", "write_file", "edit_file", "list_directory"])
setup(False, hb=True)
_, _ex, _ = hc._load_cron_tools(KIN)
check("the heartbeat box doesn't open wake-ups",
      "not for the kin" not in _ex["read_file"](dict(path=str(outside))))

# --- a heartbeat spent writing isn't nudged ---
save_kin_tools(KIN, ["read_file", "write_file", "edit_file", "list_directory"])
cfg = setup(False)
calls = []


class Result:
    def __init__(self, added):
        self.content = "I added a line about moths to curious.md."
        self.messages_added = added
        self.stopped = False


def fake_loop(model, messages, **kw):
    calls.append(kw.get("surface"))
    return Result([
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "edit_file", "arguments": {}}}]},
        {"role": "tool", "content": "ok"}])


real_loop = hc._cron_tool_loop
hc._cron_tool_loop = fake_loop
try:
    hc.run_heartbeat(KIN, cfg)
finally:
    hc._cron_tool_loop = real_loop
check("a heartbeat spent writing isn't followed by a send-it nudge",
      calls == ["heartbeat"])
log = (Path(os.environ["HEARTHKIN_HOME"]) / "logs" / "heartbeat.log")
logs = list(Path(os.environ["HEARTHKIN_HOME"]).rglob("heartbeat.log"))
check("...and is logged as work on its own",
      any("worked-on-own" in p.read_text(encoding="utf-8") for p in logs))

print()
if _fails:
    print(f"test_wake_fence: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_wake_fence: all checks passed")
