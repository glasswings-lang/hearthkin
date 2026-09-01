"""A kin's moves reach the shared feed, so a human in its park can see it.

Plain Python; run via tests/run_all.py. Skips cleanly if the tff game folder
isn't present (it ships separately).

Why: the feed's own docstring has always said "every player in a shared park (a
console, a KIN LOOP, a hand-driver) appends a one-line record of what they just
did", and gives "Tarn pets Bisker" as its example. Two consoles have always
seen each other. The kin never spoke. So a human could sit in a kin's park and
watch it change around them in total silence — the door was open, nobody had
wired the kin's mouth to it.

The `look` case is the load-bearing one: park-keeper looks at the park at the
START of every wake-up, so a host that announced looks would bury every real
move under "Tarn looked".
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


def _tff_dirs():
    """Where the game might be, in the order the app itself looks.

    Deliberately NO absolute path. A machine-specific default is not a
    fallback -- it guarantees the test runs on exactly one computer and
    quietly SKIPS on every other, which reads as "nothing to test here"
    rather than "this was never checked".

    `tff_path.txt` is the same file `tools/tff.py` reads, so whatever makes
    the game findable for the app makes it findable here. Read with plain
    utf-8 and .strip(), matching the app exactly: if a BOM would break it
    there it must break it here too, or the test passes on a config the app
    cannot actually use.
    """
    env = os.environ.get("HEARTHKIN_TFF_PATH")
    if env:
        yield env
    try:
        from hearthkin_paths import config_dir
        pf = pathlib.Path(config_dir()) / "tff_path.txt"
        if pf.exists():
            yield pf.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    yield str(pathlib.Path.home() / "tff")
    yield str(pathlib.Path.home() / "git-src" / "tff")


GAME = next((d for d in _tff_dirs()
             if d and (pathlib.Path(d) / "tff_play.py").exists()), None)
if GAME is None:
    print("  SKIP  tff game folder not found — nothing to test here.")
    print("        Point at it with HEARTHKIN_TFF_PATH or ~/.hearthkin/tff_path.txt")
    sys.exit(0)
sys.path.insert(0, GAME)
os.environ["HEARTHKIN_TFF_PATH"] = GAME

# Redirect HOME so the kin's park is a sandbox, never the real one. Must be set
# before the host resolves any path. (HEARTHKIN_TFF_PATH above is why the game
# is still findable afterwards — the game lookup uses home() too.)
_tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["USERPROFILE"] = str(_tmp)
os.environ["HOME"] = str(_tmp)
(_tmp / ".hearthkin" / "kin" / "FeedProbe").mkdir(parents=True)

import tff_feed                                            # noqa: E402
import tff_play                                            # noqa: E402
import tff_client                                          # noqa: E402
from tools.tff import _HOST                                # noqa: E402

save = _HOST.save_path("FeedProbe")
feed = pathlib.Path(tff_feed.feed_path(save))

# ── Looking is not doing ─────────────────────────────────────────────
_HOST.run("FeedProbe", "look")
check(not feed.exists(),
      "a kin LOOKING says nothing (park-keeper looks every single turn)")

# ── Acting is ────────────────────────────────────────────────────────
_HOST.run("FeedProbe", "dig 20")
check(feed.exists(), "a kin's real move reaches the feed")
lines = [json.loads(l) for l in feed.read_text(encoding="utf-8").splitlines()]
check(len(lines) == 1, "exactly one feed line per move")
check(lines[0].get("who") == "FeedProbe",
      "the move is under the KIN's own name")
check(lines[0].get("text", "").startswith("> dig 20"),
      "the feed carries the command the kin actually ran")
check(len(lines[0].get("text", "").splitlines()) > 1,
      "...and the game's narrated result, not just the command")

# ── Which is the whole point: a human in the park sees it ────────────
entries, seen = tff_client.LocalBackend(save, "SpeakerFifteen").read_new(0)
check(len(entries) == 1 and entries[0]["who"] == "FeedProbe",
      "a console in the same park READS the kin's move (co-presence)")
check(seen == 1, "the reader's position advances")

# The kin's own moves must not be filtered out of a HUMAN's view. (The console
# skips entries matching its own name; a kin's name is not the human's.)
check(entries[0]["who"] != "SpeakerFifteen",
      "the kin's move isn't mistaken for the reader's own")


# ── One source of truth for 'is this just looking?' ──────────────────
# This word-list had been copied three times and already drifted (tff_client's
# copy carried "status"; tff_play's didn't). A fourth copy — in another repo,
# inside the game-agnostic host — would have been the one that rotted quietly.
check(tff_play.is_look("look") and tff_play.is_look("  Examine  ")
      and tff_play.is_look("status"),
      "the game's text layer owns the look-words, including the drifted one")
check(not tff_play.is_look("care for everyone")
      and not tff_play.is_look("") and not tff_play.is_look("dig 50"),
      "...and a real move is not a look")
check(tff_client._is_look("look") is tff_play.is_look("look"),
      "the console delegates to the same answer the kin uses")

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
    sys.exit(1)
print("test_game_feed.py: all checks passed")
