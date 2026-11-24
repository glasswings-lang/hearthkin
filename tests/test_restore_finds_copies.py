# SPDX-License-Identifier: CC0-1.0
"""Bringing a conversation back is findable and safe (24 Nov 2026).

The person, after a kin's eight months came apart into three pieces: "If
this happens again, is there a way for me, through Hearthkin, to patch that
up?" There was a door (File, Restore a kin's history) but nothing said so,
it meant browsing hidden folders for one of five kinds of copy, and it left
the distiller's bookmark at the same row NUMBER, so months of restored
history were re-distilled, the flattening that started it all. Pinned:

  * find_saved_copies lists every copy Hearthkin keeps, from all five
    places, newest first, with its message count, and not another kin's;
  * restoring old history in front keeps the bookmark past what was already
    distilled, and never sends the restored part to the distiller;
  * what wasn't distilled yet still is;
  * the clear and new-conversation messages say how to bring it back.

Run: python tests/test_restore_finds_copies.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="restorecopies-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
from importers._canonical import restore_history  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def talk(n, month, tag):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{tag} {i}", "ts": f"2026-{month:02d}-{i % 28 + 1:02d}T10:{i % 60:02d}:00"}
            for i in range(n)]


KIN = "Sorrel"
d = kin_dir(KIN)
(d / "backups" / "cleared").mkdir(parents=True, exist_ok=True)
(d / "telegram_archive").mkdir(parents=True, exist_ok=True)
old = talk(40, 3, "march")
with open(d / "backups" / "cleared" / "conversation-old.jsonl", "w", encoding="utf-8") as fh:
    for r in old:
        fh.write(json.dumps(r) + "\n")
(d / "telegram_archive" / "7_x.json").write_text(json.dumps({"messages": talk(5, 4, "tg")}), encoding="utf-8")
kp.CONVOS_DIR.mkdir(parents=True, exist_ok=True)
(kp.CONVOS_DIR / f"{KIN}_telegram_new_2026-11-24_00-44-28.json").write_text(
    json.dumps({"agent_name": KIN, "messages": talk(7, 5, "snap")}), encoding="utf-8")
(kp.CONVOS_DIR / f"{KIN}_copy_telegram_new_2026-05-19.json").write_text(
    json.dumps({"agent_name": f"{KIN}_copy", "messages": talk(3, 5, "other")}), encoding="utf-8")

copies = kp.find_saved_copies(KIN)
names = [c["path"].name for c in copies]
check("every kind of copy is found", "conversation-old.jsonl" in names and "7_x.json" in names
      and f"{KIN}_telegram_new_2026-11-24_00-44-28.json" in names)
check("...with its message count",
      next(c for c in copies if c["path"].name == "conversation-old.jsonl")["count"] == 40)
check("...and not another kin's", not any("copy" in n for n in names))

# Live conversation: 20 November rows, the first 12 already distilled.
now = talk(20, 11, "nov")
kp.save_agent_conversation(KIN, now)
cfg = kp.load_agent_config(KIN) or {}
cfg["distill_offsets"] = {"desktop": 12}
kp.save_agent_config(KIN, cfg)

res = restore_history(KIN, old, mode="merge")
rows = kp.load_agent_conversation(KIN)
off = (kp.load_agent_config(KIN).get("distill_offsets") or {}).get("desktop")
check("restoring puts the old history back, in front", len(rows) == 60 and rows[0]["content"] == "march 0")
check("the bookmark moves past the restored part and what was distilled",
      off == 52 and res.get("distill_bookmark") == 52)
check("...so the distiller reads only what it hadn't yet",
      [r["content"] for r in rows[off:]] == [f"nov {i}" for i in range(12, 20)])

# Unstamped turns at the start of a restored copy go with the turn they led
# into, not to the front of the whole conversation (found merging a real one).
from importers._canonical import restore_rows  # noqa: E402
lead = [{"role": "assistant", "content": "reading my notes"},
        {"role": "tool", "content": "the notes"},
        {"role": "user", "content": "may 20", "ts": "2026-05-20T10:00:00"}]
merged, _ = restore_rows(talk(4, 3, "mar") + talk(4, 6, "jun"), lead, mode="merge")
pos = [m["content"] for m in merged].index("reading my notes")
check("an undated turn at the start of a copy lands by the turn it led into",
      pos > 0 and "may 20" in [m["content"] for m in merged[pos + 1:pos + 3]]
      and merged[pos - 1]["content"].startswith("mar"))

src = ((ROOT / "frame" / "chat_stream_mixin.py").read_text(encoding="utf-8")
       + (ROOT / "frame" / "file_menu_mixin.py").read_text(encoding="utf-8")
       + (ROOT / "telegram_bot.py").read_text(encoding="utf-8"))
check("clear and new conversation say how to bring it back",
      src.count("File, Restore a") >= 4)

print()
if _fails:
    print(f"test_restore_finds_copies: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_restore_finds_copies: all checks passed")
