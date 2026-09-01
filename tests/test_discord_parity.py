# SPDX-License-Identifier: CC0-1.0
"""Guard test: the Discord surface's three silent holes.

The Discord tab looks like the Telegram tab, so it reads as a finished
feature. Underneath, three things it looked like it did, it did not do -- and
each failed in the one way nobody can see from a chair: silently.

  1. An EMPTY REPLY vanished. If the model returned nothing, or returned only
     something the anti-impersonation cleanup ate, `_generate` fell off its
     own end: nothing sent, nothing persisted, nothing logged. From the
     channel that is indistinguishable from a kin choosing to ignore you, and
     there was no file anywhere to check afterwards. Both other surfaces have
     handled this for a long time. This pins that the placeholder goes out,
     that the log line is written, that a reply salvaged from mid-tool-loop
     is preferred over the placeholder, and that the placeholder is NOT
     persisted -- a kin reads its own history back, and would explain or
     apologise for a marker the harness wrote in its voice.

  2. `use_webcam` had NO GATE. It rides in the WRITE bucket next to
     write_file and note, so granting someone write access on Discord handed
     them the camera in the room the operator is sitting in, with nothing to
     approve. exec was already routed to a desktop dialog; the webcam was
     not. This pins that it is wrapped when an approval channel exists, and
     DROPPED -- not fired -- when one doesn't.

  3. The settings tab could not express "anyone". `allow_from` empty means
     nobody, deliberately; `["*"]` means anyone. The old field kept digits
     only, so it silently dropped the star, while its own label said empty
     meant anyone. Between the two there was no way from the UI to open a kin
     to a server. This pins the validator that replaced it.

Deliberately no wx and no network: the frame's dialog is stubbed, the model is
stubbed, and the checks are on what reached disk and what reached the channel.

Run: python tests/test_discord_parity.py
"""

import os
import sys
import json
import tempfile
from pathlib import Path

os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="dcparity-"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


from discord_bot import DiscordBot  # noqa: E402


# ── stubs ──────────────────────────────────────────────────────────────

