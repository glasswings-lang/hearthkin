# SPDX-License-Identifier: CC0-1.0
"""The "a newer default is available" nudge must be about a real difference.

Reported: a nag on EVERY restart saying a newer default had shipped for the
base prompt, naming nowhere to go and never clearing. The file on disk was
byte-identical to the shipped default the whole time.

Two faults, and both are pinned here.

**The stamp was never written when the file was seeded.** `save_base_prompt`
recorded a version; the automatic seeding in `load_base_prompt` did not. So
the file was born "out of date": a missing stamp reads as version 1, and any
shipped version above 1 flags a file that was just written FROM that default.
Anyone who never hand-edited their base prompt was nagged forever.

**And it could not be cleared.** The Prompt updates dialog only adopts
registry prompts; a legacy one has no button. The advice was "compare them by
hand", which for a non-visual user with no way to see the shipped text is not
an instruction at all.

The dangerous fix here would be to quieten the warning. So the positive
control comes FIRST: a genuinely edited file on an old version must still be
flagged, or the rest of this file is measuring nothing.

Run: python tests/test_prompt_update_nag.py
"""

import json
import os
import sys
import tempfile

sandbox = tempfile.mkdtemp(prefix="hk_nag_")
os.environ["HEARTHKIN_HOME"] = sandbox
os.environ.setdefault("HEARTHKIN_SILENT", "1")

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kin_persistence as KP

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


def stamps():
    p = KP._seeded_versions_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def flagged_keys():
    return [k for (k, _h, _s, _t) in KP.legacy_prompt_overrides_needing_review()]


print("\n-- POSITIVE CONTROL: a real edit on an old version IS flagged --")

KP.BASE_PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
KP.BASE_PROMPT_FILE.write_text(
    "I have rewritten this entirely in my own words.\n", encoding="utf-8")
KP._record_legacy_seeded_version("base_prompt", 1)
check("base_prompt" in flagged_keys(),
      "a hand-edited base prompt stamped v1 is still flagged")

# ...and stays flagged when the stamp is missing altogether, which is the
# situation the seeding bug created. Content differs, so it is a real review.
p = KP._seeded_versions_path()
d = stamps()
d.pop("base_prompt", None)
p.write_text(json.dumps(d), encoding="utf-8")
check("base_prompt" in flagged_keys(),
      "...and with no stamp at all, since the wording genuinely differs")

print("\n-- the actual bug: identical content is NOT a difference --")

KP.BASE_PROMPT_FILE.write_text(KP.DEFAULT_BASE_PROMPT, encoding="utf-8")
d = stamps()
d.pop("base_prompt", None)
p.write_text(json.dumps(d), encoding="utf-8")
check("base_prompt" not in flagged_keys(),
      "a file identical to the shipped default is not flagged, stamp or no")

# Windows writes CRLF; the default in source is LF. A nag nobody can clear
# because of line endings would be worse than the one being fixed.
KP.BASE_PROMPT_FILE.write_bytes(
    KP.DEFAULT_BASE_PROMPT.replace("\n", "\r\n").encode("utf-8"))
check("base_prompt" not in flagged_keys(),
      "...and line endings alone never count as a difference")

# Trailing whitespace likewise.
KP.BASE_PROMPT_FILE.write_text(
    KP.DEFAULT_BASE_PROMPT.rstrip() + "\n\n\n", encoding="utf-8")
check("base_prompt" not in flagged_keys(),
      "...nor trailing blank lines")

print("\n-- seeding stamps what it wrote, so a fresh file is never stale --")

try:
    KP.BASE_PROMPT_FILE.unlink()
except OSError:
    pass
d = stamps()
d.pop("base_prompt", None)
p.write_text(json.dumps(d), encoding="utf-8")

text = KP.load_base_prompt()
check(KP.BASE_PROMPT_FILE.exists(), "load_base_prompt seeds the file")
check(stamps().get("base_prompt") == KP.DEFAULT_BASE_PROMPT_VERSION,
      "...and records the version it seeded, rather than leaving it blank")
check("base_prompt" not in flagged_keys(),
      "...so a freshly seeded install is silent, not nagging on first run")

print("\n-- a change one character deep is still caught --")

KP.BASE_PROMPT_FILE.write_text(
    KP.DEFAULT_BASE_PROMPT + "\nOne line I added myself.\n", encoding="utf-8")
d = stamps()
d["base_prompt"] = 1
p.write_text(json.dumps(d), encoding="utf-8")
check("base_prompt" in flagged_keys(),
      "an addition to the default is a real difference and IS flagged")

print("\n-- a flagged legacy prompt can actually be adopted --")

# The nudge used to name Tools -> Prompt updates, and that screen had no row
# for a legacy prompt at all. Being told an update exists, on the one screen
# that resolves updates, and finding nothing there, is a dead end.
old_wording = KP.DEFAULT_BASE_PROMPT.split(
    "## Saying what actually happened")[0].rstrip() + "\n"
KP.BASE_PROMPT_FILE.write_text(old_wording, encoding="utf-8")
KP._record_legacy_seeded_version("base_prompt", 2)
check("base_prompt" in flagged_keys(),
      "an older base prompt is flagged, as it should be")

shipped, mine = KP.prompt_update_texts("base_prompt")
check(shipped and mine and shipped.strip() != mine.strip(),
      "the preview can show both sides, so there is something to compare")

check(KP.adopt_prompt_update("base_prompt") is True,
      "adopt works on a legacy key, not just a registry one")
check("base_prompt" not in flagged_keys(),
      "...and the nag clears, because the version stamp moved with it")
check("Saying what actually happened"
      in KP.BASE_PROMPT_FILE.read_text(encoding="utf-8"),
      "...and the file really did gain the new wording")

backups = list((KP.PROMPTS_DIR / "backups").glob("base_prompt*"))
check(len(backups) >= 1,
      "your previous wording is backed up before it is replaced")
check(old_wording.strip()
      in backups[0].read_text(encoding="utf-8").strip(),
      "...and the backup holds what you actually had")

check(KP.stash_prompt_update("base_prompt") is not None,
      "stash works for a legacy key too, for reading without adopting")
check(KP.adopt_prompt_update("no_such_prompt") is False,
      "an unknown key is refused rather than silently doing nothing")

print("\n-- the nudge names somewhere to go --")

src = (ROOT / "frame" / "menus_mixin.py").read_text(encoding="utf-8")
i = src.index("_maybe_nudge_prompt_updates")
body = src[i:i + 3000]
check("Prompt updates" in body,
      "the registry nudge names the Tools menu item that resolves it")

if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- the nag fires on real differences only.")
