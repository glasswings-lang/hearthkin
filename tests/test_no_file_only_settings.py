# SPDX-License-Identifier: CC0-1.0
"""Every kin setting can be changed from a window, never only by editing
config.json.

The operator is blind and doesn't read code; a setting that exists only in
the JSON file is a setting they effectively don't have. On 12 Nov 2026 a
sweep found three (the staging read size, and Discord's server and channel
lists); they got controls, and this keeps it that way.

How it checks: every key in the kin's default settings (and its Telegram and
Discord sections) must be named somewhere in the settings windows' code, and
every per-person setting must be in the shared person list, whose window test
proves each one is built. A key named in a window isn't proof of a working
control, so the window tests do that part; this catches the setting nobody
built a control for at all.

If a setting genuinely shouldn't have a control, list it in INTERNAL below
with the reason.

Run: python tests/test_no_file_only_settings.py
"""

import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kin_persistence import (DEFAULT_AGENT_CONFIG, DEFAULT_TELEGRAM_CONFIG,  # noqa: E402
                             DEFAULT_DISCORD_CONFIG)
from tools._person_settings import PERSON_SETTINGS, store_key  # noqa: E402

# key -> why it has no control. Keep this short and honest.
INTERNAL = {}

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


ui = ""
for f in glob.glob(str(ROOT / "dialogs" / "*.py")) + glob.glob(str(ROOT / "frame" / "*.py")):
    ui += Path(f).read_text(encoding="utf-8")
person = {store_key(s, sf) for s in PERSON_SETTINGS for sf in ("telegram", "discord")}

for label, d in (("kin", DEFAULT_AGENT_CONFIG),
                 ("Telegram", DEFAULT_TELEGRAM_CONFIG),
                 ("Discord", DEFAULT_DISCORD_CONFIG)):
    missing = [k for k in d
               if k not in person and k not in INTERNAL
               and f'"{k}"' not in ui and f"'{k}'" not in ui]
    check(f"every {label} setting has a control somewhere"
          + (f" (file-only: {missing})" if missing else ""), not missing)

# Control: a made-up key must be caught, or the check proves nothing.
check("control: a setting no window mentions is caught",
      '"zz_never_a_real_setting"' not in ui)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_no_file_only_settings: all checks passed")
