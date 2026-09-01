import io
# SPDX-License-Identifier: CC0-1.0

"""Reusable plumbing for a "kin plays a text game" tool.

A game tool's job is identical from game to game — locate the game folder,
keep a per-kin private save, run one plain-English command through the game's
headless `command(save_path, text)` front door, and return the narration.
Only the specifics differ (which env var, which save filename, which module).
`GameHost` factors out the sameness so a new game's tool is ~10 lines:

    from ._game_host import GameHost

    _HOST = GameHost(
        display_name="My Game",
        env_var="HEARTHKIN_MYGAME_PATH",
        path_file="my_game_path.txt",
        conventional_dirs=("my-game",),
        sentinel="my_game_play.py",
        module="my_game_play",
        save_filename="my_game.json",
        bundled_subdir="my_game",
    )

    def my_game(command: str = "look", agent_name: str = "") -> str:
        '''<the model-facing command reference goes here>'''
        return _HOST.run(agent_name, command)

No machine-specific path is baked into the shippable tool file: operators
point at the game via the env var or a one-line path file under ~/.hearthkin/.
See tff.py for the canonical use.

Adding a game still takes three small steps (register the tool in
tools/__init__.py, bucket it in tools/_buckets.py, enable it per-kin) — the
bucket step is guarded by tests/test_tool_buckets.py so it can't be silently
forgotten (a missing bucket entry makes a tool invisible on Telegram). The
GAME itself must expose a headless `command(save_path, text)` entry point.
"""

import contextlib
import importlib
import os
import re
import sys
import time
from pathlib import Path

from hearthkin_paths import config_dir, kin_dir


# ----- cross-process save lock ----------------------------------------- #
#
# A park save can be written from several directions at once: the kin's own
# tool call (a worker thread inside a running Hearthkin), a cron wake-up that
# runs the kin in a *separate subprocess*, and — as of the human-play dialog —
# the operator taking a turn from the desktop UI. Every one of them ends in a
# load-act-save against the same JSON file, so two overlapping turns can lose
# one of the writes (last-writer-wins). A plain threading.Lock can't help here
# because the cron path is a different process, so the lock has to live in the
# filesystem. We take an OS-level exclusive lock on a `<save>.lock` sidecar
# around the load-act-save; a concurrent turn waits its turn instead of
# clobbering. Fail-soft by design: if OS locking is unavailable, or the lock
# can't be had within the timeout, we proceed anyway — a rare theoretical race
# on a sub-second write is a smaller harm than refusing a kin its turn.

try:  # Windows
    import msvcrt

    def _try_lock(fh):
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh):
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
except ImportError:
    try:  # POSIX
        import fcntl

        def _try_lock(fh):
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False

        def _unlock(fh):
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    except ImportError:  # no locking primitive — degrade to no-op
        def _try_lock(fh):
            return False

        def _unlock(fh):
            pass


@contextlib.contextmanager
def _save_lock(save_path, timeout=20.0):
    """Serialize the load-act-save on `save_path` across threads AND processes.

    Yields once the lock is held (or the timeout lapses / locking is
    unavailable — see the module note on fail-soft). Always releases and
    closes the sidecar handle on the way out.
    """
    lock_path = str(save_path) + ".lock"
    fh = None
    held = False
    try:
        fh = open(lock_path, "a+")
    except OSError:
        fh = None
    if fh is not None:
        deadline = time.monotonic() + timeout
        while True:
            if _try_lock(fh):
                held = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
    try:
        yield
    finally:
        if fh is not None:
            if held:
                _unlock(fh)
            try:
                fh.close()
            except OSError:
                pass