class _Author:
    def __init__(self, uid=7, name="Wren"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = False


class _Handle:
    """Stands in for a sent discord Message. The surface streams by editing
    one message in place, so what the reader finally sees is the last edit,
    not the first send -- both are recorded."""

    def __init__(self, sink, text):
        self.sink = sink
        self.sink.append(text)

    def edit(self, content=""):
        self.sink.append(content)
        return self


class _Channel:
    def __init__(self, cid=99, sink=None):
        self.id = cid
        self.sink = sink if sink is not None else []

    # Not a coroutine on purpose: the test replaces asyncio's
    # run_coroutine_threadsafe with a pass-through, so whatever send()
    # returns is what the surface receives as its message handle.
    def send(self, text):
        return _Handle(self.sink, text)


class _Message:
    def __init__(self, sink=None):
        self.author = _Author()
        self.channel = _Channel(sink=sink)
        self.attachments = []


class _Bot(DiscordBot):
    """Enough DiscordBot to drive _generate's tail without a Gateway."""

    def __init__(self, **kw):
        import threading
        self.agent_name = "Bracken"
        self.sent = []
        self.persisted = []
        self._surface_label = "discord"
        self._wrap_exec = kw.get("wrap_exec")
        self._request_webcam_approval = kw.get("request_webcam_approval")
        self._last_recall_used = []
        self._active_turns = {}
        self._turn_lock = threading.Lock()

    # Capture instead of transmitting.
    def _post_sync(self, channel, text):
        self.sent.append(text)

    def _post_from_thread(self, channel, text):
        self.sent.append(text)

    def _persist_turn(self, channel_id, role, content, share):
        self.persisted.append((role, content))


# ── 1. the empty reply ─────────────────────────────────────────────────

def _log_path():
    from kin_persistence import LOGS_DIR
    return LOGS_DIR / "empty_replies.log"


def _log_text():
    p = _log_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


print("--- an empty reply is no longer silent ---")

before = len(_log_text())
bot = _Bot()
bot._log_empty_reply(
    model="gemma4:31b", channel_id=99, user_id=7,
    raw_content="[Bracken]:", post_cleanup="",
    intermediate_content="", tool_calls_made=[], salvaged=False)
after = _log_text()

check("a reply that came back empty reaches empty_replies.log",
      len(after) > before)
check("...naming the surface, so it can be told from a desktop one",
      "surface=discord" in after)
check("...and holding what the model actually returned",
      "[Bracken]:" in after)
check("...which is the whole point: cleanup ate it, the model wasn't silent",
      "post_cleanup=''" in after)

before = len(_log_text())
bot._log_empty_reply(
    model="gemma4:31b", channel_id=99, user_id=7,
    raw_content="", post_cleanup="", intermediate_content="on it now",
    tool_calls_made=["read_file"], salvaged=True)
after = _log_text()
check("a salvaged reply is logged too, marked as salvaged",
      "surface=discord [salvaged]" in after)
check("...with the tool that ran, so the pattern is visible over time",
      "'read_file'" in after)


# The placeholder path, driven through the REAL _generate rather than
# asserted against its source. A test that greps for a branch passes just as
# happily when the branch is unreachable, and the failure being fixed here
# was precisely a path nobody noticed was never taken.
print("--- the placeholder reaches the channel, but never the history ---")

import llm_backend  # noqa: E402
from kin_persistence import save_agent_config, DEFAULT_AGENT_CONFIG  # noqa: E402


class _Result:
    """The blocking-shaped ChatResult chat_collect hands back."""

    def __init__(self, content="", thinking="", stopped=False):
        self.content = content
        self.thinking = thinking
        self.stopped = stopped


def _fake_chat_collect(model_says, stop_midway=None):
    """Stand in for llm_backend.chat_collect: feed the deltas to on_content
    exactly as the real one does (the surface streams through that callback,
    so a stub that skips it would test nothing about what reaches the
    channel), and honour should_stop between them.

    `stop_midway` is called once, after the first delta, to simulate somebody
    typing "stop" in the channel while the reply is being written. It sets the
    REAL flag on the real bot, so what follows is the genuine stop path rather
    than a stubbed answer about it."""
    def _call(model, messages, *, on_content=None, should_stop=None, **kw):
        got = []
        for i, delta in enumerate(model_says):
            if should_stop is not None and should_stop():
                return _Result("".join(got), stopped=True)
            got.append(delta)
            if on_content:
                on_content(delta)
            if stop_midway is not None and i == 0:
                stop_midway()
        return _Result("".join(got))
    return _call


def _run_generate(model_says, stop_midway=False):
    """Drive DiscordBot._generate with a stubbed model and return the bot,
    so the caller can look at what was sent and what was persisted."""
    kin = "Bracken"
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg["model"] = "gemma4:31b"
    cfg["num_ctx"] = 8192
    save_agent_config(kin, cfg)

    b = _Bot()
    b.get_config = lambda: cfg
    b.get_soul = lambda: "Your name is Bracken."
    b.get_memory = lambda: ""
    b.get_model_options = lambda: {}
    b._loop = object()          # only ever handed to the stub below

    msg = _Message(sink=b.sent)

    class _Fut:
        """run_coroutine_threadsafe returns a Future; here the 'coroutine'
        has already been evaluated by _Channel.send, so hand it straight
        back."""

        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):
            return self.value

    import asyncio
    real_sched = asyncio.run_coroutine_threadsafe
    real_collect = llm_backend.chat_collect
    asyncio.run_coroutine_threadsafe = lambda coro, loop: _Fut(coro)
    llm_backend.chat_collect = _fake_chat_collect(
        model_says,
        stop_midway=((lambda: b._request_turn_stop(msg.channel.id))
                     if stop_midway else None))
    try:
        b._generate(msg, "hello there", None, None)
    finally:
        asyncio.run_coroutine_threadsafe = real_sched
        llm_backend.chat_collect = real_collect
    return b


# (a) the model returns literally nothing.
b = _run_generate([])
check("a model that says nothing still puts something in the channel",
      any("[no reply produced]" in s for s in b.sent))
check("...and the placeholder is NOT written into the kin's history",
      all("[no reply produced]" not in c for _r, c in b.persisted))
check("...so the only assistant turn stored is none at all",
      [r for r, _c in b.persisted if r == "assistant"] == [])

# (b) the model returns only its own name tag, which cleanup eats. This is
#     the commoner shape and the one that looks identical from the channel.
b = _run_generate(["[Bracken]: "])
check("a reply eaten by the name-tag cleanup is caught too",
      any("[no reply produced]" in s for s in b.sent))

# (c) an ordinary reply is unaffected -- the guard above must not have
#     changed the normal path.
b = _run_generate(["I'm here.", " What's up?"])
check("a normal reply still goes out unchanged",
      any("I'm here. What's up?" in s for s in b.sent))
check("...and is persisted, once",
      [c for r, c in b.persisted if r == "assistant"]
      == ["I'm here. What's up?"])

# (d) A STOPPED turn is not an empty reply. Someone asked it to stop; the kin
#     didn't fall silent. Half a sentence is what they chose to keep.
before = len(_log_text())
b = _run_generate(["I was going to say", " rather a lot more"],
                  stop_midway=True)
check("a stopped reply keeps the words that had arrived",
      [c for r, c in b.persisted if r == "assistant"]
      == ["I was going to say"])
check("...and is never dressed up as a failure in the channel",
      not any("[no reply produced]" in s for s in b.sent))
check("...and writes nothing to empty_replies.log, which diagnoses faults",
      len(_log_text()) == before)


