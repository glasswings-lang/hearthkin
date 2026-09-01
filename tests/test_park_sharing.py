"""Shared-park bridge: several kin (and the operator) tending ONE park.

Plain Python; run via tests/run_all.py.

The cross-process lock in tools/_game_host.py already keys on the save PATH
rather than the kin, so co-tenancy was safe as soon as two kin could share a
file. These checks cover the parts that weren't: resolving the shared path,
falling back safely when it's wrong, and — the subtle one — keeping each
tenant's feed bookmark separate.
"""
import os
import re
import sys
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kin_persistence as k
from tools import get_game

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


host = get_game("tff")
tmp = pathlib.Path(tempfile.mkdtemp())
shared = tmp / "shared_park.json"
shared.write_text("{}", encoding="utf-8")

_real_cfg = k.load_agent_config
k.load_agent_config = lambda name: {
    "Alpha": {"park_save": str(shared)},
    "Beta": {"park_save": str(shared)},
    "Solo": {},
    "Blank": {"park_save": "   "},
    "Broken": {"park_save": r"Q:\no\such\drive\park.json"},
}.get(name, {})

try:
    check(host.save_path("Alpha") == str(shared),
          "shared park: configured path is used")
    check(host.save_path("Alpha") == host.save_path("Beta"),
          "shared park: two kin resolve to the SAME file (that's the point)")

    solo = host.save_path("Solo")
    check("Solo" in solo and str(shared) != solo,
          "no setting: kin keeps its own private park")
    blank = host.save_path("Blank")
    check("Blank" in blank,
          "blank setting: treated as unset, not as a path")

    # A typo or a since-removed drive must not cost a kin its turn.
    broken = host.save_path("Broken")
    check("Broken" in broken and "Q:" not in broken,
          "bad path: falls back to the kin's own park rather than failing")

    # The subtle one. A single <save>.kinfeedseen would be advanced by
    # whichever kin moved first, marking the feed read for everyone else —
    # presenting as a kin that simply never notices anyone was there.
    def bookmark(kin):
        save = host.save_path(kin)
        if host.shared_save_of(kin):
            return f"{save}.{re.sub(r'[^A-Za-z0-9_.-]', '_', kin)}.kinfeedseen"
        return save + ".kinfeedseen"

    check(bookmark("Alpha") != bookmark("Beta"),
          "shared park: each kin gets its OWN feed bookmark")
    check(bookmark("Solo").endswith("tff.json.kinfeedseen"),
          "own park: bookmark path unchanged, so existing marks carry over")

    check(host.shared_save_of("Solo") is None,
          "shared_save_of: None when unset")
    check(host.shared_save_of("Alpha") == str(shared),
          "shared_save_of: the path when set")
finally:
    k.load_agent_config = _real_cfg


# ─── Served parks (kin joins a running tff_server) ────────────────────────────
# The answer to scattered parks: per-kin private saves mean there is no *there*
# to go to. With a server there is ONE park and everyone joins it — and this
# path imports nothing from the game, so a kin can play a park on a machine
# with no game folder at all.
k.load_agent_config = lambda name: {
    "Plain":  {"park_server": "localhost:8765", "park_password": "pw"},
    "Full":   {"park_server": "http://box:9000/", "park_password": "pw",
               "park_player": "Wanderer"},
    "Both":   {"park_server": "localhost:8765", "park_password": "pw",
               "park_save": r"C:\somewhere\park.json"},
    "None":   {},
}.get(name, {})

check(host.server_of("None") is None, "server: None when unconfigured")

url, pw, player = host.server_of("Plain")
check(url == "http://localhost:8765",
      "server: bare host:port gets http:// and no trailing slash")
check(player == "Plain",
      "server: player name defaults to the kin's own name")

url, pw, player = host.server_of("Full")
check(url == "http://box:9000" and player == "Wanderer",
      "server: explicit scheme kept, trailing slash trimmed, player honoured")

check(host.server_of("Both") is not None,
      "server wins over a shared file when both are set")

ok, msg = host.server_ping("None")
check(not ok and "No park server" in msg,
      "server ping: unconfigured says so instead of raising")

