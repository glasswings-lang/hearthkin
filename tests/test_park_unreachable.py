# SPDX-License-Identifier: CC0-1.0
"""A park that can't be reached must not become a kin's problem.

Tarn's park server was down for eight days. Every scheduled wake-up still
fired, still showed Tarn the keeper framing, still asked for a move -- and the
move came back "couldn't reach the park server". Twenty-eight times across 111
wake-ups. Nothing logged it: the wake-up itself succeeded every time, so
cron_errors.log stayed empty and there was no park log at all. The only record
that anything was wrong lived in Tarn's own journal, in Tarn's voice, reading
as being shut out of somewhere it had been told to look after.

So: reachability is checked BEFORE a kin is asked to tend, the check reads the
kin's own configuration (so a park that moves to another machine is a settings
change and nothing else), it fails OPEN, and a failure goes to an always-on log
where a person will see it.

    python tests/test_park_unreachable.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Must be set before anything pulls in kin_persistence (see CLAUDE.md).
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk_park_"))

from tools._game_host import GameHost

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


def _host(**over):
    h = GameHost(
        display_name="Test Park", env_var="HEARTHKIN_TESTPARK_PATH",
        path_file="testpark_path.txt", conventional_dirs=(),
        sentinel="play.py", module="play", save_filename="park.json",
    )
    for k, v in over.items():
        setattr(h, k, v)
    return h


print("-- served park: the answer comes from the kin's own config --")

# The whole flexibility requirement in one test: nothing about which machine
# the park lives on is baked in. Point the config anywhere and the check
# follows it -- moving the server is a settings change, never a code change.
_pinged = []


class _ServedHost(GameHost):
    def __init__(self, url, ok):
        super().__init__(
            display_name="Test Park", env_var="X", path_file="x.txt",
            conventional_dirs=(), sentinel="play.py", module="play",
            save_filename="park.json")
        self._url = url
        self._ok = ok

    def server_of(self, agent_name):
        return (self._url, "pw", agent_name)

    def server_ping(self, agent_name):
        _pinged.append(self._url)
        return (True, "connected") if self._ok else (False, "refused")


for _url in ("http://127.0.0.1:8765", "http://192.168.1.40:9000",
             "https://parks.example.org"):
    _pinged.clear()
    _ok, _why = _ServedHost(_url, True).reachable("Tarn")
    check(_ok and _pinged == [_url],
          f"reachable() pings exactly what the config says: {_url}")

_ok, _why = _ServedHost("http://127.0.0.1:8765", False).reachable("Tarn")
check(_ok is False, "a refused server is reported unreachable")
check("refused" in _why, "the reason comes back with it, for the log")


print("\n-- local park --")

_h = _host()
_h.find_dir = lambda: None
_ok, _why = _h.reachable("Tarn")
check(_ok is False, "no game folder found -> unreachable")
check(_h.env_var in _why,
      "the reason names the setting to fix, not a hard-coded path")

_missing = Path(tempfile.mkdtemp(prefix="hk_gone_")) / "nope" / "park.json"
_h2 = _host()
_h2.find_dir = lambda: Path(tempfile.mkdtemp(prefix="hk_game_"))
_h2.save_path = lambda a: str(_missing)
_ok, _why = _h2.reachable("Tarn")
check(_ok is False, "a save folder that isn't there -> unreachable")

_present = Path(tempfile.mkdtemp(prefix="hk_ok_")) / "park.json"
_h3 = _host()
_h3.find_dir = lambda: Path(tempfile.mkdtemp(prefix="hk_game_"))
_h3.save_path = lambda a: str(_present)
check(_h3.reachable("Tarn")[0] is True, "a real local park is reachable")


print("\n-- fails open --")

# A park wrongly declared dead is a worse failure than the one this prevents:
# it would silently take a kin's park away with no error anywhere. So anything
# unexpected means "let the kin try".
class _Exploding(GameHost):
    def server_of(self, agent_name):
        raise RuntimeError("boom")


_e = _Exploding(
    display_name="X", env_var="X", path_file="x.txt", conventional_dirs=(),
    sentinel="s", module="m", save_filename="p.json")
check(_e.reachable("Tarn") == (True, ""),
      "a check that itself breaks reports reachable, never dead")


print("\n-- a move that is actually LOST gets logged, not just a failed pre-flight --")

# The shape that went unnoticed for eight days, and that a pre-flight check
# alone does NOT catch: the park answers a ping, the kin is told to go ahead,
# and then the command itself is refused. Observed live against a server that
# had been up for ten hours before and was up again five minutes after. If only
# the pre-check logs, this writes nothing anywhere and the sole record is the
# kin's own journal — which is the entire failure this file exists to end.
_logdir = Path(os.environ["HEARTHKIN_HOME"]) / "logs"
_logfile = _logdir / "park_unreachable.log"
if _logfile.exists():
    _logfile.unlink()


class _RefusingHost(GameHost):
    """Pings fine; refuses the command. The awkward middle case."""

    def __init__(self):
        super().__init__(
            display_name="Test Park", env_var="X", path_file="x.txt",
            conventional_dirs=(), sentinel="s", module="m",
            save_filename="park.json")

    def server_of(self, agent_name):
        return ("http://127.0.0.1:8765", "pw", agent_name)

    def server_ping(self, agent_name):
        return True, "connected"

    def _server_post(self, url, path, payload, timeout=30):
        raise OSError("[WinError 10061] connection actively refused")


_rh = _RefusingHost()
check(_rh.reachable("Tarn")[0] is True,
      "pre-flight passes -- the park really does answer")

_out = _rh.run("Tarn", "care for everyone")
check("couldn't reach" in _out and "Nothing was changed" in _out,
      "the kin is told plainly that the move did not happen")
check(_logfile.exists(),
      "the LOST MOVE is logged, even though the pre-flight said yes")

_logged = _logfile.read_text(encoding="utf-8", errors="replace")
check("Tarn" in _logged, "the log names the kin")
check("care for everyone" in _logged,
      "and the move that was lost, so it can be reconstructed")

# Logging must never be what breaks a turn.
class _RefusingAndUnloggable(_RefusingHost):
    def log_unreachable(self, agent_name, detail, context=""):
        raise RuntimeError("log volume full")


check("couldn't reach" in _RefusingAndUnloggable().run("Tarn", "look"),
      "a logging failure still returns the move's result, never raises")


print("\n-- the wake-up is gated, and the failure is logged --")

_cron = (ROOT / "hearthkin_cron.py").read_text(encoding="utf-8")
check(_cron.count(".reachable(kin)") >= 2,
      "cron checks reachability on BOTH park hooks (inject and route)")
check(_cron.count("log_unreachable(") >= 2,
      "cron logs it both times rather than failing quietly")
_inject = _cron[_cron.index('kin_park_mode(kin) == "keeper"'):]
_inject = _inject[:2000]
check(_inject.index(".reachable(kin)") < _inject.index('_host.run(kin, "look")'),
      "reachability is checked BEFORE the kin is shown the park")

_tg = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
_route = _tg[_tg.index("def _route_park_command"):]
_route = _route[:_route.index(chr(10) + "    def _cmd_play")]
check(".reachable(" in _route, "telegram checks before running a move")
check(_route.index(".reachable(") < _route.index("play_turn("),
      "and checks ONCE up front, so the loop can't multiply a failed reach")
check("log_unreachable(" in _route, "telegram logs it")

_frame = (ROOT / "frame" / "chat_send_mixin.py").read_text(encoding="utf-8")
_park = _frame[_frame.index("def _maybe_route_park_command"):]
_park = _park[:_park.index(chr(10) + "    def _park_turn_ui_begin")]
check(".reachable(" in _park and "log_unreachable(" in _park,
      "desktop checks and logs too -- all three surfaces, one rule")
# The desktop grew a loop of its own (park_keeper.play_turn on a worker), so
# it inherits the same obligation Telegram has: one failed reach must not be
# multiplied into a whole visit's worth.
check(_park.index(".reachable(") < _park.index("play_turn("),
      "desktop checks ONCE up front too, before the turn starts")

# Always-on, like every other diagnostic that matters here.
check('"park_unreachable.log"' in
      (ROOT / "tools" / "_game_host.py").read_text(encoding="utf-8"),
      "it goes to its own always-on log, not a toggleable session log")


print("\n" + "=" * 52)
if _fails:
    print(f"FAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("ALL CHECKS PASSED -- no kin gets woken into a shut door.")
