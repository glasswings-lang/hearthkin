# SPDX-License-Identifier: CC0-1.0
"""Both ways a wake-up runs on its own finish its reply the same way.

A wake-up runs on its own when Hearthkin is closed, or when it's open but set
not to send wake-ups into the chat. The second used to skip every finishing
step, so its reply went to the journal and Telegram with stray thinking text,
a file the kin wrote out was never saved, and an empty reply was never
rescued. Since 12 Nov 2026 both go through hearthkin_cron._finish_cron_reply.

This runs a whole wake-up each way, through the real main(), with the model
stubbed, and checks what reached the journal and the Telegram post. Nothing
leaves the machine.

Run: python tests/test_cron_finish.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cronfinish-")
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
(k / "tools.json").write_text(json.dumps({"enabled": ["write_file", "read_file"]}),
                              encoding="utf-8")
(k / "soul.md").write_text("A small kin.", encoding="utf-8")


class R:
    def __init__(self, content, added=None):
        self.content = content
        self.thinking = ""
        self.stopped = False
        self.messages_added = added or []
        self.tool_calls_made = []


def run(mode, reply_obj, show_thinking=False):
    """One whole wake-up. mode: 'closed' or 'open-not-in-chat'."""
    (k / "config.json").write_text(json.dumps({
        "model": "stub-model",
        "cron_inject_when_running": mode == "closed",
        "show_thinking": show_thinking,
        "cron_entries": [{"prompt": "Tend your notes.", "enabled": True,
                          "time": "08:00"}]}), encoding="utf-8")
    posted = []
    real = (llm_backend.run_tool_loop, hearthkin_cron._post_cron_reply_to_telegram,
            cron_helpers.lock_indicates_running)
    llm_backend.run_tool_loop = lambda *a, **kw: reply_obj
    hearthkin_cron._post_cron_reply_to_telegram = (
        lambda kin, cfg, reply, *a, **kw: posted.append(reply))
    cron_helpers.lock_indicates_running = lambda: mode != "closed"
    jp = cron_helpers.kin_journal_path(KIN)
    before = jp.read_text(encoding="utf-8") if jp.exists() else ""
    try:
        rc = hearthkin_cron.main(["--kin", KIN, "--entry-index", "0",
                                  "--run-now"])
    finally:
        (llm_backend.run_tool_loop, hearthkin_cron._post_cron_reply_to_telegram,
         cron_helpers.lock_indicates_running) = real
    journal = (jp.read_text(encoding="utf-8") if jp.exists() else "")[len(before):]
    return rc, posted, journal


for mode in ("closed", "open-not-in-chat"):
    target = k / f"fenced-{mode}.md"
    leaky = R("<thinking>I should be careful here.</thinking>All tended.\n"
              f"```write:{target.name}\nsaved by the fence\n```\n")
    rc, posted, journal = run(mode, leaky)
    check(f"{mode}: the wake-up finished", rc == 0)
    check(f"{mode}: stray thinking text never reaches Telegram",
          posted and "careful" not in posted[0] and "All tended." in posted[0])
    check(f"{mode}: ...or the journal",
          "careful" not in journal and "All tended." in journal)
    check(f"{mode}: a file written out in the reply is saved",
          target.exists()
          and target.read_text(encoding="utf-8").strip() == "saved by the fence")

    added = [{"role": "assistant", "content": "Done: I tidied February.",
              "tool_calls": [{"function": {"name": "read_file",
                                           "arguments": "{}"}}]},
             {"role": "tool", "content": "ok"}]
    rc, posted, journal = run(mode, R("", added))
    check(f"{mode}: an empty reply is rescued from what the kin said "
          f"alongside its tool call",
          posted and posted[0].startswith("Done: I tidied February.")
          and "[no reply produced]" not in journal)

# "Show reasoning" on: the reasoning reaches the journal, not the post.
for mode in ("closed", "open-not-in-chat"):
    r = R("<thinking>Beans first, then the onions.</thinking>Planted.")
    rc, posted, journal = run(mode, r, show_thinking=True)
    check(f"{mode}: with Show reasoning on, the reasoning is in the journal",
          "Beans first, then the onions." in journal)
    check(f"{mode}: ...and never in what's posted onward",
          posted and "onions" not in posted[0])
    rc, posted, journal = run(mode, R("<thinking>Quietly.</thinking>Done."))
    check(f"{mode}: with it off, the reasoning goes nowhere",
          "Quietly." not in journal and posted and "Quietly." not in posted[0])

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_cron_finish: all checks passed")