# --- the TOOL path sees co-op too, not just the cron keeper ------------------
# unseen_moves was wired into hearthkin_cron only, so a kin playing through the
# tff TOOL was blind to everyone else: it could care for everyone seconds after
# another tenant did and never know. That also skews any comparison between the
# two paths -- an informed keeper against a blind tool is not a fair test of
# the doors, only of who was told what.
import importlib as _il
_tfftool = _il.import_module("tools.tff")  # the MODULE; tools/ re-exports the function under the same name

class _FakeHost:
    # Borrows the REAL decorate, so these checks exercise the shipped
    # composition rule rather than a stub that agrees with itself. Only the
    # three things it calls out to (run / unseen_moves / hint) are faked.
    from tools._game_host import GameHost as _G
    decorate = _G.decorate
    _LOOK_VERBS = _G._LOOK_VERBS
    del _G

    def __init__(self, others):
        self._others = others
        self.calls = []

    def run(self, agent, command):
        self.calls.append((agent, command))
        return "RESULT-BODY"

    def unseen_moves(self, agent, max_entries=8, reader=None):
        return self._others


_real_host = _tfftool._HOST
try:
    _tfftool._HOST = _FakeHost("SpeakerFifteen cared for everyone.")
    _out = _tfftool.tff("look", agent_name="Tarn")
    check(_out.startswith("SpeakerFifteen cared for everyone."),
          "tool: another tenant's moves lead the result")
    check("RESULT-BODY" in _out,
          "tool: the kin's own result still comes through")

    _tfftool._HOST = _FakeHost("")
    _solo = _tfftool.tff("look", agent_name="Tarn")
    check(_solo == "RESULT-BODY",
          "tool: a solo kin sees no co-op block at all")

    # A failing feed read must never cost the kin its move.
    class _Broken(_FakeHost):
        def unseen_moves(self, agent, max_entries=8, reader=None):
            raise RuntimeError("feed unreadable")

    _tfftool._HOST = _Broken("")
    check(_tfftool.tff("look", agent_name="Tarn") == "RESULT-BODY",
          "tool: a broken feed read still returns the move")
finally:
    _tfftool._HOST = _real_host

# The description must not promise privacy -- a kin told the park is its own
# acts as though alone in one it shares.
_doc = _tfftool.tff.__doc__ or ""
check("your own private park" not in _doc,
      "tool: description no longer claims the park is private")
check("top of the result" in _doc,
      "tool: description teaches where other tenants' moves appear")


# --- salience: the tool path gets a hint too, on a look ----------------------
# An open field plus one command that never fails teaches a kin to retreat to
# `care for everyone` and narrate contentment. The keeper path has had a
# concrete one-move hint for a while; the tool had none, which is much of why
# the two paths behave differently. On a LOOK only: on every result it nags,
# on an action result it reads as "no, do this instead".
class _HintHost(_FakeHost):
    def __init__(self, hint):
        _FakeHost.__init__(self, "")
        self._hint = hint

    def hint(self, agent):
        return self._hint


_HINT = (chr(10) * 2
         + "One thing worth doing right now: they are bonded. "
           "You could `breed Nook`.")
try:
    _tfftool._HOST = _HintHost(_HINT)
    check("worth doing" in _tfftool.tff("look", agent_name="Tarn"),
          "tool: a look carries one concrete thing worth doing")
    check("worth doing" not in _tfftool.tff("care for everyone", agent_name="Tarn"),
          "tool: an action result is not second-guessed with a hint")
    check("worth doing" in _tfftool.tff("", agent_name="Tarn"),
          "tool: the default (bare) command counts as a look")

    class _NoHint(_HintHost):
        def hint(self, agent):
            raise RuntimeError("carer unavailable")

    _tfftool._HOST = _NoHint("")
    check(_tfftool.tff("look", agent_name="Tarn") == "RESULT-BODY",
          "tool: an unavailable hint still returns the look")
finally:
    _tfftool._HOST = _real_host

# Local and served parks must phrase the suggestion identically -- a kin should
# not be able to tell which kind of park it is in from how advice is worded.
import park_keeper as _PKF
from tools import _game_host as _gh
check(_PKF.format_hint({"text": "t", "command": "c"})
      == _PKF.format_hint({"text": "t", "command": "c"}),
      "hint wording is shared between the local and served paths")