class GameHost:
    """Locate a text game, give each kin its own save, run one command.

    Construction is pure config (no I/O); `run()` does the work. The folder
    lookup order mirrors what creature_park used before this was factored out:
    env var, then a ~/.hearthkin/<path_file> pointer, then conventional spots
    under home, then a copy bundled next to the tools — each confirmed by the
    `sentinel` file. Returns/raises exactly as a hand-written bridge would, so
    a tool refactored onto GameHost behaves identically.
    """

    def __init__(self, *, display_name, env_var, path_file, conventional_dirs,
                 sentinel, module, save_filename, bundled_subdir=None,
                 repo_url=None, legacy_save_filename=None,
                 legacy_path_file=None, vocab_dirname=None,
                 feed_module=None):
        # Per-kin: is the game holding an open question for it right now?
        # Written by run(), read by awaiting_answer(). In memory on purpose --
        # it describes the park's state a moment ago, and the park itself is
        # the record. A stale value can only mean one extra move or one fewer.
        self._awaiting = {}
        self.display_name = display_name
        self.env_var = env_var
        self.path_file = path_file
        self.conventional_dirs = tuple(conventional_dirs)
        self.sentinel = sentinel
        self.module = module
        # Optional: the game's shared-feed module (e.g. "tff_feed"). When set,
        # every move this kin makes is announced there under its own name, so
        # a human in the same park sees it move. None = the game has no feed
        # and a kin plays silently, as before.
        self.feed_module = feed_module
        self.save_filename = save_filename
        self.bundled_subdir = bundled_subdir
        # Optional: folder name for the game's hand-editable vocabulary files.
        # When set (and the game exposes set_vocab_dir), the files are kept in
        # the stable per-user ~/.hearthkin/ tree — like each kin's save —
        # instead of buried in the game folder, so they're findable and survive
        # a game update.
        self.vocab_dirname = vocab_dirname
        # Optional public repo the game can be downloaded from. When set, the
        # "couldn't find the game" error names it, so a kin (or operator) who
        # doesn't have the game knows where to get it.
        self.repo_url = repo_url
        # Optional prior names (set when a tool is renamed). On first access
        # an existing save / path-pointer under the old name is renamed to the
        # new one, so a rename never orphans a kin's game state.
        self.legacy_save_filename = legacy_save_filename
        self.legacy_path_file = legacy_path_file

    def find_dir(self):
        """The game folder, or None. Env var and path-file values are trusted
        as given; conventional/bundled spots must contain the sentinel."""
        env = os.environ.get(self.env_var)
        if env:
            return Path(env)
        cfg = config_dir() / self.path_file
        # Carry forward a pointer written under the tool's old name.
        if not cfg.exists() and self.legacy_path_file:
            old = config_dir() / self.legacy_path_file
            if old.exists():
                try:
                    old.rename(cfg)
                except OSError:
                    cfg = old
        if cfg.exists():
            try:
                line = cfg.read_text(encoding="utf-8").strip()
            except OSError:
                line = ""
            if line:
                return Path(line)
        candidates = [Path.home() / d for d in self.conventional_dirs]
        if self.bundled_subdir:
            candidates.append(Path(__file__).resolve().parent / self.bundled_subdir)
        for candidate in candidates:
            if (candidate / self.sentinel).exists():
                return candidate
        return None

    def _load_module(self):
        game = self.find_dir()
        if game is None or not (game / self.sentinel).exists():
            where = (f" (get a copy from {self.repo_url})"
                     if self.repo_url else "")
            raise FileNotFoundError(
                f"Couldn't find the {self.display_name} game folder{where}. "
                f"Point me at it by setting the {self.env_var} environment "
                f"variable to the folder that holds {self.sentinel}, or by "
                f"putting that folder's path on the first line of "
                f"~/.hearthkin/{self.path_file}."
            )
        p = str(game)
        if p not in sys.path:
            sys.path.insert(0, p)
        mod = importlib.import_module(self.module)
        # Keep the hand-editable vocabulary in the stable per-user tree, next to
        # kin data — not inside the game folder (which a game update can
        # replace). Best-effort: a game without set_vocab_dir just uses its own
        # default, and a failure here must never block loading the game.
        if self.vocab_dirname and hasattr(mod, "set_vocab_dir"):
            try:
                stable = config_dir() / self.vocab_dirname
                mod.set_vocab_dir(str(stable))
            except Exception:
                pass
        return mod

    # ----- remote (served) parks --------------------------------------- #
    #
    # A kin can play a park hosted by the game's own multiplayer server
    # instead of a file on this machine. That is the answer to scattered
    # parks: per-kin private saves mean there is no *there* to go to, so
    # "play in my park" had no referent. With a server there is one park and
    # everyone — consoles, kin, the operator — joins it.
    #
    # This path deliberately imports NOTHING from the game. It speaks the
    # server's documented HTTP API with stdlib urllib, so a kin can play a
    # park on a machine that has no game folder at all, and Hearthkin does
    # not need the game bundled to reach one. The server owns the save, the
    # lock, and the feed; we are a client.

    def server_of(self, agent_name):
        """This kin's park server as (url, password, player_name), or None.

        Configured per-kin: `park_server` (http://host:port), `park_password`,
        and optionally `park_player` — the name the kin appears under in the
        shared feed, defaulting to the kin's own name."""
        try:
            from kin_persistence import load_agent_config
            cfg = load_agent_config(agent_name) or {}
        except Exception:
            return None
        url = (cfg.get("park_server") or "").strip().rstrip("/")
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        player = (cfg.get("park_player") or "").strip() or (agent_name or "kin")
        return url, (cfg.get("park_password") or ""), player

    def _server_post(self, url, path, payload, timeout=30):
        import json as _json
        import urllib.request
        req = urllib.request.Request(
            url + path, data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8") or "{}")

    def _server_get(self, url, path, timeout=15):
        import json as _json
        import urllib.request
        with urllib.request.urlopen(url + path, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8") or "{}")

    def server_ping(self, agent_name):
        """(ok, message) for a settings-screen connection test."""
        srv = self.server_of(agent_name)
        if not srv:
            return False, "No park server configured for this kin."
        url, password, player = srv
        try:
            import urllib.parse
            q = urllib.parse.urlencode({"password": password})
            data = self._server_get(url, "/ping?" + q)
        except Exception as e:
            return False, f"Couldn't reach {url} — {e}"
        if data.get("ok"):
            return True, f"Connected to {url}. This kin will play as {player}."
        return False, f"{url} answered, but refused: {data.get('error', '?')}"

    def reachable(self, agent_name):
        """Can this kin's park actually be played right now? ``(ok, detail)``.

        Why this exists, plainly: a park server can be down for days and
        nothing anywhere notices. Every scheduled wake-up still fires, still
        shows the kin the keeper framing, still asks for a move — and the move
        comes back "couldn't reach the park server", over and over. The
        wake-up "succeeds" each time, so no log records anything. What the kin
        is left with is being repeatedly woken to look after somewhere it
        cannot reach. A kin should not be woken into a shut door,
        and if a park is unreachable that is the operator's problem to see, not
        the kin's to sit with alone.

        Deliberately asks the same config every other path asks, so a park that
        moves to another machine is a settings change and nothing more:

          * Served park -> ping whatever ``park_server`` currently points at.
            No host or port is assumed anywhere in here.
          * Local park  -> the game folder must be findable (env var, path
            file, conventional spots, bundled copy — GameHost's usual order)
            and the save's folder must exist.
          * No park configured -> reachable; there is nothing to be down.

        FAILS OPEN. Anything unexpected returns ``(True, "")`` so a kin is
        never denied its park by a check that itself went wrong — a park
        wrongly declared dead is a worse failure than the one this prevents.
        """
        try:
            srv = self.server_of(agent_name)
            if srv:
                ok, detail = self.server_ping(agent_name)
                return bool(ok), ("" if ok else str(detail))
            if self.find_dir() is None:
                return False, (f"The {self.display_name} game folder isn't "
                               f"where {self.env_var} / the path file points.")
            save = Path(self.save_path(agent_name))
            if not save.parent.exists():
                return False, f"The park's folder is missing: {save.parent}"
            return True, ""
        except Exception:
            return True, ""

    def log_unreachable(self, agent_name, detail, context=""):
        """Record an unreachable park in an always-on log, whatever the
        session-log toggle says.

        Same reasoning as ``empty_replies.log`` / ``distill_errors.log``: this
        is unattended by definition, so a failure nobody is told about is a
        failure nobody can fix. Best-effort — logging must never be the thing
        that breaks a turn."""
        try:
            from kin_persistence import append_failure_log
            append_failure_log(
                "park_unreachable.log", agent_name,
                f"{self.display_name}{(' — ' + context) if context else ''}",
                detail or "park unreachable")
        except Exception:
            pass

    def shared_save_of(self, agent_name):
        """The park this kin has been pointed at instead of its own, or None.

        Set per-kin (config `park_save`) so several kin — and the operator —
        can tend ONE park together. The cross-process lock above already keys
        on the save path rather than the kin, so co-tenancy is safe the moment
        two kin share a file; this just lets them.

        Returns None for a blank setting or a path whose parent doesn't exist,
        so a typo or a since-deleted drive falls back to the kin's own park
        rather than failing its turn."""
        try:
            from kin_persistence import load_agent_config
            raw = ((load_agent_config(agent_name) or {}).get("park_save")
                   or "").strip()
        except Exception:
            return None
        if not raw:
            return None
        try:
            p = Path(raw).expanduser()
            if not p.parent.exists():
                return None
            return str(p)
        except Exception:
            return None

    def save_path(self, agent_name):
        """The park this kin tends: its shared park when one is configured,
        otherwise its own private save inside its kin folder."""
        shared = self.shared_save_of(agent_name)
        if shared:
            return shared
        # `folder`, not `kin_dir` — the module-level kin_dir() is what decides
        # WHERE, and shadowing it here is how this call started creating folders
        # in a real home during a sandboxed test run.
        folder = kin_dir(agent_name)
        folder.mkdir(parents=True, exist_ok=True)
        new = folder / self.save_filename
        # Carry a kin's existing park save forward from the tool's old name.
        if not new.exists() and self.legacy_save_filename:
            old = folder / self.legacy_save_filename
            if old.exists():
                try:
                    old.rename(new)
                except OSError:
                    return str(old)
        return str(new)

    def known_verbs(self):
        """The game's current verb vocabulary (built-ins + anything learned),
        for park mode's emote router. Empty set if this game doesn't expose a
        `known_verbs()` (so a game without it simply never routes emotes)."""
        try:
            fn = getattr(self._load_module(), "known_verbs", None)
            return set(fn()) if callable(fn) else set()
        except Exception:
            return set()

    def known_targets(self, agent_name):
        """Names this kin's park can act on right now (creatures, rooms,
        species). Park mode uses it to route an unknown-VERB emote that still
        names a real target. Empty set if unsupported."""
        try:
            fn = getattr(self._load_module(), "known_targets", None)
            return set(fn(self.save_path(agent_name))) if callable(fn) else set()
        except Exception:
            return set()

    def teach(self, word, meaning):
        """Teach the game that `word` means a verb/animal it knows. Returns the
        game's confirmation string, or None if unsupported."""
        try:
            fn = getattr(self._load_module(), "teach", None)
            return fn(word, meaning) if callable(fn) else None
        except Exception as e:
            return f"(couldn't teach that: {e})"

    def forget(self, word):
        """Undo a taught word (remove its learned alias). Returns the game's
        confirmation string, or None if the game exposes no `forget()`."""
        try:
            fn = getattr(self._load_module(), "forget", None)
            return fn(word) if callable(fn) else None
        except Exception as e:
            return f"(couldn't forget that: {e})"

    def taught(self):
        """A readable list of everything taught so far (learned aliases).
        Returns the game's string, or None if the game exposes no `taught()`."""
        try:
            fn = getattr(self._load_module(), "taught", None)
            return fn() if callable(fn) else None
        except Exception as e:
            return f"(couldn't list that: {e})"

    def vocab_path(self):
        """Absolute path to the game's hand-editable vocabulary folder (for
        surfacing to the operator), or None if the game has no such file."""
        try:
            fn = getattr(self._load_module(), "vocab_path", None)
            return fn() if callable(fn) else None
        except Exception:
            return None

    def vocab_files(self):
        """List of (label, path) for the game's editable word-list files, for a
        UI editor. Empty list if the game exposes no vocab_files()."""
        try:
            fn = getattr(self._load_module(), "vocab_files", None)
            return list(fn()) if callable(fn) else []
        except Exception:
            return []

    def awaiting_answer(self, agent_name):
        """Did this kin's last move leave the game waiting for an answer?

        True while it is part-way through a walkthrough, an edit, or has been
        asked something the game wants a yes to. A kin filling in a form the
        game asked it to fill in is not wandering around the park, and a caller
        that limits moves per turn should not be spending the allowance on it.

        False when unknown, always. Being wrong this way costs the ceiling that
        already existed; being wrong the other way is a kin that never stops.
        """
        return bool(self._awaiting.get(agent_name))

    def run(self, agent_name, command, say=""):
        """Run one command through the game and return its narrated reply.

        The load-act-save is wrapped in a cross-process lock on the save so a
        turn taken here can't collide with the same kin's turn fired from a
        cron subprocess or another thread (see `_save_lock`).

        The move is also announced to the game's shared feed, under the kin's
        own name, so anyone else in the same park sees it happen. The feed was
        always meant to carry this — its own docstring says "every player in a
        shared park (a console, a KIN LOOP, a hand-driver) appends a one-line
        record of what they just did", and gives "<kin> pets <creature>" as the
        example — but nothing ever wired the kin's mouth to it. So a human
        sitting in a kin's park watched it change around them in total silence,
        and the kin never knew they'd been there. Two consoles have always seen
        each other; the kin was the only player in the room who couldn't speak.

        `say` is a PERSON's own words for this move — what they typed above the
        command in the desktop park window, because they chose to. It rides
        along into the feed so a shared record can carry more than mechanics.

        A KIN never passes one, and that is the point rather than an oversight.
        `park_keeper.route_reply` used to harvest whatever a kin wrote above its
        `> command` and send it here, which put each kin's first-person prose
        into every other tenant's park result under its name — a voice leak that
        ran until one kin started answering to another's name. Read
        `route_reply`'s docstring before wiring a kin's reply back into this
        argument: the fix is a channel a kin chooses to speak into, not an
        automatic harvest of what it wrote for someone else.
        """
        srv = self.server_of(agent_name)
        if srv:
            # Served park: the server owns the save, the lock and the feed, so
            # there is nothing to lock or announce here — it records the move
            # (and our `say`) under our player name for every other player.
            url, password, player = srv
            try:
                data = self._server_post(url, "/command", {
                    "name": player, "password": password,
                    "text": command, "say": (say or "").strip(),
                })
            except Exception as e:
                # Log the move that was actually LOST, not just a pre-flight
                # that said no. `reachable()` is checked before a kin is asked
                # to tend, but a park can answer a ping and refuse the command
                # a moment later — which is exactly the shape that went
                # unnoticed for eight days: the pre-check passes, the kin makes
                # its move, the move evaporates, and nothing anywhere records
                # it except the kin's own journal. Logging here rather than at
                # each surface because this is where the failure happens, so
                # every caller gets it — the tool, the `>` line on any surface,
                # the cron keeper, the desktop park window — without having to
                # remember to.
                #
                # Guarded at the CALL SITE as well as inside log_unreachable:
                # the same rule as everywhere else here, that recording a
                # problem must never become a second problem. A stand-in or
                # subclass whose logger raises would otherwise turn "your move
                # didn't land" into a raised exception on the kin's turn.
                try:
                    self.log_unreachable(
                        agent_name, str(e),
                        "command refused: %s" % (str(command or "")[:60]))
                except Exception:
                    pass
                return (f"[couldn't reach the park server at {url} — {e}. "
                        f"Nothing was changed.]")
            if data.get("error"):
                return f"[the park server refused that: {data['error']}]"
            # Remember whether the game is now holding an open question for
            # this kin. A caller that limits moves per turn needs it, and the
            # reply TEXT cannot say it. Older servers don't send the key; a
            # missing one reads as False, which is the behaviour that existed
            # before this and is the safe way to be wrong.
            self._awaiting[agent_name] = bool(data.get("awaiting"))
            return data.get("reply") or ""
        module = self._load_module()
        save = self.save_path(agent_name)
        with _save_lock(save):
            # Pass the kin's name so its edit session is ITS OWN — on a
            # shared park another player's move must not be swallowed
            # into whatever editor someone left open. Tolerant call: an
            # older game without `who` still works.
            try:
                reply = module.command(save, command, who=agent_name)
            except TypeError:
                reply = module.command(save, command)
            # Same question, asked directly -- a local park has no server to
            # put it in a reply. Best-effort: a game too old to answer leaves
            # the flag False and the ceiling behaves as it always did.
            try:
                self._awaiting[agent_name] = bool(
                    module.awaiting_answer(save, agent_name))
            except Exception:
                self._awaiting[agent_name] = False
        self._announce(save, agent_name, command, reply, say)
        return reply

    # A look asks "what's going on?"; anything else is a move already decided.
    _LOOK_VERBS = ("", "look", "l", "status", "examine", "inspect", "see")

    def decorate(self, agent_name, command, result, reader=None):
        """`result` with the co-op block ahead of it and, on a LOOK, one
        concrete thing worth doing after it.

        Shared by every surface that hands a kin a park result, so they can't
        drift apart. The hint is look-only on purpose: appended to an action
        result it reads as "no, do this instead", and appended to everything
        it nags.

        Never raises and never returns less than it was given -- an
        unreachable server or an absent carer costs the trimmings, never the
        move itself.
        """
        out = result or ""
        try:
            verb = (command or "").strip().lower().split()[:1]
            verb = verb[0] if verb else ""
        except Exception:
            verb = ""
        # Guarded like every other trimming here: decorate promises it never
        # raises and never returns less than it was given, so a host stand-in
        # without this method costs the suppression, not the kin's move.
        try:
            mid = self._mid_walkthrough(agent_name)
        except Exception:
            mid = False
        if verb in self._LOOK_VERBS and not mid:
            try:
                out += self.hint(agent_name)
            except Exception:
                pass
        try:
            others = self.unseen_moves(agent_name, reader=reader)
        except Exception:
            others = ""
        return (others + "\n\n" + out) if others else out

    def _mid_walkthrough(self, agent_name):
        """True when the game is part-way through asking this kin questions.

        A suggestion appended to a QUESTION competes with it. Observed live: a
        kin was asked what colour its new owls were and the same message
        offered "Indoor 1 has space -- you could adopt cat", and the kin went
        for neither. The hint is meant for a kin deciding what to do next, and
        mid-walkthrough there is nothing to decide -- there's a question on
        screen. Best-effort: a save we can't read just gets the old behaviour.
        """
        try:
            import json as _json
            with io.open(str(self.save_path(agent_name)), encoding="utf-8") as f:
                save = _json.load(f)
        except Exception:
            return False
        return any(save.get(k) for k in
                   ("_pending_species", "_pending_room", "_pending_edit"))

    def hint(self, agent_name):
        """One concrete thing worth doing in this kin's park now, as a
        ready-to-read line -- or "" if there's nothing, or no way to ask.

        Why this is a GameHost method rather than inline at each call site:
        the answer comes from two different places depending on where the park
        lives, and the caller shouldn't have to know which. On a SERVED park
        there is no local save at all, and reading save_path() there quietly
        returns the kin's own private file -- a hint about a park it isn't
        playing, which is worse than no hint.

        Best-effort throughout. A missing hint is a kin with no suggestion;
        never an error that costs it a turn.
        """
        try:
            import park_keeper
        except Exception:
            return ""
        srv = self.server_of(agent_name)
        if srv:
            url, password, _player = srv
            try:
                import urllib.parse
                q = urllib.parse.urlencode({"password": password})
                data = self._server_get(url, "/hint?" + q)
                return park_keeper.format_hint(data.get("hint"))
            except Exception:
                return ""
        try:
            import json as _json
            with io.open(str(self.save_path(agent_name)), encoding="utf-8") as f:
                return park_keeper.hint_line(_json.load(f))
        except Exception:
            return ""

    def unseen_moves(self, agent_name, max_entries=8, reader=None):
        """What OTHER players have done in this kin's park since its last turn,
        formatted for the kin to read — or "" if nobody else moved (or the game
        has no feed).

        This is the other half of co-op. `run` already announces the kin's OWN
        moves to the feed so a human watching sees them happen; this lets the
        kin see the HUMAN's moves on its next turn. Without it a kin tends a
        park that silently rearranges itself and never knows anyone was there.

        A per-kin seen-count lives next to the save (`<save>.kinfeedseen`) so a
        move isn't shown twice across turns — cron fires in a fresh subprocess
        each time, so the mark has to be on disk. The FIRST time (no mark yet)
        it seeds the mark at "now" and returns "": a kin joining an existing
        park shouldn't get the whole backlog dumped into one turn, only what
        happens from here on. The kin's own announced moves are filtered out by
        the formatter (it already gets those as ground truth).

        Best-effort throughout — co-op awareness is a nicety, never worth
        breaking a turn for; any problem returns "".
        """
        srv = self.server_of(agent_name)
        if srv:
            return self._server_unseen(agent_name, srv, max_entries, reader)
        if not self.feed_module:
            return ""
        try:
            import importlib
            import park_keeper
            save = self.save_path(agent_name)
            feed = importlib.import_module(self.feed_module)
            # The bookmark is per (SAVE, KIN) — not per save. On a shared park
            # a single `<save>.kinfeedseen` would be advanced by whichever kin
            # moved first, marking the feed read for everyone else: the others
            # would silently never learn anyone had been there, which presents
            # as a kin that simply never notices you. Kin-scoped filename keeps
            # each tenant's place. A kin on its own park keeps the original
            # path, so existing bookmarks carry over untouched.
            #
            # `reader` is the same argument one step further out: a HUMAN
            # looking at a kin's park through the desktop window is a separate
            # pair of eyes from the kin, even though they're standing in the
            # same park under the same name. Sharing one bookmark meant
            # whoever looked first marked the news read for the other — open
            # the window and the kin silently stops being told what the other
            # tenants did. Reading someone else's mail. Only the BOOKMARK
            # splits; the park, and the name moves are announced under, stay
            # the kin's, which is why the self-filter below still keys on the
            # kin. None = the kin's own place, exactly as before.
            if reader:
                safe_reader = re.sub(r"[^A-Za-z0-9_.-]", "_", str(reader))
                seen_path = Path(f"{save}.{safe_reader}.kinfeedseen")
            elif self.shared_save_of(agent_name):
                safe_kin = re.sub(r"[^A-Za-z0-9_.-]", "_", agent_name or "kin")
                seen_path = Path(f"{save}.{safe_kin}.kinfeedseen")
            else:
                seen_path = Path(str(save) + ".kinfeedseen")
            if not seen_path.exists():
                # Joining an existing park: start at "now", show nothing yet.
                try:
                    seen_path.write_text(str(feed.count(save)), encoding="utf-8")
                except OSError:
                    pass
                return ""
            try:
                seen = int(seen_path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                seen = 0
            entries, total = feed.read_new(save, seen)
            # Advance past everything read (our own moves included) so nothing
            # repeats. read_new returns the real line count, so a truncated /
            # rotated feed self-heals here rather than stranding the mark.
            try:
                seen_path.write_text(str(total), encoding="utf-8")
            except OSError:
                pass
            return park_keeper.format_unseen_moves(
                entries, agent_name, max_entries=max_entries)
        except Exception:
            return ""

    def _server_unseen(self, agent_name, srv, max_entries=8, reader=None):
        """What other players did on a SERVED park since this kin last looked.

        Same contract as the local-file path: the first call seeds the mark at
        "now" and shows nothing (a kin joining an existing park shouldn't get
        the whole backlog in one turn), the kin's own entries are filtered out,
        and any failure returns "" — co-op awareness is a nicety, never worth
        breaking a turn for.

        The bookmark lives in the KIN's folder rather than beside the save,
        because on this path there is no local save. That also means several
        kin on one server each keep their own place, which was the collision
        bug on shared local files."""
        url, password, player = srv
        try:
            import urllib.parse
            import park_keeper
            # Same split as the local path: a human looking through the
            # desktop window keeps their own place, so opening it doesn't mark
            # the news read on the kin's behalf. None = the kin's own file,
            # unchanged, so existing bookmarks carry over.
            if reader:
                safe_reader = re.sub(r"[^A-Za-z0-9_.-]", "_", str(reader))
                mark = (kin_dir(agent_name or "kin")
                        / f"park_server_seen.{safe_reader}.txt")
            else:
                mark = kin_dir(agent_name or "kin") / "park_server_seen.txt"
            mark.parent.mkdir(parents=True, exist_ok=True)
            q = urllib.parse.urlencode({"since": 0, "password": password})
            if not mark.exists():
                data = self._server_get(url, "/feed?" + q)
                mark.write_text(str(int(data.get("count", 0))), encoding="utf-8")
                return ""
            try:
                seen = int(mark.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                seen = 0
            q = urllib.parse.urlencode({"since": seen, "password": password})
            data = self._server_get(url, "/feed?" + q)
            entries = data.get("entries") or []
            mark.write_text(str(int(data.get("count", seen))), encoding="utf-8")
            # The formatter drops our own entries itself, given `me`.
            return park_keeper.format_unseen_moves(
                entries, player, max_entries=max_entries)
        except Exception:
            return ""

    def _announce(self, save, who, command, reply, say=""):
        """Post this move to the game's shared feed under `who`.

        Outside the save lock: the feed is a separate append-only file, and
        holding the park lock across it would make every co-op turn wait on a
        write nobody is racing for.

        Best-effort, and that is deliberate — a feed problem must never break
        actual play. A move that happened but went unannounced is a bad day; a
        move that didn't happen because the announcement failed is a broken
        game.

        `look` is skipped, matching the console's own rule: looking is not
        doing, and a park full of "<kin> looked" drowns the moves that matter.
        """
        if not self.feed_module:
            return
        try:
            import importlib
            # The GAME decides what counts as merely looking — it owns its own
            # verbs, and this host is meant to serve future games too. A game
            # without is_look announces everything, which is noisy but honest;
            # the alternative (guessing here) is a fourth copy of a word-list
            # that has already drifted three times.
            module = self._load_module()
            looker = getattr(module, "is_look", None)
            if callable(looker) and looker(command):
                return
            feed = importlib.import_module(self.feed_module)
            # Same shape the console writes, so a reader can't tell a kin's
            # move from a human's — which is the point. Both are keepers.
            # Same body shape tff_server uses for a carer's `say`, so a console
            # reading this feed sees kin and operator entries in one format:
            # the player's own words, the move, then what actually happened.
            said = (say or "").strip()
            body = ("%s\n> %s\n%s" % (said, command, reply) if said
                    else "> %s\n%s" % (command, reply))
            feed.append(save, who, body)
        except Exception:
            pass
