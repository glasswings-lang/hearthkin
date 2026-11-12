# SPDX-License-Identifier: CC0-1.0
"""One list of per-person settings for Telegram and Discord.

Discord fell behind Telegram because each per-person setting was added to
each surface by hand. Now there's one list (tools/_person_settings.py) that
both person windows, the settings window and the bots go through. Checked
here, without building any windows:

  - saving and loading a person round-trips every setting, on both surfaces;
  - "use the kin's setting" is stored by having no entry, as before;
  - an ID change moves every setting, and removing a person clears all of
    them (Telegram used to leave the tool limit behind);
  - Telegram's existing config keys are unchanged, so existing configs load;
  - the settings window adds, edits and removes people on BOTH surfaces
    through the one shared path, so neither can grow a setting alone.

Run: python tests/test_person_settings.py
"""

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._person_settings import (  # noqa: E402
    PERSON_SETTINGS, SETTING_KEYS, load_person, save_person, remove_person,
    person_value, resolve_file_reach, store_key)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


FULL = {"label": "Alex", "bucket": "full", "share_desktop": True,
        "mirror": True, "webcam": "auto", "tool_cap": 25,
        "exec_approval": "ask", "file_reach": "outside"}
DEFAULTS = {s["key"]: s["default"] for s in PERSON_SETTINGS}

for surface in ("telegram", "discord"):
    sec = save_person({}, surface, None, "42", FULL)
    check(f"{surface}: every setting round-trips",
          load_person(sec, surface, "42") == FULL)
    check(f"{surface}: the person is on the allow list",
          sec["allow_from"] == ["42"])
    sec2 = save_person(sec, surface, "42", "42", DEFAULTS)
    check(f"{surface}: 'use the kin's setting' is stored as no entry",
          "42" not in sec2["user_tool_caps"]
          and "42" not in sec2["user_exec_approval"]
          and "42" not in sec2["user_file_reach"])
    check(f"{surface}: ...and loads back as the default",
          load_person(sec2, surface, "42") == DEFAULTS)
    sec3 = save_person(sec, surface, "42", "77", FULL)
    check(f"{surface}: changing the ID moves every setting",
          load_person(sec3, surface, "77") == FULL
          and all("42" not in (sec3.get(store_key(s, surface)) or {})
                  for s in PERSON_SETTINGS)
          and sec3["allow_from"] == ["77"])
    gone = remove_person(sec, surface, "42")
    check(f"{surface}: removing a person clears every setting",
          all("42" not in (gone.get(store_key(s, surface)) or {})
              for s in PERSON_SETTINGS) and gone["allow_from"] == [])
    check(f"{surface}: junk in a stored setting falls back to its default",
          load_person({"user_webcam_permission": {"9": "sometimes"},
                       "user_tools": {"9": "everything"}},
                      surface, "9")["webcam"] == "ask")

# Telegram's keys are the ones it has always used, so old configs load.
old_tg = {"user_labels": {"5": "Sam"}, "user_tools": {"5": "write"},
          "user_share_desktop": {"5": True},
          "user_mirror_to_telegram": {"5": True},
          "user_webcam_permission": {"5": "deny"}}
v = load_person(old_tg, "telegram", "5")
check("an existing Telegram config loads unchanged",
      (v["label"], v["bucket"], v["share_desktop"], v["mirror"], v["webcam"])
      == ("Sam", "write", True, True, "deny"))

# Discord's old kin-wide sharing box is the fallback for people saved before.
check("Discord: a person with no sharing entry inherits the old kin-wide box",
      load_person({}, "discord", "5", {"share_desktop": True})["share_desktop"])

# Files: the person's choice wins, else the kin-wide box.
KIN_OUT = {"remote_unconfined_files": True}
KIN_IN = {"remote_unconfined_files": False}
sec = save_person({}, "discord", None, "1", dict(DEFAULTS, file_reach="inside"))
check("files: 'keep inside' wins over the kin's 'reach outside'",
      resolve_file_reach(sec, "discord", "1", KIN_OUT) is True)
sec = save_person({}, "telegram", None, "1", dict(DEFAULTS, file_reach="outside"))
check("files: 'allow outside' wins over the kin's 'keep inside'",
      resolve_file_reach(sec, "telegram", "1", KIN_IN) is False)
check("files: no setting follows the kin-wide box",
      resolve_file_reach({}, "discord", "1", KIN_OUT) is False
      and resolve_file_reach({}, "discord", "1", KIN_IN) is True)
check("a '*' entry on Discord covers anyone without their own",
      person_value({"user_webcam_permission": {"*": "deny"}}, "discord",
                   "8", "webcam") == "deny")

# The settings window goes through one shared path for both surfaces.
src = (ROOT / "dialogs" / "edit_kin.py").read_text(encoding="utf-8")
tree = ast.parse(src)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
for name in ("_on_tg_add_user", "_on_tg_edit_user", "_on_dc_add_user",
             "_on_dc_edit_user"):
    body = ast.unparse(fns[name]) if name in fns else ""
    check(f"{name} goes through the shared person window",
          "_person_window_flow" in body)
for name in ("_on_tg_remove_user", "_on_dc_remove_user"):
    body = ast.unparse(fns[name]) if name in fns else ""
    check(f"{name} goes through the shared remove", "remove_person" in body)
check("nothing in the settings window writes a per-person key by hand",
      not any(f'["{store_key(s, sf)}"] =' in src
              for s in PERSON_SETTINGS for sf in ("telegram", "discord")))

# Every setting has a builder in the shared window, so a new one can't be
# added to the list without appearing on screen.
pw = (ROOT / "dialogs" / "person_window.py").read_text(encoding="utf-8")
for k in SETTING_KEYS:
    check(f"the person window builds '{k}'", f"def _build_{k}(" in pw)

print()
if _fails:
    print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("test_person_settings: all checks passed")