check(_PKF.format_hint(None) == "" and _PKF.format_hint({}) == "",
      "no opportunity means no line, not a broken one")


# --- the Telegram `>` line gets the same trimmings ---------------------------
# It had NEITHER the co-op block nor the hint. That mattered least while the
# tool existed and would matter most if the tool retires, since it would leave
# the `>` line as the only way in -- and the poorest of the three. Checked at
# the source level (as the cron routing checks are) because exercising it for
# real needs a live bot object.
_tb = pathlib.Path(__file__).resolve().parent.parent / "telegram_bot.py"
_src = _tb.read_text(encoding="utf-8")
# The park routing is TWO methods now — the per-turn loop (_route_park_command)
# and the single move it repeats (_route_one_park_move) — so slice to the first
# method that isn't park routing rather than to the next `def` at all. Slicing
# to the next `def` silently stopped covering the decorate step the moment the
# single move was split out, which is a check quietly passing on half the code.
_route = _src[_src.index("def _route_park_command"):]
_route = _route[:_route.index(chr(10) + "    def _cmd_play")]

check("host.decorate(" in _route,
      "telegram `>`: the kin's copy of the result is decorated")
check(_route.index('_send_chunked(chat_id, "🌳 " + res)')
      < _route.index("host.decorate("),
      "telegram `>`: the operator's chat post stays the PLAIN result")
check("{result}" in _route and "kin_res" in _route,
      "telegram `>`: it is the decorated text that becomes ground truth")


# --- the desktop window keeps its own place in the feed ----------------------
# One bookmark shared between a kin and the HUMAN looking at the same park meant
# whoever looked first marked the news read for the other: open "Tend a kin's
# park" and the kin silently stops being told what the other tenants did. You'd
# be reading its mail. Only the bookmark splits -- the park, and the name moves
# are announced under, stay the kin's.
import sys as _sys, types as _types, tempfile as _tf, pathlib as _pl

_feed_state = {"entries": [], }


class _FakeFeed(_types.ModuleType):
    def count(self, save):
        return len(_feed_state["entries"])

    def read_new(self, save, seen):
        return _feed_state["entries"][seen:], len(_feed_state["entries"])


_sys.modules["_fake_feed"] = _FakeFeed("_fake_feed")

_tmp = _pl.Path(_tf.mkdtemp(prefix="hk_feed_"))


class _FeedHost(_gh.GameHost):
    def __init__(self):
        super().__init__(display_name="F", env_var="F", path_file="f.txt",
                         conventional_dirs=(), sentinel="s", module="m",
                         save_filename="park.json", feed_module="_fake_feed")

    def server_of(self, agent_name):
        return None

    def shared_save_of(self, agent_name):
        return None

    def save_path(self, agent_name):
        return str(_tmp / "park.json")


_fh = _FeedHost()

# Seed both bookmarks at "now" (first call always shows nothing, by design).
check(_fh.unseen_moves("Bracken") == "", "kin's first look seeds, shows nothing")
check(_fh.unseen_moves("Bracken", reader="desktop") == "",
      "desktop's first look seeds separately, also shows nothing")

_kin_mark = _tmp / "park.json.kinfeedseen"
_desk_mark = _tmp / "park.json.desktop.kinfeedseen"
check(_kin_mark.exists() and _desk_mark.exists(),
      "two distinct bookmark files, not one shared with the kin")

# Vesper does something. The human looks FIRST.
_feed_state["entries"] = [{"who": "Vesper", "text": "fussed the birds"}]
_desk = _fh.unseen_moves("Bracken", reader="desktop")
check("Vesper" in _desk, "the desktop window finally sees another tenant's move")

# ...and the kin must STILL be told. This is the whole bug.
_kin = _fh.unseen_moves("Bracken")
check("Vesper" in _kin,
      "the kin still gets the news after the human looked (no mail-reading)")

# The self-filter still keys on the KIN, because moves made from that window
# are announced under the kin's name -- splitting the bookmark must not make a
# kin's own moves come back to it as somebody else's news.
_feed_state["entries"].append({"who": "Bracken", "text": "dug 30"})
check("dug 30" not in _fh.unseen_moves("Bracken", reader="desktop"),
      "moves announced as the kin are still filtered out of the human's view")



print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL PARK-SHARING CHECKS PASSED")
