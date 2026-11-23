# SPDX-License-Identifier: CC0-1.0
"""A kin's settings save keeps what someone else changed meanwhile (23 Nov 2026).

The owner caught it: editing a kin's config.json while Hearthkin is open did
nothing, because the running app held the settings in memory and wrote the
whole file back on its next save. Now a save writes only what its own copy
changed since it was loaded, on top of the disk as it is now. Pinned:

  * a hand edit survives the app saving a different setting;
  * ...inside a nested block (telegram) too;
  * the app's copy sees the hand edit after saving;
  * when both changed the same setting, the save wins, as before;
  * a setting the app removed stays removed;
  * a later save from the same copy still keeps it;
  * the check can fail: a copy with no idea what it was loaded from
    overwrites, exactly the old behaviour.

Run: python tests/test_config_save_merge.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="cfgmerge-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as kp  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


KIN = "Sorrel"
kin_dir(KIN).mkdir(parents=True, exist_ok=True)
path = kin_dir(KIN) / "config.json"
first = dict(kp.DEFAULT_AGENT_CONFIG)
first["model"] = "old-model"
kp.save_agent_config(KIN, first)


def hand_edit(fn):
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def disk():
    return json.loads(path.read_text(encoding="utf-8"))


app = kp.load_agent_config(KIN)                    # the open app's copy
hand_edit(lambda d: d.__setitem__("cron_tools_off", ["exec"]))
app["model"] = "new-model"
kp.save_agent_config(KIN, app)
check("a hand edit survives the app saving something else",
      disk().get("cron_tools_off") == ["exec"])
check("...and the app's own change landed", disk().get("model") == "new-model")
check("the app's copy now sees the hand edit", app.get("cron_tools_off") == ["exec"])

hand_edit(lambda d: d["telegram"].__setitem__("hand_key", "kept"))
app["telegram"]["app_key"] = "also"
kp.save_agent_config(KIN, app)
check("inside the telegram block, both changes are kept",
      disk()["telegram"].get("hand_key") == "kept" and disk()["telegram"].get("app_key") == "also")

hand_edit(lambda d: d.__setitem__("model", "hand-model"))
app["model"] = "app-model"
kp.save_agent_config(KIN, app)
check("both changed the same setting: the save wins", disk().get("model") == "app-model")

app["temporary"] = 1
kp.save_agent_config(KIN, app)
del app["temporary"]
kp.save_agent_config(KIN, app)
check("a setting the app removed stays removed", "temporary" not in disk())

app["num_ctx"] = 12345
kp.save_agent_config(KIN, app)
check("a later save from the same copy still keeps the hand edit",
      disk().get("cron_tools_off") == ["exec"])

hand_edit(lambda d: d.__setitem__("cron_tools_off", ["web_search"]))
kp._CFG_SEEN.clear()
blind = dict(app)                                   # no memory of loading
blind["model"] = "blind"
kp.save_agent_config(KIN, blind)
check("the check can fail: a copy with no base overwrites, as before",
      disk().get("cron_tools_off") == ["exec"])

print()
if _fails:
    print(f"test_config_save_merge: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_config_save_merge: all checks passed")