# ── 2. the webcam gate ─────────────────────────────────────────────────

print("--- use_webcam cannot fire unasked from Discord ---")

msg = _Message()

# (a) an approval channel exists -> the tool is wrapped, and the wrapper
#     actually consults it rather than passing straight through.
asked = []
fired = []


def _approve(label, uid):
    asked.append((label, uid))
    return "deny"


bot = _Bot(request_webcam_approval=_approve)
wrapped = bot._wrap_webcam_for_discord(
    lambda args: fired.append(args) or "captured", msg)
out = wrapped({})
check("the wrapper asks the operator before anything happens", asked == [
      ("Wren", 7)])
check("...and a refusal means the camera never ran", fired == [])
check("...and the kin is told it was declined, in words it can read",
      "declined" in json.loads(out)["error"])

asked.clear()
fired.clear()
bot = _Bot(request_webcam_approval=lambda label, uid: "allow")
wrapped = bot._wrap_webcam_for_discord(
    lambda args: fired.append(args) or "captured", msg)
wrapped({"reason": "x"})
check("an approval does run the capture", fired == [{"reason": "x"}])

# "unavailable" is not "deny" -- nobody was shown the request, so telling
# the kin it was refused invents a decision the operator never made.
bot = _Bot(request_webcam_approval=lambda label, uid: "unavailable")
wrapped = bot._wrap_webcam_for_discord(lambda args: "captured", msg)
err = json.loads(wrapped({}))["error"]
check("an unreachable operator is reported as unreachable, not as a refusal",
      "nobody refused it" in err)

# (b) no approval channel -> the tool must be REMOVED from what the model is
#     offered, not handed over ungated. This is the same shape exec already
#     had; the check is on the code that builds the tool set.
import inspect  # noqa: E402
src = inspect.getsource(DiscordBot._generate)
check("with no approval channel wired, use_webcam is dropped from the executor",
      'if k != "use_webcam"' in src)
check("...and from the schemas, so the model is never even offered it",
      '!= "use_webcam"' in src)


# ── 3. the settings tab can express 'anyone' ───────────────────────────

print("--- the allow-list can say 'anyone', and 'nobody' is the default ---")

from kin_persistence import DEFAULT_DISCORD_CONFIG  # noqa: E402

check("out of the box, nobody can reach a kin on Discord",
      DEFAULT_DISCORD_CONFIG["allow_from"] == [])
check("...and the bot agrees that empty means nobody",
      DiscordBot._is_allowed(7, []) is False)
check("...while '*' means anyone", DiscordBot._is_allowed(7, ["*"]) is True)

# The validator that replaced the digits-only field. A star used to be
# silently dropped on its way to disk, which is why the config's own
# 'anyone' setting had no way in from the UI.
from dialogs.edit_kin import EditKinDialog  # noqa: E402

valid = EditKinDialog._dc_valid_user_id
check("a numeric Discord ID is accepted", valid("123456789012345678"))
check("a star is accepted -- this is the bug that made 'anyone' unreachable",
      valid("*"))
check("blank is rejected rather than quietly meaning everyone", not valid(""))
check("a name typed instead of an ID is rejected", not valid("Wren"))


print("--- the list says who can talk and what they can reach ---")


class _FakeList:
    """Stands in for the wx.ListBox. rebuild_listbox only needs .Set and the
    selection pair, so the row-building logic can be checked with no widgets
    and therefore no window stealing anyone's focus."""

    def __init__(self):
        self.items = []

    def Set(self, items):
        self.items = list(items)

    def GetSelection(self):
        return -1

    def SetSelection(self, i):
        pass


class _FakeDialog:
    """Just enough of EditKinDialog to run the real refresh."""
    _refresh_dc_users_list = EditKinDialog._refresh_dc_users_list

    def __init__(self, cfg):
        self.cfg = cfg
        self.dc_users_list = _FakeList()


d = _FakeDialog({"discord": {
    "allow_from": ["4001", "*"],
    "user_tools": {"4001": "read"},
}})
d._refresh_dc_users_list()
rows = d.dc_users_list.items

check("each person is one row", len(rows) == 2)
check("...showing what they can reach, not just their number",
      "tools: read" in rows[0] and "4001" in rows[0])
check("a star is spelled out rather than shown as a punctuation mark",
      "anyone" in rows[1])
check("...and someone with no bucket set reads as 'none', not as blank",
      "tools: none" in rows[1])
check("the row order matches the stored list, so the keys line up",
      d._dc_user_ids_in_order == ["4001", "*"])

d = _FakeDialog({"discord": {"allow_from": [], "user_tools": {}}})
d._refresh_dc_users_list()
check("an empty list is empty, not a phantom entry",
      d.dc_users_list.items == [])


print()
if _fails:
    print(f"test_discord_parity: {len(_fails)} FAILED")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("test_discord_parity: all checks passed")
