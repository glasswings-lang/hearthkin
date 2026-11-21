# SPDX-License-Identifier: CC0-1.0
"""A file path named in a Telegram message is read only for someone allowed to.

Found in the 20 Nov 2026 audit: in a Telegram direct message, any allowed
person could name a path on the operator's machine and have that file read in
front of the kin, whatever tool access they'd been given. Groups checked for
read_file, but not the "keep file tools inside the kin's folder" setting.

Now one rule for both: nothing is read unless the person's own tools include
read_file, and when their file tools are kept inside the kin's folder, only
paths inside it.

Run: python tests/test_named_paths_gated.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="namedpaths-", dir=(os.environ.get("HEARTHKIN_HOME") or None))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import DEFAULT_AGENT_CONFIG, save_agent_config  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402
import telegram_bot as _tb  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Bracken"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
inside = kin_dir(KIN) / "notes.md"
inside.write_text("kin notes", encoding="utf-8")
outside = Path(tempfile.mkdtemp()) / "private.txt"
outside.write_text("secret", encoding="utf-8")
cfg = dict(DEFAULT_AGENT_CONFIG)
cfg["model"] = "stub-model"
save_agent_config(KIN, cfg)

TG = {"user_file_reach": {}}


def bot():
    b = _tb.TelegramBot.__new__(_tb.TelegramBot)
    b.__init__(KIN, get_config=lambda: TG, get_soul=lambda: "",
               get_memory=lambda: "",
               get_model_options=lambda: ("stub-model", {}),
               on_status=lambda *a: None)
    return b


paths = [str(inside), str(outside)]
b = bot()
check("no read_file in their tools: nothing is read",
      b._named_paths_they_may_read(paths, 7, ["web_search"]) == [])
check("read_file, file tools kept inside the kin's folder (the default): "
      "only the path inside it",
      b._named_paths_they_may_read(paths, 7, ["read_file"]) == [str(inside)])
TG["user_file_reach"] = {"7": "outside"}
check("read_file and allowed outside the folder: both",
      b._named_paths_they_may_read(paths, 7, ["read_file"]) == paths)

src = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
check("the direct-message path and the group path both use the one rule",
      src.count("self._named_paths_they_may_read(") == 2)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_named_paths_gated: all checks passed")
