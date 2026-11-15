# SPDX-License-Identifier: CC0-1.0
"""A wake-up can run on its own model (15 Nov 2026).

A kin's chat model looped on tool calls at its wake-ups, and its person
wanted wake-ups handed to another model while chat stays where it is. A cron
entry's "model" names it; blank means the kin's own. Pinned, through the real
main() with the model stubbed (nothing leaves the machine):

  * a wake-up with its own model calls that model, both when Hearthkin is
    closed and when it's open but not sending wake-ups into the chat;
  * a wake-up without one calls the kin's own model (the check can fail);
  * the kin's saved model is not changed by any of it;
  * the open app never pours an own-model wake-up into the live chat;
  * the wake-up window keeps, clears and saves the choice (isolated desktop).

Run: python tests/test_cron_entry_model.py   (the window part needs
     tests/_gui_runner.py; run_all does that.)
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Its own data folder, never the suite's shared one: main() records
# overnight turns there, which other tests read.
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronmodel-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cron_helpers  # noqa: E402
import hearthkin_cron  # noqa: E402
import llm_backend  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Wren"
k = kin_dir(KIN)
k.mkdir(parents=True, exist_ok=True)
(k / "tools.json").write_text(json.dumps({"enabled": ["read_file"]}), encoding="utf-8")
(k / "soul.md").write_text("A small kin.", encoding="utf-8")


class R:
    content = "Done."
    thinking = ""
    stopped = False
    messages_added = []
    tool_calls_made = []
    tool_calls = []


def run(mode, entry_model):
    entry = {"prompt": "Tend your notes.", "enabled": True, "time": "08:00"}
    if entry_model is not None:
        entry["model"] = entry_model
    (k / "config.json").write_text(json.dumps({
        "model": "chat-model",
        "cron_inject_when_running": mode == "closed",
        "cron_entries": [entry]}), encoding="utf-8")
    used = []

    def loop(model, *a, **kw):
        used.append(model)
        return R()

    def chat(model, *a, **kw):
        used.append(model)
        return R()

    real = (llm_backend.run_tool_loop, llm_backend.chat,
            hearthkin_cron._post_cron_reply_to_telegram,
            cron_helpers.lock_indicates_running)
    llm_backend.run_tool_loop = loop
    llm_backend.chat = chat
    hearthkin_cron._post_cron_reply_to_telegram = lambda *a, **kw: None
    cron_helpers.lock_indicates_running = lambda: mode != "closed"
    try:
        hearthkin_cron.main(["--kin", KIN, "--entry-index", "0", "--run-now"])
    finally:
        (llm_backend.run_tool_loop, llm_backend.chat,
         hearthkin_cron._post_cron_reply_to_telegram,
         cron_helpers.lock_indicates_running) = real
    saved = json.loads((k / "config.json").read_text(encoding="utf-8"))
    return used, saved.get("model")


for mode in ("closed", "open-not-in-chat"):
    used, saved = run(mode, "steady-model")
    check(f"{mode}: a wake-up with its own model calls it", bool(used) and set(used) == {"steady-model"})
    check(f"{mode}: ...and the kin's saved model is unchanged", saved == "chat-model")
    used, _ = run(mode, None)
    check(f"{mode}: the check can fail: without one, the kin's own model is called",
          bool(used) and set(used) == {"chat-model"})
    used, _ = run(mode, "   ")
    check(f"{mode}: a blank model means the kin's own", bool(used) and set(used) == {"chat-model"})

check("cfg_for_entry never changes the config it's given",
      (lambda c: (hearthkin_cron.cfg_for_entry(c, {"model": "x"}), c)[1])({"model": "y"}) == {"model": "y"})

# The open app: an own-model wake-up is never handed to the live chat.
tick_src = (ROOT / "frame" / "cron_exec_mixin.py").read_text(encoding="utf-8")
i = tick_src.find("and not own_history")
check("the live-chat branch also requires no model of its own",
      i != -1 and "and not own_model" in tick_src[i:i + 300])
check("the open app's worker runs the wake-up with its own model",
      "hearthkin_cron.cfg_for_entry(cfg, entry)" in tick_src)

# The window, only on an isolated desktop.
if os.environ.get("HEARTHKIN_GUI_TESTS", "").strip() in ("1", "true", "yes"):
    import wx
    from kin_persistence import save_agent_config
    app = wx.App()
    save_agent_config(KIN, {"model": "chat-model"})
    from dialogs.cron_entry import CronEntryDialog
    d = CronEntryDialog(None, KIN, {"prompt": "p", "times": ["08:00"], "enabled": True,
                                    "model": "steady-model"})
    check("the window shows this wake-up's own model",
          "steady-model" in d.model_display.GetValue() and "chat-model" in d.model_display.GetValue())
    check("...and it's kept on saving", d.get_entry().get("model") == "steady-model")
    d.set_wake_model("chat-model")
    check("choosing the kin's own model clears it", "model" not in d.get_entry())
    check("...the window says so", d.model_display.GetValue().startswith("The kin's own model: chat-model"))
    check("...and 'use the kin's own' is greyed out", not d.model_own_btn.IsEnabled())
    d.set_wake_model("other-model")
    check("choosing another model saves it", d.get_entry().get("model") == "other-model")
    d._on_use_own_model(None)
    check("'use the kin's own model' clears it", "model" not in d.get_entry())
    d.Destroy()
    app.Destroy()
else:
    print("(window checks run on the isolated desktop: python tests/_gui_runner.py "
          "tests/test_cron_entry_model.py)")

print()
if _fails:
    print(f"test_cron_entry_model: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_cron_entry_model: all checks passed")
