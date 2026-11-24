"""Discord surface for Hearthkin — a kin present in a Discord server.

Mirrors telegram_bot.TelegramBot's integration contract (same
get_config / get_soul / get_memory / get_model_options / on_status /
on_activity callbacks, its own start()/stop(), all inference off the
UI thread) so the frame wires it up the same way it wires Telegram.

Transport note: unlike Telegram (simple getUpdates long-poll over
plain HTTP), Discord requires a persistent Gateway WebSocket to
receive messages. Python's stdlib has no WebSocket client, so this
surface depends on `discord.py` — lazy-imported here so a Hearthkin
install that never enables Discord doesn't need it installed. If the
import fails, start() logs and no-ops; the app never crashes over a
missing optional dependency (stdlib-first dependency policy).

discord.py is asyncio-based; Hearthkin is threads + wx. So the client
runs its own event loop on a daemon thread (same shape as Telegram's
poll thread), and every blocking llm_backend.chat() call is pushed to
a thread-pool executor so it can never stall the Gateway heartbeat —
a stalled heartbeat gets the bot disconnected.

MVP scope (this slice): mention-only replies in whatever servers the
bot is in, one reply per mention, reusing llm_backend.chat() and the
same anti-impersonation cleanup as the Telegram group path. Per-channel
serialization keeps one kin from trying to generate two overlapping
replies in the same channel. NOT yet in this slice: conversation
history/persistence, tools, streaming, the model-gated ("chime in when
relevant") policy, and global backpressure across channels — all
tracked as follow-ups.
"""

import os
import re
import asyncio
import threading

from kin_persistence import append_failure_log

# Cap on image attachments pulled from one message, and per-image size.
DISCORD_MAX_IMAGES = 4
DISCORD_MAX_IMAGE_BYTES = 8 * 1024 * 1024

try:
    import discord
    DISCORD_AVAILABLE = True
except ImportError:
    discord = None
    DISCORD_AVAILABLE = False

# Discord's hard per-message character limit.
DISCORD_MSG_LIMIT = 2000


# Abuse throttles (parity with Telegram's flood handling). A single user
# can't force back-to-back generations faster than the cooldown, and no
# more than N generations run concurrently across all channels — so a
# mention-spammer across many channels can't saturate the Ollama host /
# thread pool (a cheap DoS / cost-amplification otherwise).
DISCORD_USER_COOLDOWN_SECS = 3.0
DISCORD_MAX_CONCURRENT_GENERATIONS = 3
# How many of one person's messages may wait their turn at once. Past this
# they're told, not silently dropped (12 Nov 2026).
DISCORD_MAX_WAITING_PER_PERSON = 3
# Discord cuts a message at 2,000 characters; a piece this long was cut by
# the client, so its continuation is on its way (see _coalesce).
DISCORD_SPLIT_LEN = 1990

# Matches a Discord user mention token: <@123> or <@!123> (the "!" form
# is the legacy nickname mention). Stripped from content before the
# message goes to the model so the kin doesn't see "<@1234> hey" — it
# just sees "hey".
_MENTION_RE = re.compile(r"<@!?\d+>")

# Words that end an in-flight reply when sent on their own: the one list,
# chat_helpers.is_stop_word, shared with Telegram. The whole message has to
# be the word, so "stop that, it's funny" is a remark to the kin, not a
# control instruction.
from chat_helpers import is_stop_word as _is_stop_word

# Typed commands, with / or ! in front (Discord's own command menu can get in
# the way of a leading /). Discord's own /shrug, /me and the like are turned
# into ordinary text by the Discord app before they're sent, so a bot never
# sees them. In a direct message an unknown command gets Telegram's answer
# ("Unknown command ... /help"); in a server channel it's left alone unless
# the kin is @-mentioned in it, since "!play" and the like there are often
# meant for another bot (21 Nov 2026).
_COMMANDS = frozenset({"help", "commands", "status", "regen", "regenerate",
                       "retry", "undo", "clear", "forget", "compact",
                       "front"})


def discord_test_token(token):
    """Check a bot token with Discord, as Telegram's "Test token" does with
    getMe: asks Discord who the token belongs to. Returns (ok, message).
    Needs no running bot and no discord library (21 Nov 2026)."""
    token = (token or "").strip()
    if not token:
        return False, "no token"
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur
    req = _ur.Request("https://discord.com/api/v10/users/@me", headers={
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBot (https://github.com, 1)"})
    try:
        with _ur.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read().decode("utf-8"))
        name = data.get("username") or "(bot)"
        return True, f"{name} (bot ID {data.get('id', '?')})"
    except _ue.HTTPError as e:
        if e.code == 401:
            return False, "Discord says this token isn't valid."
        return False, f"Discord answered {e.code}."
    except Exception as e:
        return False, f"Couldn't reach Discord: {e}"


def channel_entries(dcfg):
    """The server channels a kin is in: {channel id: {"label", "policy",
    "exclude"}}, from the Discord tab's channel list. A list kept the older
    way (a line of channel IDs) is read as channels answering on the kin's
    one policy, with nobody muted, and (as it was then) only people on the
    people list talking. Never raises."""
    out = {}
    try:
        d = dcfg if isinstance(dcfg, dict) else {}
        entries = d.get("channel_entries")
        if isinstance(entries, dict) and entries:
            for cid, e in entries.items():
                e = e if isinstance(e, dict) else {}
                pol = str(e.get("policy") or "mention_only").lower()
                out[str(cid)] = {
                    "label": str(e.get("label") or ""),
                    "policy": pol if pol in ("mention_only", "always")
                    else "mention_only",
                    "exclude": {str(x).strip() for x in (e.get("exclude") or [])
                                if str(x).strip()},
                }
            return out
        pol = str(d.get("policy") or "mention_only").lower()
        for cid in d.get("channels") or []:
            if str(cid).strip():
                # Kept as it was: in a list from before, only people on
                # the people list could talk, and that doesn't widen unasked.
                out[str(cid).strip()] = {"label": "", "policy": pol,
                                         "exclude": set(),
                                         "people_only": True}
    except Exception:
        return {}
    return out


def _unknown_command(content):
    """The word after / or ! when the message looks like a command that
    isn't one of ours, else None."""
    s = (content or "").strip()
    if len(s) < 2 or s[0] not in "/!" or not (s[1].isalpha()):
        return None
    if _parse_command(s) is not None or _is_stop_word(s):
        return None
    return s[1:].split(maxsplit=1)[0]


def _parse_command(content):
    """(name, rest) for a known command, else None."""
    s = (content or "").strip()
    if len(s) < 2 or s[0] not in "/!":
        return None
    parts = s[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    if name not in _COMMANDS:
        return None
    return name, (parts[1] if len(parts) > 1 else "")


def _cfg_count(cfg, key, default):
    """A non-negative whole-number setting, or the default if it's missing
    or not a number."""
    try:
        return max(0, int((cfg or {}).get(key, default)))
    except (TypeError, ValueError):
        return default


class DiscordBot:
    """Per-kin Discord bot. One live Gateway connection, mention-only
    replies, all inference off the event loop."""

    def __init__(self, agent_name, get_config, get_soul, get_memory,
                 get_model_options, on_status, on_activity=None,
                 wrap_exec=None, request_webcam_approval=None,
                 on_long_reply=None, get_busy_label=None,
                 transcribe_voice=None):
        # transcribe_voice(bytes) -> str: a voice message's words, with the
        # desktop's dictation model (voice_notes.py), as on Telegram.
        self.transcribe_voice = transcribe_voice
        # get_busy_label(skip_bot=None) -> str: what has the model right now
        # ("Bracken is writing a memory note"), or "" when nothing does. Same
        # contract as Telegram's; see _maybe_busy_notice.
        self.get_busy_label = get_busy_label
        self.agent_name = agent_name
        self.get_config = get_config
        self.get_soul = get_soul
        self.get_memory = get_memory
        self.get_model_options = get_model_options
        self.on_status = on_status
        # on_activity(kind, identifier): fired after a turn so the frame
        # can tick per-(kin, scope) distillation counters, same as
        # Telegram. kind="discord", identifier=channel_id. Safe as None.
        self.on_activity = on_activity
        # on_long_reply(kin, surface, who, count): see TelegramBot.
        self.on_long_reply = on_long_reply
        # wrap_exec(executors, agent_name) -> executors: the frame's
        # exec-approval wrapper. A shell command a Discord user triggers
        # must pop approval on the OPERATOR's desktop (never the server),
        # which needs the frame's wx plumbing — so it's injected. When
        # None, exec is dropped from the tool set rather than run raw.
        self._wrap_exec = wrap_exec
        # request_webcam_approval(label, user_id) -> "allow"/"deny"/
        # "unavailable". Same frame helper the Telegram surface uses: it
        # marshals a wx dialog onto the UI thread and blocks this worker
        # until the operator answers. `use_webcam` sits in the WRITE bucket,
        # so the moment a Discord user is granted write access the tool is
        # callable — and unlike exec it has a physical-world effect. Without
        # this hook the tool is DROPPED rather than fired unasked.
        self._request_webcam_approval = request_webcam_approval
        self._surface_label = "discord"

        self._loop = None            # the asyncio loop, set on the thread
        self._client = None          # discord.Client, set on the thread
        self._thread = None
        self._stop = threading.Event()
        # Per-channel asyncio.Locks so two mentions in the same channel
        # serialize instead of racing two generations at once. Created
        # lazily on the event loop (asyncio primitives are loop-bound).
        self._channel_locks = {}
        # Direct-message channels seen this run. A DM kept with the desktop
        # is part of the private side, like a shared Telegram DM; its rows
        # are stored marked so (chat_helpers.place_of, 20 Nov 2026).
        self._dm_channels = set()
        # Abuse throttles: per-user cooldown timestamps + a global bounded
        # concurrency semaphore (created lazily on the loop). See the
        # DISCORD_USER_COOLDOWN_SECS / DISCORD_MAX_CONCURRENT_GENERATIONS
        # constants.
        self._user_last_reply = {}
        self._gen_semaphore = None
        # One-time warning latch: if allow_from is empty (deny-by-default)
        # the bot silently ignores everyone, which would look like a broken
        # bot to a blind operator. Surface it once via on_status.
        self._warned_empty_allow = False
        # In-flight replies, keyed by channel id: {channel_id: {"who": str,
        # "stop": bool}}. Guarded by a plain threading.Lock rather than an
        # asyncio one because it is read from BOTH sides — the Gateway event
        # loop (to honour a stop, and to answer it) and the desktop's UI
        # thread (to answer "what would quitting abandon?").
        self._active_turns = {}
        self._turn_lock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────────

    def start(self):
        """Spin up the Gateway connection on a daemon thread. No-op (with
        a logged reason) if discord.py isn't installed or no token is set."""
        if not DISCORD_AVAILABLE:
            self._status("Discord unavailable: the 'discord.py' package "
                         "isn't installed.")
            append_failure_log(
                "discord_failures.log", self.agent_name,
                "start", RuntimeError("discord.py not installed"))
            return
        token = ((self.get_config() or {}).get("discord", {})
                 .get("bot_token", "") or "").strip()
        if not token:
            self._status("Discord not started: no bot token set.")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(token,), daemon=True)
        self._thread.start()
        self._status("Discord connecting...")

    def stop(self):
        """Ask the client to close and let the loop wind down. Best-effort;
        the thread is a daemon so process exit never blocks on it."""
        self._stop.set()
        # Ask any reply mid-generation to wind down rather than being cut off
        # mid-call. The frame asks before quitting, so by the time this runs
        # the person has already chosen not to wait.
        self.stop_all_turns()
        client, loop = self._client, self._loop
        if client is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop)
            except Exception:
                pass

    # ── event loop / client ────────────────────────────────────────

    def _run(self, token):
        """Thread entry: own asyncio loop, own client, run until closed."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        intents = discord.Intents.default()
        # Privileged intent — MUST also be toggled on in the Discord
        # Developer Portal (Bot → Privileged Gateway Intents → Message
        # Content). Without it message.content arrives empty and the kin
        # sees nothing to reply to.
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            self._status(f"Discord connected as {client.user}.")

        @client.event
        async def on_message(message):
            await self._on_message(client, message)

        try:
            loop.run_until_complete(client.start(token))
        except Exception as e:
            self._status(f"Discord error: {e}")
            append_failure_log("discord_failures.log", self.agent_name,
                               "gateway", e)
        finally:
            try:
                loop.run_until_complete(client.close())
            except Exception:
                pass
            loop.close()
            self._status("Discord disconnected.")

    # ── in-flight turns: stopping one, and reporting one ───────────

    def _begin_turn(self, channel_id, who, author_id=None):
        """Register a reply as being written. Clears any stale stop flag so a
        'stop' typed during an earlier turn can't kill this one. Keeps WHOSE
        reply it is, so a message they send mid-reply can reach it."""
        with self._turn_lock:
            self._active_turns[channel_id] = {
                "who": who, "stop": False,
                "author_id": None if author_id is None else str(author_id),
                "held": []}

    def _hold_for_turn(self, channel_id, author_id, text):
        """Keep a message for the reply running in this channel, if that
        reply is this person's. Returns True when held."""
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            if not entry or entry.get("author_id") != str(author_id):
                return False
            entry.setdefault("held", []).append(text)
            return True

    def _take_interjections(self, channel_id):
        """The interject poll, called from inside the tool loop (same
        contract as Telegram's): hand over what the person said mid-reply."""
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            held = list(entry.get("held") or []) if entry else []
            who = (entry or {}).get("who") or ""
            author_id = (entry or {}).get("author_id")
            if entry:
                entry["held"] = []
        # The kin is sent "[time] [Name] words" (the one shared rule); the
        # row stored keeps the words and the account apart (_persist_turn).
        import datetime
        from chat_helpers import person_row_text
        try:
            kin_cfg = self.get_config() or {}
        except Exception:
            kin_cfg = {}
        out = []
        for t in held:
            row = {"role": "user", "content": t, "sender_name": who,
                   "ts": datetime.datetime.now().isoformat(timespec="seconds")}
            if author_id is not None:
                row["sender_id"] = str(author_id)
            out.append({**row, "content": person_row_text(
                row, kin_cfg, "discord")[0], "said": t, "interjected": True})
        return out

    def _take_leftovers(self, channel_id):
        """Messages held for a reply that ended before taking another step.
        _end_turn sets them aside; the message handler answers them next, so
        nothing sent mid-reply is ever lost."""
        with self._turn_lock:
            return self.__dict__.setdefault("_leftovers", {}).pop(channel_id, [])

    def _end_turn(self, channel_id):
        with self._turn_lock:
            entry = self._active_turns.pop(channel_id, None)
            held = list((entry or {}).get("held") or [])
            if held:
                self.__dict__.setdefault("_leftovers", {}).setdefault(
                    channel_id, []).extend(held)

    def _turn_cancelled(self, channel_id):
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            return bool(entry and entry.get("stop"))

    def _needs_no_mention(self, message):
        """Messages that reach the kin in a mentions-only channel without an
        @mention, as on Telegram: a stop or a command, and the next piece of
        a message Discord split, while the first is waiting to be joined.
        These used to be dropped first for having no mention (audit, 20 Nov
        2026). A follow-up from the person whose reply is running is NOT one
        of them any more: in a channel they may be talking to someone else,
        as Telegram groups already assume (21 Nov 2026)."""
        try:
            text = _MENTION_RE.sub("", message.content or "").strip()
            if _parse_command(text) is not None:
                return True
            cid, aid = message.channel.id, message.author.id
            with self._turn_lock:
                entry = self._active_turns.get(cid)
            # A bare "stop" only while a reply is running here: in a busy
            # channel people say "stop" to each other.
            if entry and _is_stop_word(text):
                return True
            return (cid, aid) in (self.__dict__.get("_coalescing") or {})
        except Exception:
            return False

    def _may_explain_refusal(self, author_id):
        """True at most once per person every ten minutes."""
        import time as _t
        times = getattr(self, "_unauth_reply_times", None)
        if times is None:
            times = self._unauth_reply_times = {}
        now = _t.monotonic()
        last = times.get(str(author_id))
        if last is not None and now - last < 600.0:
            return False
        if len(times) > 256:
            for k in [k for k, v in times.items() if now - v >= 600.0]:
                times.pop(k, None)
        times[str(author_id)] = now
        return True

    def _request_turn_stop(self, channel_id, author_id=None):
        """Ask the reply in this channel to stop. Returns True when there was
        one to stop, so the caller can say something true either way.

        Scoped to the CHANNEL, which is the unit a Discord reply belongs to —
        one channel's stop must not reach into another's. And, as on
        Telegram, only the person the reply is for, or the kin's own person,
        can stop it: in a shared channel anyone could stop anyone's reply
        (audit, 20 Nov 2026). `author_id` None (the desktop's stop) always
        may."""
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            if not entry:
                return False
            if author_id is not None and not self._may_stop(entry, author_id):
                return False
            entry["stop"] = True
            return True

    def _memory_ok(self, message, author_id):
        """Whether a memory block from a kin with no tools is acted on this
        turn. In a direct message, yes. In a server channel, only for you or
        someone whose own tools could write: anyone else's turn must not
        steer the kin's memory through a block (22 Nov 2026)."""
        if getattr(message, "guild", None) is None:
            return True
        if self._is_me(author_id):
            return True
        try:
            from tools._person_settings import person_value
            dcfg = (self.get_config() or {}).get("discord") or {}
            return person_value(dcfg, "discord", author_id,
                                "bucket") in ("write", "full")
        except Exception:
            return False

    def _is_me(self, author_id):
        """The account is marked "This is me" on the Discord tab."""
        try:
            from tools._person_settings import person_value
            dcfg = (self.get_config() or {}).get("discord") or {}
            return bool(person_value(dcfg, "discord", author_id, "is_me"))
        except Exception:
            return False

    def _may_stop(self, entry, author_id):
        if str(entry.get("author_id")) == str(author_id):
            return True
        try:
            from tools._person_settings import person_value
            dcfg = (self.get_config() or {}).get("discord") or {}
            return bool(person_value(dcfg, "discord", author_id, "is_me"))
        except Exception:
            return False

    def _announce_long_reply(self, channel_id, count):
        """Tell the desktop, once, that a reply here is running long."""
        cb = getattr(self, "on_long_reply", None)
        if cb is None:
            return
        with self._turn_lock:
            entry = self._active_turns.get(channel_id) or {}
        who = entry.get("who") or "someone"
        try:
            cb(self.agent_name, "Discord", who, count)
        except Exception:
            pass

    def stop_any_turn(self):
        """Stop every reply this bot is writing (operator's desktop command,
        Chat -> Stop every reply). Returns a human line, or None."""
        label = self.active_turn_label()
        with self._turn_lock:
            if not self._active_turns:
                return None
            for entry in self._active_turns.values():
                entry["stop"] = True
        if label and " is part-way through a reply" in label:
            return label.replace(" is part-way through a reply", "'s reply", 1)
        if label and " is part-way through " in label:
            return label.replace(" is part-way through ", "'s ", 1)
        return f"{self.agent_name}'s replies on Discord"

    def active_turn_label(self):
        """One human line naming a reply being written right now, or None.

        Read by the desktop's confirm-on-close check, which had no way at all
        to see this surface: quitting mid-reply abandoned somebody's
        conversation in silence. Same contract as TelegramBot's, so the frame
        treats both the same way."""
        with self._turn_lock:
            active = list(self._active_turns.values())
        if not active:
            return None
        if len(active) == 1:
            who = active[0].get("who") or "someone"
            return (f"{self.agent_name} is part-way through a reply to "
                    f"{who} on Discord")
        return (f"{self.agent_name} is part-way through {len(active)} replies "
                f"on Discord")

    def stop_all_turns(self):
        """Ask every in-flight reply to stop. Called on shutdown so worker
        threads inside a model call wind down rather than being abandoned."""
        with self._turn_lock:
            for entry in self._active_turns.values():
                entry["stop"] = True

    # ── message handling ───────────────────────────────────────────

    async def _on_message(self, client, message):
        if getattr(message, "guild", None) is None:
            try:
                self._dm_channels.add(str(message.channel.id))
            except Exception:
                pass
        # Never reply to ourselves or to other bots (a two-bot loop in a
        # server would generate forever).
        if message.author.id == client.user.id or message.author.bot:
            return

        cfg = self.get_config() or {}
        dcfg = cfg.get("discord", {}) or {}
        in_dm = getattr(message, "guild", None) is None

        # Server channels work like Telegram groups (20 Nov 2026): the kin
        # talks in the channels on its list, with everyone there except the
        # people muted in that channel, each channel answering on its own
        # terms. The people list is for direct messages. Before, one people
        # list decided everywhere and one policy covered every channel, the
        # opposite of Telegram's design for the same thing.
        listed = channel_entries(dcfg)
        if not in_dm and listed:
            entry = listed.get(str(message.channel.id))
            if entry is None:
                return  # not a channel the kin is in
            if str(message.author.id) in entry["exclude"]:
                return  # muted here
            if entry.get("people_only") and not self._is_allowed(
                    message.author.id, dcfg.get("allow_from")):
                return  # a channel listed the older way: people list only
            if (entry["policy"] == "mention_only"
                    and client.user not in message.mentions
                    and not self._needs_no_mention(message)):
                return  # not spoken to
        else:
            policy = (dcfg.get("policy") or "mention_only").lower()
            # A direct message is spoken to the kin by definition (12 Nov
            # 2026): it used to need an @-mention too under the default
            # policy, which a voice message can't carry at all.
            addressed = (client.user in message.mentions or in_dm
                         or self._needs_no_mention(message))
            if policy == "mention_only" and not addressed:
                return  # not spoken to — stay quiet on a busy server

            # Location gate, for servers only: a direct message is in no
            # server, and listing servers used to shut direct messages off
            # without a word (audit, 20 Nov 2026).
            guilds = [str(x) for x in (dcfg.get("guilds") or [])
                      if str(x).strip()]
            if guilds and not in_dm:
                gid = getattr(getattr(message, "guild", None), "id", None)
                if gid is None or str(gid) not in guilds:
                    return

        # Access control for direct messages, and for server channels when no
        # channel is listed (deny-by-default). Empty allow_from = nobody;
        # "*" = anyone; otherwise only listed user IDs. Silent ignore in a
        # channel, but the empty-allow_from case is surfaced once via
        # on_status so it doesn't look like a dead bot.
        allow_from = dcfg.get("allow_from")
        if (in_dm or not listed) and not self._is_allowed(
                message.author.id, allow_from):
            allow = [str(x) for x in (allow_from or []) if str(x).strip()]
            if not allow and not self._warned_empty_allow:
                self._warned_empty_allow = True
                self._status(
                    "Discord: no allow_from configured, so every message is "
                    "ignored (deny-by-default for safety). Add Discord user "
                    "IDs, or \"*\" to allow anyone, in the kin's Discord "
                    "settings.")
            # In a direct message, say why and how to be added, as Telegram
            # does (a silent drop leaves the person no way to ask); at most
            # once per person every ten minutes. Never in a channel.
            if in_dm and self._may_explain_refusal(message.author.id):
                await self._send_chunked(
                    message.channel,
                    f"Hi! You're not on {self.agent_name}'s list yet, so I "
                    f"can't chat with you. Your Discord user ID is "
                    f"{message.author.id}. Share that with the kin's operator "
                    f"and ask to be added in Hearthkin's kin settings, on the "
                    f"Discord tab.")
            return

        content = _MENTION_RE.sub("", message.content or "").strip()
        # A voice message becomes words (12 Nov 2026; it was ignored without
        # a word). In a server channel too, since 20 Nov 2026: by here it has
        # passed the same gates as a typed message (spoken to, or a channel
        # where the kin answers everything; an allowed person in an allowed
        # place), so nothing is fetched for a message the kin won't answer.
        voice = self._voice_attachment(message)
        if voice is not None:
            heard = await self._hear_voice(message, voice)
            if heard is None:
                return
            import voice_notes
            content = voice_notes.as_message(heard, content)
        # A picture or file sent with no words is still a message: it used
        # to be dropped here, before the downloads below (audit, 20 Nov 2026).
        if not content and not getattr(message, "attachments", None):
            return
        # Any other file (a song, a recording, a video, a PDF): tell the kin
        # what came, and keep a sound where it can listen to it, as Telegram
        # does (reading_bridge.file_arrival_note). It used to be invisible.
        for note in await self._other_file_notes(message, voice):
            content = (content + "\n\n" + note) if content else note

        # A stop, handled BEFORE the per-channel lock and before the cooldown.
        # Both would otherwise swallow it: the lock is held by the very reply
        # being stopped, and someone typing "stop" twice is exactly the person
        # who most needs the second one to land. Nothing else on this surface
        # could end a reply — an unstoppable multi-minute generation against a
        # slow local model is the "nothing stops it but quitting" shape this
        # app keeps closing.
        #
        # Safe to do here because, unlike Telegram's single poll thread, the
        # Gateway loop keeps running throughout: inference is handed to an
        # executor, so this handler is never blocked by the reply it stops.
        if _is_stop_word(content):
            with self._turn_lock:
                running = message.channel.id in self._active_turns
            if self._request_turn_stop(message.channel.id, message.author.id):
                await self._send_chunked(
                    message.channel,
                    "Stopped. Keeping what was written so far.")
            elif running:
                await self._send_chunked(
                    message.channel,
                    "That reply is for someone else, so only they can stop "
                    "it.")
            else:
                await self._send_chunked(
                    message.channel,
                    "Nothing is being written here just now.")
            return

        # Commands, before the mid-reply hold and the joining below, so
        # "/undo" is never read as more words for the kin.
        command = _parse_command(content)
        if command is not None:
            await self._handle_command(message, *command)
            return
        _unknown = _unknown_command(content)
        if _unknown is not None and getattr(message, "guild", None) is None:
            await self._send_chunked(
                message.channel,
                f"Unknown command: /{_unknown}. Try /help to see what's "
                "available.")
            return
        if (_unknown is not None
                and client.user not in (getattr(message, "mentions", None)
                                        or [])):
            # A command for another bot ("!play ...") in a server channel:
            # not the kin's business unless it's named in it. Left alone,
            # neither answered nor sent to the kin (21 Nov 2026).
            return

        # A plain message from the person whose reply is running in this
        # channel reaches that reply before its next step, as on Telegram
        # (12 Nov 2026). It used to wait behind the reply it was about, so
        # "wait, that's wrong" was only read after the wrong thing was done.
        # Attachments keep their usual path. If the reply takes no further
        # step, the message is answered straight after (_end_turn keeps it).
        # Direct messages only, as on Telegram: in a server channel the
        # person whose reply is running may be talking to someone else, and
        # pulling that into the kin's turn (and posting "Got it" there) would
        # be wrong (21 Nov 2026).
        if (getattr(message, "guild", None) is None
                and not getattr(message, "attachments", None)
                and self._hold_for_turn(
                    message.channel.id, message.author.id, content)):
            await self._send_chunked(
                message.channel,
                f"Got it. {self.agent_name} will see this before its next "
                f"step, or straight after this reply if there isn't one.")
            return

        # A message split across several sends is put back together first,
        # as on Telegram. Attachments keep their own path. A piece merged
        # into a message already waiting has nothing more to do here.
        if not getattr(message, "attachments", None):
            content = await self._coalesce(message, content)
            if content is None:
                return

        # Per-person pace. A follow-up within the cooldown used to be thrown
        # away without a word. Now it waits out the rest of the cooldown and
        # is answered; past a few waiting at once, the person is TOLD, so a
        # flood is still bounded but nothing vanishes silently (12 Nov 2026).
        import time
        uid = message.author.id
        waiting = self.__dict__.setdefault("_waiting", {})
        if waiting.get(uid, 0) >= DISCORD_MAX_WAITING_PER_PERSON:
            await self._send_chunked(
                message.channel,
                "I've already got a few of your messages waiting, so I "
                "skipped this one. Send it again once I've caught up.")
            return
        waiting[uid] = waiting.get(uid, 0) + 1
        try:
            await self._maybe_busy_notice(message)
            gap = time.monotonic() - self._user_last_reply.get(uid, 0.0)
            if gap < DISCORD_USER_COOLDOWN_SECS:
                await asyncio.sleep(DISCORD_USER_COOLDOWN_SECS - gap)
            self._user_last_reply[uid] = time.monotonic()

            # Download any attached images up front (only when the kin's model
            # can actually see them). Cleaned up after the turn either way.
            image_paths = await self._download_images(message)
            # Text documents (.txt/.md/source) were previously skipped by the
            # image filter and dropped without a word — "check this out"
            # arrived with nothing attached. Unlike images these need no
            # model support.
            text_docs = await self._download_text_documents(message)
            await self._generate_in_turn(message, content, image_paths,
                                         text_docs)
            # Anything said mid-reply that the reply never got to: answer it
            # now, as one message, rather than lose it.
            leftovers = self._take_leftovers(message.channel.id)
            while leftovers:
                await self._generate_in_turn(
                    message, "\n\n".join(leftovers), [], [])
                leftovers = self._take_leftovers(message.channel.id)
        finally:
            waiting[uid] = max(0, waiting.get(uid, 1) - 1)
        if self.on_activity:
            try:
                # Where this conversation is stored decides which memory
                # counter ticks: the same per-person, per-channel rule as
                # the storage itself (audit, 20 Nov 2026).
                self.on_activity("discord", message.channel.id,
                                 self._shares_desktop(message.author.id,
                                                      message.channel.id))
            except Exception:
                pass

    def _message_wait_secs(self):
        """How long to listen for the rest of a message: Discord's own
        "message_wait_secs", on the Discord tab. It read Telegram's until 23
        Nov 2026, a setting merged across two surfaces. Junk falls back to 2
        seconds."""
        cfg = self.get_config() or {}
        raw = (cfg.get("discord") or {}).get("message_wait_secs", 2)
        try:
            secs = float(raw)
        except (TypeError, ValueError):
            return 2.0
        if secs != secs:
            return 2.0
        return max(0.0, min(secs, 600.0))

    @staticmethod
    def _join_parts(prev_text, next_text):
        """Same seam rule as Telegram: a newline between pieces, except
        where whitespace is already there or a piece hit the length limit
        (the cut may be mid-word)."""
        if not prev_text:
            return next_text
        if not next_text:
            return prev_text
        if prev_text[-1].isspace() or next_text[0].isspace():
            return prev_text + next_text
        if len(prev_text) >= DISCORD_SPLIT_LEN:
            return prev_text + next_text
        return prev_text + "\n" + next_text

    async def _coalesce(self, message, content):
        """Put a message split across several sends back together, as
        Telegram does (12 Nov 2026): Discord cuts long messages at 2,000
        characters, and people type one thought across two quick lines.

        The first piece waits a moment; pieces from the same person in the
        same channel that arrive meanwhile join it, and each one restarts
        the wait (longer after a piece cut at the length limit). Returns
        the whole text for the first piece, or None for a piece that was
        merged into a message already waiting (nothing more to do)."""
        key = (message.channel.id, message.author.id)
        open_ = self.__dict__.setdefault("_coalescing", {})
        if key in open_:
            entry = open_[key]
            entry["parts"].append(content)
            entry["last"] = asyncio.get_running_loop().time()
            entry["long"] = len(content) >= DISCORD_SPLIT_LEN
            return None
        loop = asyncio.get_running_loop()
        entry = {"parts": [content], "last": loop.time(),
                 "long": len(content) >= DISCORD_SPLIT_LEN}
        open_[key] = entry
        try:
            while len(entry["parts"]) < 16:
                window = self._message_wait_secs()
                if entry["long"]:
                    window = max(window, 8.0)
                remaining = entry["last"] + window - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.25))
        finally:
            open_.pop(key, None)
        text = entry["parts"][0]
        for part in entry["parts"][1:]:
            text = self._join_parts(text, part)
        return text

    async def _maybe_busy_notice(self, message):
        """Say once, when this person's message is waiting behind the app's
        own background work (a memory write, a wake-up, another reply), so
        a held-up kin doesn't look like one ignoring them. Telegram has had
        this; Discord's constructor never got the callback (12 Nov 2026).

        Same rules as Telegram's: only when something is actually named;
        not for this kin's own reply in this same channel (they just sent
        what it's answering); once per busy stretch per channel. The message
        still goes through either way."""
        if not getattr(self, "get_busy_label", None):
            return
        channel_id = message.channel.id
        sent = self.__dict__.setdefault("_busy_notice_sent", set())
        with self._turn_lock:
            skip_bot = (self.agent_name
                        if channel_id in self._active_turns else None)
        try:
            label = (self.get_busy_label(skip_bot) or "").strip()
        except Exception:
            label = ""
        if not label:
            sent.discard(channel_id)
            return
        if channel_id in sent:
            return
        sent.add(channel_id)
        try:
            await self._send_chunked(
                message.channel,
                f"{label}. Your message is in the queue and will be answered "
                f"when that finishes, as long as Hearthkin stays open.")
        except Exception:
            pass

    async def _generate_in_turn(self, message, content, image_paths, text_docs):
        """One reply, under the global concurrency cap and this channel's
        lock. Together they bound total in-flight generations so a spammer
        across many channels can't saturate the backend."""
        if self._gen_semaphore is None:
            self._gen_semaphore = asyncio.Semaphore(
                DISCORD_MAX_CONCURRENT_GENERATIONS)
        lock = self._channel_locks.setdefault(
            message.channel.id, asyncio.Lock())
        async with self._gen_semaphore, lock:
            try:
                # _generate streams the reply into the channel itself (and
                # persists it), so there's nothing to send back here.
                async with message.channel.typing():
                    await self._loop.run_in_executor(
                        None, self._generate, message, content, image_paths,
                        text_docs)
            except Exception as e:
                append_failure_log("discord_failures.log", self.agent_name,
                                   f"channel={message.channel.id}", e)
                # In plain words, as on Telegram; the raw text can carry file
                # paths and provider bodies, so only the kin's own person in
                # a direct message sees the details. It used to post the raw
                # error in any channel, public ones included (audit, 20 Nov
                # 2026).
                from chat_helpers import humanize_error
                from tools._person_settings import person_value
                dcfg = (self.get_config() or {}).get("discord") or {}
                mine = (getattr(message, "guild", None) is None
                        and person_value(dcfg, "discord",
                                         getattr(message.author, "id", ""),
                                         "is_me"))
                await self._send_chunked(message.channel, "⚠️ " + humanize_error(
                    e, kin=self.agent_name, redact=not mine))
            finally:
                for p in image_paths:
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    def _dm_mark(self, channel_id):
        """{"dm": True} for a direct-message channel, else {}."""
        dms = getattr(self, "_dm_channels", None) or set()
        return {"dm": True} if str(channel_id) in dms else {}

    def _channel_history(self, channel_id, share):
        """This channel's prior turns, model-shaped (role + content only),
        everything since the last compaction, as on Telegram (its own store
        is capped as Telegram's is, history_rows.trim_front; the newest 40 used to be all that was
        sent, 21 Nov 2026). `share` picks the store:

          share=True  — MERGED: filter the unified conversation.jsonl for
                        rows tagged source=discord:<channel_id>.
          share=False — SEGREGATED: read the channel's slice of the
                        standalone discord_history.json.

        Merged: a direct message sees the private side (desktop, shared
        DMs, wake-ups, and groups switched on for it); a server channel sees
        itself, plus the private side only if switched on for it. Before
        20 Nov 2026 it was this channel ONLY, so it never saw desktop or
        another channel."""
        if share:
            # Kept with the desktop: a direct message is the private side,
            # like a shared Telegram DM; a server channel is its own place,
            # plus the private side only if switched on for it
            # (chat_helpers.visible_rows, 20 Nov 2026).
            from kin_persistence import load_agent_config, load_agent_conversation
            from chat_helpers import PRIVATE, visible_rows
            dm = str(channel_id) in (getattr(self, "_dm_channels", None) or set())
            try:
                rows = visible_rows(
                    load_agent_conversation(self.agent_name) or [],
                    PRIVATE if dm else f"discord:{channel_id}",
                    load_agent_config(self.agent_name) or {},
                    own_place=f"discord:{channel_id}")
            except Exception:
                return []
        else:
            from kin_persistence import load_discord_history
            try:
                rows = (load_discord_history(self.agent_name) or {}).get(
                    str(channel_id)) or []
            except Exception:
                return []
        # A compacted conversation is read from its latest note onward
        # (/compact); nothing before it was deleted.
        from chat_helpers import view_after_compaction
        rows = view_after_compaction(rows)
        from chat_helpers import person_row_text
        try:
            _kin_cfg = self.get_config() or {}
        except Exception:
            _kin_cfg = {}
        # Since 12 Nov 2026 the kin gets back its own tool steps and the
        # notes Hearthkin gave it ("you described it but didn't do it", "your
        # reply came back empty"), as on every other surface. Only plain
        # messages used to come back, so a Discord kin never saw what its
        # tools did last time, and never received a single steering note.
        turns = []
        for m in rows:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role == "assistant" and m.get("tool_calls"):
                turns.append({"role": "assistant", "content": content or "",
                              "tool_calls": m["tool_calls"]})
            elif role == "tool" and m.get("tool_call_id"):
                t = {"role": "tool", "content": content or "",
                     "tool_call_id": m["tool_call_id"]}
                if m.get("name"):
                    t["name"] = m["name"]
                turns.append(t)
            elif role == "user" and content and isinstance(content, str):
                # "[time] [Name] words": names, marks and times kept for
                # every person's message, Discord's own and other places'
                # (it used to send their words bare). chat_helpers.
                turns.append({"role": "user", "content": person_row_text(
                    m, _kin_cfg, "discord")[0]})
            elif role in ("user", "assistant", "system") and content:
                t = {"role": role, "content": content}
                # Fed back only when the kin feeds its reasoning back, as on
                # the desktop and Telegram.
                if (role == "assistant" and m.get("thinking")
                        and _kin_cfg.get("feed_thinking", False)):
                    t["thinking"] = m["thinking"]
                turns.append(t)
        # A trimmed store can start between a call and its results; a tool
        # result whose call scrolled away can't be sent (providers reject it).
        while turns and turns[0].get("role") == "tool":
            turns.pop(0)
        # "Tool history kept" (Settings, Tools), as on the desktop.
        try:
            from chat_helpers import compact_tool_history
            keep = int((self.get_config() or {}).get("tool_history_keep", 5) or 0)
            turns = compact_tool_history(turns, keep)
        except Exception:
            pass
        return turns

    def _persist_turn(self, channel_id, role, content, share, said=None,
                      speaker=None, ts=None, thinking=None):
        """Store one turn in whichever store `share` selects (merged
        conversation.jsonl vs segregated discord_history.json).

        `speaker`: {"sender_id", "sender_name"} for a person's message. The
        name is stored apart from the words, as on Telegram, and put in
        front of them when read (chat_helpers.person_row_text), so a later
        label or "This is me" applies to it. Before 21 Nov 2026 the name and
        its mark were baked into the stored words."""
        import datetime
        turn = {"role": role, "content": content,
                "ts": ts or datetime.datetime.now().isoformat(
                    timespec="seconds")}
        if said is not None:
            turn["said"] = said
        if thinking:
            turn["thinking"] = thinking
        if speaker:
            turn.update({k: v for k, v in speaker.items()
                         if k in ("sender_id", "sender_name", "fronting")
                         and v not in (None, "")})
        if share:
            from kin_persistence import append_agent_conversation_turn
            append_agent_conversation_turn(
                self.agent_name,
                {**turn, **self._dm_mark(channel_id),
                 "source": f"discord:{channel_id}"})
        else:
            from kin_persistence import append_discord_turn
            append_discord_turn(self.agent_name, channel_id, turn)

    def _persist_person_row(self, channel_id, turn, share):
        """Store a person's message the loop handed back (one said mid-
        reply): the words and the speaker, never the name-prefixed text the
        kin was sent."""
        speaker = {"sender_id": turn.get("sender_id"),
                   "sender_name": turn.get("sender_name")}
        words = turn.get("said")
        if not isinstance(words, str):
            words = turn.get("content") or ""
        self._persist_turn(channel_id, "user", words, share, said=words,
                           speaker=speaker, ts=turn.get("ts"))

    def _shares_desktop(self, author_id, channel_id=None):
        """Whether this Discord talk is kept with the desktop's: the
        person's own box in the person window (the old kin-wide box is the
        fallback for someone saved before per-person sharing existed), or
        the channel has either of its sharing switches on (20 Nov 2026), so
        what's said there can reach, or be reached from, the private side."""
        from tools._person_settings import person_value
        dcfg = (self.get_config() or {}).get("discord") or {}
        if channel_id is not None:
            cid = str(channel_id)
            if ((dcfg.get("channel_private_sees") or {}).get(cid)
                    or (dcfg.get("channel_sees_private") or {}).get(cid)):
                return True
        return bool(person_value(dcfg, "discord", author_id, "share_desktop",
                                 fallback=dcfg.get("share_desktop", False)))

    # ── commands: help, status, regen, undo, clear (12 Nov 2026) ────
    #
    # Telegram has had these all along; Discord had only "stop". Same words,
    # same rules: help works anywhere, the rest only in a direct message,
    # because in a server channel the conversation belongs to everyone there
    # and one person's /clear would wipe it for all of them.

    def _cut_from_last_user(self, channel_id, share, look_only=False):
        """Remove this channel's last message from a person, and everything
        after it in this channel. Returns the removed message, or None when
        there was nothing to remove. Other channels, the desktop's own rows
        and Telegram's are never touched. look_only: find it, change nothing."""
        from kin_persistence import (
            _get_conversation_lock, load_agent_conversation,
            save_agent_conversation, load_discord_history,
            save_discord_history)
        with _get_conversation_lock(self.agent_name):
            if share:
                src = f"discord:{channel_id}"
                conv = list(load_agent_conversation(self.agent_name) or [])
                ours = [i for i, m in enumerate(conv)
                        if isinstance(m, dict) and m.get("source") == src]
                users = [i for i in ours if conv[i].get("role") == "user"]
                if not users:
                    return None
                cut = users[-1]
                if look_only:
                    return conv[cut]
                kept = conv[:cut] + [
                    m for m in conv[cut:]
                    if not (isinstance(m, dict) and m.get("source") == src)]
                save_agent_conversation(self.agent_name, kept)
                return conv[cut]
            data = load_discord_history(self.agent_name) or {}
            rows = list(data.get(str(channel_id)) or [])
            users = [i for i, m in enumerate(rows)
                     if isinstance(m, dict) and m.get("role") == "user"]
            if not users:
                return None
            cut = users[-1]
            if look_only:
                return rows[cut]
            data[str(channel_id)] = rows[:cut]
            from kin_persistence import load_discord_rows_added
            from history_rows import note_end_cut
            counts = load_discord_rows_added(self.agent_name)
            note_end_cut(counts, str(channel_id), len(rows), len(rows) - cut)
            save_discord_history(self.agent_name, data, rows_added=counts)
            return rows[cut]

    def _clear_channel(self, channel_id, share):
        """Wipe this channel's conversation. Returns how many messages went."""
        # What it takes away is copied first (23 Nov 2026: no clear loses a
        # conversation). If the copy can't be written this raises and
        # nothing is cleared.
        from kin_persistence import (
            _get_conversation_lock, load_agent_conversation,
            save_agent_conversation, load_discord_history,
            save_discord_history, keep_cleared_copy)
        with _get_conversation_lock(self.agent_name):
            if share:
                src = f"discord:{channel_id}"
                conv = list(load_agent_conversation(self.agent_name) or [])
                kept, going = [], []
                for m in conv:
                    (going if isinstance(m, dict) and m.get("source") == src
                     else kept).append(m)
                if going:
                    keep_cleared_copy(self.agent_name,
                                      f"discord-{channel_id}", going)
                    save_agent_conversation(self.agent_name, kept)
                return len(going)
            data = load_discord_history(self.agent_name) or {}
            going = list(data.get(str(channel_id)) or [])
            if going:
                keep_cleared_copy(self.agent_name,
                                  f"discord-{channel_id}", going)
                data.pop(str(channel_id), None)
                save_discord_history(self.agent_name, data)
            return len(going)

    # /compact, as on Telegram and the desktop (12 Nov 2026): the kin writes
    # itself a note, the person reads it here, "/compact confirm" puts a
    # marker with it into this channel's conversation, and from then on the
    # kin reads the note and the last few messages. Nothing is deleted.
    COMPACT_KEEP = 6

    async def _cmd_compact(self, message, arg, share):
        from chat_helpers import compact_marker, compact_keep_count, compact_sent_words
        try:
            from kin_persistence import load_agent_config
            _keep = compact_keep_count(load_agent_config(self.agent_name))
        except Exception:
            _keep = self.COMPACT_KEEP
        from kin_persistence import load_app_prompt, now_iso
        ch = message.channel
        pending = self.__dict__.setdefault("_pending_compact", {})
        word = (arg or "").strip().lower()
        who = self.agent_name
        user = self._compact_user_name(message)
        if word == "confirm":
            note = pending.pop(ch.id, None)
            if not note:
                await self._send_chunked(
                    ch, f"There's no note waiting. Send /compact and {who} "
                        "will write one first.")
                return
            preface = load_app_prompt("compact_marker", who).replace(
                "{user}", user)
            row = compact_marker(note, _keep, ts=now_iso(),
                                 preface=preface, kin=who)
            from kin_persistence import save_now_note
            save_now_note(who, note)
            if share:
                from kin_persistence import append_agent_conversation_turn
                append_agent_conversation_turn(
                    who, {**row, **self._dm_mark(ch.id), "source": f"discord:{ch.id}"})
            else:
                from kin_persistence import append_discord_turn
                append_discord_turn(who, ch.id, row)
            await self._send_chunked(
                ch, f"Compacted. From now on {who} reads "
                    f"{compact_sent_words(_keep)} here. Nothing was deleted.")
            return
        if word not in ("", "again"):
            await self._send_chunked(
                ch, "Send /compact to have the note written, then "
                    "/compact confirm to compact with it.")
            return
        await self._send_chunked(
            ch, f"Asking {who} to write its note. This can take a moment.")
        err = ""
        try:
            async with ch.typing():
                note = await self._loop.run_in_executor(
                    None, self._compact_note, ch.id, share, user)
        except Exception as e:
            note, err = "", str(e)
        if not note:
            pending.pop(ch.id, None)
            await self._send_chunked(
                ch, f"{who} didn't write a note"
                    + (f" ({err})" if err else "")
                    + ". Nothing was changed; you can try /compact again.")
            return
        pending[ch.id] = note
        await self._send_chunked(
            ch, f"Here's the note {who} wrote for after:\n\n{note}\n\n"
                "Send /compact confirm to compact with it, or /compact again "
                "for a new one. Anything else leaves it as it was. Nothing "
                "is deleted either way.")

    def _compact_user_name(self, message):
        from tools._person_settings import person_value
        dcfg = (self.get_config() or {}).get("discord") or {}
        uid = getattr(message.author, "id", "")
        name = (person_value(dcfg, "discord", uid, "label")
                or getattr(message.author, "display_name", "") or "")
        return name.strip() or "the person you talk with"

    def _compact_note(self, channel_id, share, user):
        """BLOCKING. Ask the kin for its note: soul and memory, this
        channel's conversation as it's read now, and the request. No tools."""
        import llm_backend
        from kin_persistence import (build_system_prompt, load_app_prompt,
                                     resolve_kin_ollama_host)
        from chat_helpers import extract_inline_thinking
        cfg = self.get_config() or {}
        got = self.get_model_options()
        options = (got[1] if isinstance(got, tuple) and len(got) == 2
                   else got) or {}
        if not isinstance(options, dict):
            options = {}
        num_ctx = int(options.get("num_ctx") or 0) or 8192
        msgs = [{"role": "system", "content": build_system_prompt(
            self.get_soul() or "", self.get_memory() or "",
            enabled_tools=[], kin_name=self.agent_name)}]
        for m in self._channel_history(channel_id, share):
            if m.get("role") in ("user", "assistant", "system") \
                    and not m.get("tool_calls") and m.get("content"):
                msgs.append({"role": m["role"], "content": m["content"]})
        from kin_persistence import compact_request_text
        msgs.append({"role": "user", "content": compact_request_text(
            self.agent_name, user)})
        from chat_helpers import compact_options
        r = llm_backend.chat_collect(
            cfg.get("model", ""), msgs, options=compact_options(options, cfg),
            max_context_tokens=max(512, num_ctx - 2000),
            kin_name=self.agent_name, surface="compact",
            ollama_host=resolve_kin_ollama_host(
                cfg.get("ollama_host_name", "")))
        note, _ = extract_inline_thinking(getattr(r, "content", "") or "",
                                          getattr(r, "thinking", "") or "")
        return (note or "").strip()

    @staticmethod
    def _words_of(row):
        """What the person actually said in a stored message, without the
        name Hearthkin put in front of it for the kin."""
        said = row.get("said")
        if isinstance(said, str):
            return said.strip()
        return re.sub(r"^\[[^\]\n]{1,100}\] ", "",
                      row.get("content") or "", count=1).strip()

    def _help_text(self, in_dm):
        if not in_dm:
            return (
                f"Commands {self.agent_name} answers to here:\n"
                "/help: this message\n"
                "/front: say who's fronting here (/front off clears it)\n"
                "stop: end the reply being written, keeping what's written "
                "so far\n\n"
                "The others (/status, /regen, /undo, /clear, /compact) "
                "work in a "
                f"direct message with {self.agent_name}. Here, the "
                "conversation belongs to everyone in the channel.")
        return (
            f"Commands for {self.agent_name}:\n"
            "/help: this message\n"
            "/status: model, your tool access, and whether this talk is "
            "shared with the desktop\n"
            "/regen: write the last reply again\n"
            "/undo: take back your last message and its reply\n"
            "/clear: wipe this conversation (memory is kept)\n"
            "/compact: the kin writes itself a note and carries on from it "
            "and the last few messages (nothing is deleted)\n"
            "/front: say who's fronting here (/front off clears it)\n"
            "stop: end the reply being written, keeping what's written "
            "so far\n"
            "You can start them with ! instead of / if Discord gets in "
            "the way.")

    def _status_text(self, message, share):
        from tools._person_settings import person_value
        cfg = self.get_config() or {}
        dcfg = cfg.get("discord") or {}
        uid = getattr(message.author, "id", "")
        bucket = person_value(dcfg, "discord", uid, "bucket")
        if isinstance(bucket, str) and bucket:
            access = bucket
        elif bucket:
            access = "a custom list"
        else:
            access = "none (chat only)"
        lines = [f"Kin: {self.agent_name}",
                 f"Model: {cfg.get('model') or '(not set)'}",
                 f"Your tool access: {access}",
                 "This conversation: " + (
                     "shared with the Hearthkin desktop" if share
                     else "kept on Discord only"),
                 "Messages it remembers here: "
                 f"{len(self._channel_history(message.channel.id, share))}"]
        try:
            import llm_backend
            real = llm_backend.last_reported_prompt_tokens(self.agent_name)
        except Exception:
            real = None
        if real:
            lines.append(f"Last send used {real} tokens of context")
        return "\n".join(lines)

    async def _handle_command(self, message, name, arg):
        """One command. Returns nothing; every path answers in the channel."""
        ch = message.channel
        in_dm = getattr(message, "guild", None) is None
        if name in ("help", "commands"):
            await self._send_chunked(ch, self._help_text(in_dm))
            return
        if name == "front":
            # Who's fronting (fronting.py), in a channel too: per
            # conversation, only from the person's own account, answered
            # by Hearthkin, not the kin.
            if not self._is_me(getattr(message.author, "id", "")):
                await self._send_chunked(ch, "Only an account marked \"This "
                                         "is me\" can set who's fronting.")
                return
            import fronting
            await self._send_chunked(ch, fronting.reply_line(
                arg, self.agent_name, f"discord:{ch.id}"))
            return
        if not in_dm:
            await self._send_chunked(
                ch,
                f"/{name} only works in a direct message with "
                f"{self.agent_name}. Here, the conversation belongs to "
                "everyone in the channel. /help works anywhere.")
            return
        with self._turn_lock:
            busy = ch.id in self._active_turns
        if busy:
            await self._send_chunked(
                ch,
                f"{self.agent_name} is still writing a reply here. Say stop, "
                f"or wait for it to finish, then send /{name} again.")
            return
        share = self._shares_desktop(getattr(message.author, "id", ""),
                                     message.channel.id)
        if name == "status":
            await self._send_chunked(ch, self._status_text(message, share))
        elif name == "undo":
            gone = self._cut_from_last_user(ch.id, share)
            if gone is None:
                await self._send_chunked(
                    ch, "Nothing to undo. There's no earlier message here.")
            else:
                await self._send_chunked(
                    ch,
                    f"Undone. {self.agent_name} won't see your last message "
                    "or its reply." + (
                        " This conversation is shared with the desktop, so "
                        "they're gone from there too." if share else ""))
        elif name in ("clear", "forget"):
            if share and arg.strip().lower() != "confirm":
                await self._send_chunked(
                    ch,
                    "This conversation is shared with the Hearthkin desktop, "
                    "so clearing it takes these messages away there too. "
                    "A copy is kept on the computer, and "
                    f"{self.agent_name}'s memory stays.\n"
                    "To go ahead, send: /clear confirm")
                return
            try:
                self._clear_channel(ch.id, share)
            except Exception:
                await self._send_chunked(
                    ch,
                    "Couldn't clear: a copy couldn't be kept first, so "
                    "nothing was taken away.")
                return
            await self._send_chunked(
                ch,
                f"Cleared. {self.agent_name} won't remember anything said "
                "here before this. A copy is kept on the computer. Its "
                "memory is untouched.")
        elif name == "compact":
            await self._cmd_compact(message, arg, share)
        elif name in ("regen", "regenerate", "retry"):
            last = self._cut_from_last_user(ch.id, share, look_only=True)
            if last is None:
                await self._send_chunked(
                    ch, "Nothing to write again. There's no earlier message "
                        "here.")
                return
            words = self._words_of(last)
            if not words:
                await self._send_chunked(
                    ch, "Can't write that again: the last message had no "
                        "words to send.")
                return
            self._cut_from_last_user(ch.id, share)
            # The reply is written fresh, as if the message had just come in.
            await self._generate_in_turn(message, words, [], [])

    def _generate(self, message, content, image_paths=None, text_docs=None):
        """BLOCKING — runs in the executor, never on the event loop.
        Loads this channel's prior turns for context, persists the new user
        turn (before generating, so a failure never orphans it), generates,
        persists the reply — into the merged or segregated store per the
        kin's Discord `share_desktop` setting — then runs the same anti-
        impersonation cleanup the Telegram group path uses.

        image_paths: local temp files for images the user attached (already
        filtered to the model-supports-images case by the caller). Attached
        to THIS turn's user message only — they're ephemeral (deleted after
        the turn), so the persisted history stays text-only."""
        import llm_backend
        from kin_persistence import (
            build_system_prompt, resolve_kin_ollama_host,
            sanitize_for_prompt_literal, load_kin_tools, load_app_prompt)
        from tools._buckets import filter_tool_names
        from chat_helpers import (
            clean_kin_reply, extract_inline_thinking)

        cfg = self.get_config() or {}
        dcfg = cfg.get("discord") or {}
        soul = self.get_soul() or ""
        memory = self.get_memory() or ""
        # The frame's get_model_options returns a (model, options) PAIR, the
        # same contract Telegram unpacks. This line used to take the pair
        # itself as the options, so every tool loop died converting a tuple
        # to a dict, and plain chat quietly dropped the reply cap and the
        # context window (chat() ignores non-dict options). A bare dict is
        # still accepted so an older caller can't reintroduce the crash.
        got = self.get_model_options()
        if isinstance(got, tuple) and len(got) == 2:
            options = got[1] or {}
        else:
            options = got or {}
        if not isinstance(options, dict):
            options = {}
        model = cfg.get("model", "")
        channel_id = message.channel.id
        # Per-person settings, from the one list Telegram uses too
        # (tools/_person_settings.py). Sharing used to be one box for the
        # whole kin on Discord; that box is now the fallback for a person
        # saved before per-person sharing existed.
        from tools._person_settings import person_value
        author_id = getattr(message.author, "id", "")
        share = self._shares_desktop(author_id, channel_id)

        # Inline attribution, no colon — same anti-impersonation shape as
        # the Telegram group path (a "[Name]:" token is a speaker-turn
        # attractor; the no-colon bracket isn't). Display name is
        # attacker-controllable, so sanitize control chars first.
        # Through the SHARED helper, not a bracket built here. It produces the
        # same shape today, which is exactly why a second hand-rolled copy is
        # dangerous: the no-colon rule is an anti-impersonation defence, and a
        # copy is what keeps the old shape when the rule next changes. Every
        # surface that names a speaker to a model goes through one function.
        # The name you gave this person wins over the one Discord shows.
        who = sanitize_for_prompt_literal(
            person_value(dcfg, "discord", author_id, "label")
            or getattr(message.author, "display_name", None)
            or message.author.name)
        # Stored: the words, the account, and the name Discord showed, kept
        # apart (21 Nov 2026; the name and mark used to be baked into the
        # words). Sent: "[time] [Name] words", labelled and marked by
        # account (the kin's own person is known by account, never by name:
        # anyone in a server can call themselves anything), through the one
        # rule every surface uses.
        import datetime as _dt
        from chat_helpers import person_row_text
        _speaker = {"sender_id": str(author_id),
                    "sender_name": sanitize_for_prompt_literal(
                        getattr(message.author, "display_name", None)
                        or message.author.name)}
        if self._is_me(author_id):
            import fronting
            fronting.stamp(_speaker, self.agent_name, f"discord:{channel_id}")
        _ts = _dt.datetime.now().isoformat(timespec="seconds")
        user_text, who_for_kin = person_row_text(
            {"role": "user", "content": content, "ts": _ts, **_speaker},
            cfg, "discord")

        history = self._channel_history(channel_id, share)
        # The names this reply was shown, so one that opens as one of them is
        # caught (audit, 20 Nov 2026: Discord never passed them).
        from chat_helpers import speakers_shown
        _shown = speakers_shown(history, also=(who_for_kin,))

        # Persist the user turn NOW, before generating — if generation
        # fails the message is still on record, never orphaned. `said` keeps
        # the words without the name in front, so /regen can send them again.
        self._persist_turn(channel_id, "user", content, share, said=content,
                           speaker=_speaker, ts=_ts)

        # Load the kin's tools, gated by THIS user's bucket — the same
        # (kin tools.json) ∩ (per-user bucket) intersection the Telegram
        # surface enforces. A user not listed in discord.user_tools
        # defaults to 'none' (chat only), so write_file/edit_file/note/exec
        # are never handed to an arbitrary Discord member. exec is
        # additionally routed to the operator's desktop for approval
        # (self._wrap_exec); if no approval path is wired, exec is dropped
        # rather than run unguarded.
        schemas, executor = [], {}
        # A "*" entry (anyone) now really covers anyone without their own,
        # as the person window always said; it used to be ignored here.
        bucket = person_value(dcfg, "discord", author_id, "bucket")
        _kin_tools = load_kin_tools(self.agent_name) or []
        enabled = filter_tool_names(_kin_tools, bucket)
        # Your own direct message ("This is me"): you can ask the kin to write
        # to another kin, as on the desktop and Telegram (22 Nov 2026). Never
        # in a server channel. Only for a kin that uses tools at all.
        _me_dm = (getattr(message, "guild", None) is None
                  and self._is_me(author_id))
        if _me_dm and _kin_tools and "message_kin" not in enabled:
            enabled = list(enabled) + ["message_kin"]
        if enabled:
            from tools import load_tools
            # confine_paths revokes the absolute-path escape hatch for file
            # tools on this remote surface (audit D1). Confined by DEFAULT;
            # `remote_unconfined_files` in the kin config (Settings -> Tools
            # -> Tool settings) hands this kin desktop-equivalent reach.
            # Read from the kin config, never from anything the model sets.
            # Per person since 12 Nov 2026; the kin-wide box otherwise.
            from tools._person_settings import resolve_file_reach
            _confine = resolve_file_reach(dcfg, "discord", author_id, cfg)
            # What memory search may reach for this person (18 Nov 2026).
            from tools._person_settings import resolve_said_scope
            _said = resolve_said_scope(dcfg, "discord", author_id,
                                       f"discord:{channel_id}")
            schemas, executor = load_tools(
                enabled,
                context={"agent_name": self.agent_name,
                         "confine_paths": _confine,
                         "said_scope": _said},
                model=model,
                person_present=_me_dm)
            if self._wrap_exec is not None:
                executor = self._wrap_exec(executor, self.agent_name,
                                           user_id=str(author_id))
            elif "exec" in executor:
                executor = {k: v for k, v in executor.items() if k != "exec"}
                schemas = [s for s in schemas
                           if s.get("function", {}).get("name") != "exec"]
            # Same discipline for use_webcam. It rides in the WRITE bucket
            # alongside write_file/note, so granting a Discord member write
            # access would otherwise hand them the host's camera with no gate
            # at all — the one tool here with a consequence in the room the
            # operator is sitting in. Approval pops on the OPERATOR's desktop,
            # never in the server, exactly like exec. No approval channel
            # wired → drop the tool rather than fire it unasked.
            # The person's webcam setting, same three answers as Telegram:
            # always refuse, take it without asking, or ask the operator.
            _cam = person_value(dcfg, "discord", author_id, "webcam")
            if "use_webcam" in executor:
                if _cam == "deny":
                    import json as _json
                    executor = dict(executor)
                    executor["use_webcam"] = lambda _a: _json.dumps({
                        "ok": False,
                        "error": "Webcam capture refused for this user "
                                 "(per-user webcam permission is set to "
                                 "'always deny')."})
                elif _cam == "auto":
                    pass        # their window says take it without asking
                elif self._request_webcam_approval is not None:
                    executor = dict(executor)
                    executor["use_webcam"] = self._wrap_webcam_for_discord(
                        executor["use_webcam"], message)
                else:
                    executor = {k: v for k, v in executor.items()
                                if k != "use_webcam"}
                    schemas = [s for s in schemas
                               if s.get("function", {}).get("name")
                               != "use_webcam"]
        tool_names = [s["function"]["name"] for s in schemas]

        # enabled_tools=tool_names gates the base prompt's tool/memory
        # scaffolding to exactly what the kin can call here.
        system = build_system_prompt(soul, memory, enabled_tools=tool_names,
                                     kin_name=self.agent_name)
        # Where the kin is, as a Telegram group says (chat_helpers.place_note):
        # the channel's display label, else Discord's own name for it. A
        # direct message adds nothing.
        if getattr(message, "guild", None) is not None:
            from chat_helpers import place_note
            _entry = channel_entries(dcfg).get(str(channel_id)) or {}
            system += place_note("discord", _entry.get("label")
                                 or getattr(message.channel, "name", "")
                                 or f"channel {channel_id}")
        # This turn's tools and blocks, from the one place every surface
        # asks (block_hints, 22 Nov 2026). Discord used to send only the tool
        # list, so it saved fenced files, ran park lines and kept no-tools
        # memory blocks without ever saying any of that was possible.
        import block_hints
        system += block_hints.for_turn(
            self.agent_name,
            "discord_dm" if getattr(message, "guild", None) is None
            else "discord_channel",
            tool_names, person_present=_me_dm,
            memory_ok=self._memory_ok(message, author_id))
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        # Attached text documents go in front of the user's turn — same
        # placement as the desktop shared-files path — so the kin reads the
        # file, then reads what the operator said about it.
        if text_docs:
            try:
                import reading_bridge
                block = reading_bridge.build_attachment_context_block(
                    text_docs, self.agent_name)
                if block:
                    messages.append({"role": "system", "content": block})
            except Exception as e:
                append_failure_log("discord_failures.log", self.agent_name,
                                   "text attachment injection failed", e)
        # Another kin may have written (kin_mail): in the person's own DM
        # it's handed over here, before their message, and kept in the kin's
        # conversation, as on Telegram and the desktop (22 Nov 2026).
        # Parts of the kin manual it asked for come the same way, for anyone
        # (reply_actions).
        try:
            import reply_actions
            _mail = reply_actions.before_message(self.agent_name,
                                                 mail_in_chat=_me_dm)
            if _mail:
                messages.append({"role": "system", "content": _mail})
        except Exception as e:
            append_failure_log("kin_messages.log", self.agent_name,
                               "deliver on Discord", e)
        user_msg = {"role": "user", "content": user_text}
        if image_paths:
            user_msg["attachments"] = list(image_paths)
        messages.append(user_msg)

        num_ctx = int(cfg.get("num_ctx", 8192) or 8192)

        # Per-turn memory recall — the same one-line integration the desktop,
        # Telegram and cron paths already use. Discord was the only
        # conversational surface without it, so a kin here reached its own
        # depth logs ONLY if it thought to call a memory tool. That is exactly
        # what smaller models don't do, and putting the relevant notes in
        # front of a kin unasked is the whole reason recall exists.
        #
        # Fail-soft by construction: any problem leaves `messages` exactly as
        # it was. A reply without recall is worth more than no reply.
        self._last_recall_used = []
        try:
            from memory_recall import inject_into_messages
            # Tell recall which brackets are OURS. Every Discord turn is
            # stored as "[display name] text", so without this the sender's
            # name sits in every message the matcher reads, and a depth log
            # named after that person qualifies on EVERY turn regardless of
            # what was actually said. Same failure the Telegram path had.
            # Names are read from what we bracketed, never matched by shape —
            # a bracket a person typed themselves is their own words.
            _bracketed = {who}
            for _m in history:
                if (_m.get("role") or "") != "user":
                    continue
                _hit = re.match(r"\s*\[([^\]]{1,64})\]\s", _m.get("content") or "")
                if _hit:
                    _bracketed.add(_hit.group(1))
            messages, self._last_recall_used = inject_into_messages(
                messages, self.agent_name,
                num_ctx=num_ctx, cfg=cfg,
                speaker_names=sorted(_bracketed))
        except Exception:
            pass

        common = dict(
            options=options, cache=bool(cfg.get("cache", True)),
            cache_ttl=str(cfg.get("cache_ttl", "auto")),
            show_thinking=bool(cfg.get("show_thinking", False)),
            max_context_tokens=max(num_ctx - 2000, 1000),
            kin_name=self.agent_name,
            ollama_host=resolve_kin_ollama_host(
                cfg.get("ollama_host_name", "")),
        )
        channel = message.channel
        # In-place streaming: the reply fills into ONE message as tokens
        # arrive, then finalize() overwrites it with the cleaned text.
        # Reuses the Telegram stream editor — it treats the message handle
        # opaquely, so a Discord Message works fine as that handle, and it
        # inherits the streamed-reply cutoff fix + its test coverage.
        # Throttled well clear of Discord's message-edit rate limit.
        from telegram_bot import _TelegramStreamEditor

        def _send(text):
            try:
                return asyncio.run_coroutine_threadsafe(
                    channel.send((text or "…")[:DISCORD_MSG_LIMIT]),
                    self._loop).result(timeout=20)
            except Exception:
                return None

        def _edit(handle, text):
            if handle is None:
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    handle.edit(content=(text or "…")[:DISCORD_MSG_LIMIT]),
                    self._loop).result(timeout=20)
            except Exception:
                pass

        def _stream_clean(t):
            # Shape the live text the way the finished message will be shaped,
            # so the streamed reply only ever GROWS. Without it a model that
            # opens with its own name tag shows the tag and then has it edited
            # away, and a tool call mid-reply would take back the sentence in
            # front of it — see _TelegramStreamEditor, which owns that rule.
            from chat_helpers import live_visible
            return clean_kin_reply(live_visible(t), self.agent_name,
                                   known_speakers=_shown)[0]

        ed = _TelegramStreamEditor(_send, _edit, throttle_secs=4.0,
                                   max_len=DISCORD_MSG_LIMIT,
                                   clean=_stream_clean)
        # Filled by the tool loop's turn hook below; read by the empty-reply
        # salvage after cleanup. Plain list so the closure can append.
        intermediate_seen = []
        tool_names_called = []

        # Register the reply as in flight. Two readers: the Gateway loop, so a
        # "stop" typed in this channel can reach the model call; and the
        # desktop's confirm-on-close, so quitting says what it would abandon.
        self._begin_turn(channel_id, who,
                         author_id=getattr(message.author, "id", None))
        # This person's tool-call ceiling (their own number if one is set,
        # else the kin's), kept with the turn so the model call can read it.
        try:
            from tools._buckets import resolve_tool_call_cap
            _cap = resolve_tool_call_cap(
                (cfg.get("discord") or {}).get("user_tool_caps"),
                getattr(message.author, "id", ""),
                # Their own number, else the kin's. Sharing with the desktop
                # doesn't lift it; limits and sharing are separate things.
                _cfg_count(cfg, "remote_max_tool_calls", 15))
            with self._turn_lock:
                if channel_id in self._active_turns:
                    self._active_turns[channel_id]["tool_cap"] = _cap
        except Exception:
            pass
        try:
            text, thinking, added_turns = self._run_model(
                schemas, executor, model, messages, cfg, common, ed, channel,
                channel_id, intermediate_seen, tool_names_called)
        finally:
            # Read the flag BEFORE clearing it — _end_turn discards it so a
            # stop can't leak into the next turn in this channel.
            turn_stopped = self._turn_cancelled(channel_id)
            self._end_turn(channel_id)

        # Snapshot before cleanup, for the empty-reply log below: the whole
        # diagnostic value is in seeing what the model returned versus what
        # survived the anti-impersonation passes.
        # Did the reply stop at its length cap? Asked on this thread, before
        # anything else calls a model (chat_helpers.take_cut_off).
        from chat_helpers import take_cut_off
        _cut_off = take_cut_off() and not turn_stopped
        raw_model_content = text
        # Keep reasoning that came inside the reply text with the rest of
        # it (it was thrown away; audit, 20 Nov 2026).
        text, thinking = extract_inline_thinking(text, thinking)
        text, _imp = clean_kin_reply(text, self.agent_name,
                                     known_speakers=_shown)
        text = (text or "").strip()

        # Empty after cleanup. This used to fall off the end of the method:
        # nothing sent, nothing persisted, nothing logged — from the channel
        # it was indistinguishable from the kin choosing to ignore you, and
        # there was no file anywhere to check afterwards. Both other surfaces
        # have handled this for a long time; Discord simply never got it.
        #
        # A STOPPED turn is not an empty reply and must not be recorded as
        # one: the kin didn't fall silent, someone asked it to stop. So no
        # salvage, no placeholder, and nothing written to empty_replies.log,
        # which exists to diagnose faults and would otherwise fill up with
        # our own interruptions.
        if not text and not turn_stopped:
            salvaged = ""
            for candidate in reversed(intermediate_seen):
                cand, _ = extract_inline_thinking(candidate, "")
                cand, _ = clean_kin_reply(cand, self.agent_name,
                                          known_speakers=_shown)
                cand = (cand or "").strip()
                if cand:
                    salvaged = cand
                    break
            self._log_empty_reply(
                model=model, channel_id=channel_id,
                user_id=getattr(message.author, "id", ""),
                raw_content=raw_model_content,
                post_cleanup=text,
                intermediate_content=salvaged,
                tool_calls_made=tool_names_called,
                salvaged=bool(salvaged),
            )
            if salvaged:
                text = salvaged
            else:
                placeholder = "[no reply produced]"
                if not ed.finalize(placeholder):
                    self._post_sync(channel, placeholder)
                # Deliberately NOT persisted. A placeholder in the history is
                # a turn the kin reads back as something it said, and it then
                # explains or apologises for it. The log is the record here.
                # What IS kept (12 Nov 2026, as on Telegram): the tool steps
                # it took, and a note telling it the reply came back empty,
                # so it can pick up next turn instead of not knowing.
                for _turn in added_turns:
                    if isinstance(_turn, dict) and _turn.get("role") in (
                            "assistant", "tool"):
                        self._persist_tool_turn(channel_id, _turn, share)
                    elif (isinstance(_turn, dict)
                          and _turn.get("role") == "user"
                          and _turn.get("interjected")):
                        self._persist_person_row(channel_id, _turn, share)
                try:
                    from kin_persistence import load_app_prompt
                    self._persist_turn(
                        channel_id, "system",
                        # The placeholder WAS posted, so the note that says
                        # so (the group one says nothing was posted at all;
                        # audit, 20 Nov 2026).
                        load_app_prompt("empty_reply_note",
                                        self.agent_name), share)
                except Exception:
                    pass
                return ""

        if not text:
            # Stopped before a single word arrived. Nothing to show and
            # nothing to store — but say so, or the stop looks like a crash.
            self._post_sync(channel, "[stopped before anything was written]")
            return ""

        # Write the cleaned reply into the streamed message. If that edit
        # fails (e.g. rate-limited after many edits), re-send the whole
        # thing fresh — better a possible duplicate than a message frozen
        # mid-stream (the exact rule from the Telegram cutoff fix).
        # Name the memories recall surfaced, the way Telegram does. Without it
        # a kin drawing on the wrong note is indistinguishable from a kin
        # being odd — recall ran here and its result was even stored on the
        # bot, but nothing ever showed it. Per-kin opt-out.
        # "_used: ..._", the receipt of which tools really ran, as on
        # Telegram (chat_helpers.tool_summary_footer, 21 Nov 2026).
        from chat_helpers import tool_summary_footer
        shown = text + tool_summary_footer(added_turns, dcfg)
        if dcfg.get("show_recall_summary", True):
            names = []
            for used in (self._last_recall_used or []):
                rel = (used.get("relpath") if isinstance(used, dict) else "") or ""
                if rel and rel not in names:
                    names.append(rel)
            if names:
                shown = shown + "\n\n_recalled: " + ", ".join(names) + "_"
        if _cut_off:
            from chat_helpers import CUT_OFF_LINE
            shown = shown + "\n\n" + CUT_OFF_LINE

        if not ed.finalize(shown):
            self._post_sync(channel, shown)
        # "Show reasoning in chat" reached the model call and stopped there
        # here too: the kin thought, and the thinking was discarded. Its own
        # message, never folded into the streamed reply — that message may
        # only grow, and this belongs before the answer it explains.
        if cfg.get("show_thinking", False):
            import turn_steering
            _block = turn_steering.reasoning_block(
                thinking, cfg=cfg, hard_cap=DISCORD_MSG_LIMIT - 200)
            if _block:
                self._post_sync(channel, _block)
        # Tool round-trips, stored so the kin can see what it DID last turn,
        # and any message the person sent mid-reply, in the order they
        # happened: BEFORE the final reply (12 Nov 2026; they used to be
        # stored after it, so they'd have read back out of order).
        for _turn in added_turns:
            if not isinstance(_turn, dict):
                continue
            if _turn.get("role") in ("assistant", "tool"):
                self._persist_tool_turn(channel_id, _turn, share)
            elif _turn.get("role") == "user" and _turn.get("interjected"):
                self._persist_person_row(channel_id, _turn, share)

        # The footer is for the reader, not the record — storing it would put
        # the harness's bookkeeping into the kin's own voice.
        from chat_helpers import thinking_to_save
        self._persist_turn(channel_id, "assistant", text, share,
                           thinking=thinking_to_save(thinking, cfg) or None)
        if _cut_off and text:
            from chat_helpers import cut_off_note_row
            self._persist_turn(channel_id, "system", cut_off_note_row(
                self.agent_name)["content"], share)

        # Suggest compacting once a day when it's full (18 Nov 2026). Direct
        # messages only: /compact doesn't work in a server channel.
        try:
            if getattr(message, "guild", None) is None:
                from chat_helpers import compact_hint
                _hint = compact_hint(self.agent_name, cfg,
                                     f"discord:{channel_id}",
                                     "Send /compact to compact it.")
                if _hint:
                    self._post_sync(channel, _hint)
        except Exception:
            pass

        # The steering notes every other surface gives back. All four are
        # shared code (turn_steering) rather than a fourth copy of the same
        # judgement — see that module for why. Best-effort throughout: a
        # steering fault must never cost a reply that has already gone out.
        try:
            import turn_steering
            notes = []
            # Same folder rule as this surface's file tools: a fenced save
            # must not reach where write_file was deliberately stopped.
            # Read from the kin config, never from anything the model sets.
            from tools._person_settings import resolve_file_reach
            _all = self.get_config() or {}
            _bridge_confine = resolve_file_reach(
                _all.get("discord") or {}, "discord",
                getattr(message.author, "id", ""), _all)
            # A kin with no write tools saves memory through this bridge; in a
            # server channel only on your turn or a writer's (_memory_ok).
            _aid = getattr(message.author, "id", "")
            if ({"write_file", "edit_file"} & set(tool_names or [])
                    or self._memory_ok(message, _aid)):
                kin_note, human_note = turn_steering.authoring_bridge_notes(
                    self.agent_name, text, tool_names, confine=_bridge_confine)
            else:
                kin_note, human_note = None, None
            if kin_note:
                notes.append(kin_note)
            if human_note and self._block_lines_on():
                self._post_sync(channel, human_note)
            if not kin_note:
                # Only when the bridge found nothing to commit: a kin whose
                # fenced write was just SAVED is not gesturing, and telling it
                # off for the thing that worked is worse than saying nothing.
                note = turn_steering.roleplay_corrective_note(
                    self.agent_name, text, tool_names, added_turns,
                    surface=self._surface_label, model=model)
                if note:
                    notes.append(note)
            note = turn_steering.read_gesture_note(
                self.agent_name, text, tool_names, added_turns,
                shared_this_turn=bool(text_docs))
            if note:
                notes.append(note)
            # In the person's own DM, a ```to:Name block writes to that kin,
            # as on the desktop and Telegram (kin_mail). Never in a channel.
            # A ```manual block works for anyone (reply_actions).
            import reply_actions
            _me_dm_here = (getattr(message, "guild", None) is None
                           and self._is_me(getattr(message.author, "id", "")))
            _block_lines = []
            notes.extend(reply_actions.after_reply(
                self.agent_name, text, kin_mail_ok=_me_dm_here,
                prompted=True, shown=_block_lines)[0])
            # What the blocks did, told to the person too (23 Nov 2026),
            # under "Show what reply blocks did".
            if _block_lines and self._block_lines_on():
                self._post_sync(channel, chr(10).join(_block_lines))
            for note in notes:
                self._persist_turn(channel_id, "system", note, share)
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "turn steering", e)
        # Text-in/text-out park bridge (per-kin `park` setting = chat|keeper):
        # a `> command` in the reply runs. In a direct message, the kin's
        # whole turn (play_turn, as on the desktop and in Telegram private
        # chats); in a server channel, one move. See _route_park_command.
        # After the reply, so a park error can never cost a message that
        # already went out.
        park_ask = None
        park_msgs = list(messages) + [{"role": "assistant", "content": text}]
        if getattr(message, "guild", None) is None:
            def park_ask():
                # The move's result is already in park_msgs (on_note below),
                # so the kin answers the ground truth, not its own guess.
                r = llm_backend.chat_collect(
                    model, park_msgs,
                    should_stop=lambda: self._turn_cancelled(channel_id),
                    surface=self._surface_label, **common)
                nxt = (getattr(r, "content", "") or "").strip()
                nxt, _ = extract_inline_thinking(nxt, "")
                nxt = (nxt or "").strip()
                if not nxt or getattr(r, "stopped", False):
                    return ""
                park_msgs.append({"role": "assistant", "content": nxt})
                self._post_sync(channel, nxt)
                self._persist_turn(channel_id, "assistant", nxt, share)
                return nxt
        self._route_park_command(
            text, channel, channel_id, share, ask=park_ask, who=who,
            author_id=getattr(message.author, "id", None),
            on_note=lambda n: park_msgs.append(
                {"role": "system", "content": n}))
        return text

    def _route_park_command(self, text, channel, channel_id, share, ask=None,
                            who="", author_id=None, on_note=None):
        """Run the `> command` a kin puts in its Discord reply.

        With `ask` (direct messages only, since 12 Nov 2026): the kin's
        whole turn, through the same `park_keeper.play_turn` loop the
        desktop, Telegram private chats and wake-ups use. It sees each
        result and moves again until it stops or its allowance runs out,
        and "stop" reaches it between moves.

        Without `ask` (server channels): ONE move. A channel can hold several
        people at once, same as a Telegram group, and
        `park_keeper.route_reply`'s docstring names what a loop risks there:
        a kin's turn landing under another tenant's name in a feed everyone
        reads. Telegram draws the same line for groups.

        Best-effort, after the reply already posted: a park error must
        never cost a message that already landed in the channel."""
        try:
            import park_keeper
            if park_keeper.kin_park_mode(self.agent_name) not in (
                    "chat", "keeper"):
                return
            from tools import get_game
            host = get_game("tff")
            if host is None:
                return
            # Reachability is only worth checking -- and only worth failing
            # loudly about -- when there's a real move to run. A kin quoting
            # a document back (route_reply's own quote guard) never touches
            # the game, so it must not be blamed on the game being down.
            # Checked once, before any move, so one failed reach can't
            # become a whole turn's worth.
            if park_keeper.extract_command(text):
                ok, why = host.reachable(self.agent_name)
                if not ok:
                    host.log_unreachable(self.agent_name, why, "discord")
                    if self._block_lines_on():
                        self._post_sync(
                            channel,
                            "🌳 The park isn't reachable right now, so that "
                            "move didn't run and nothing was changed.")
                    return
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "park routing", e)
            return
        # Re-open the turn while the loop runs, so "stop" reaches it.
        reopened = False
        if ask is not None:
            try:
                self._begin_turn(channel_id, who, author_id=author_id)
                reopened = True
            except Exception:
                reopened = False
        try:
            result = park_keeper.play_turn(
                self.agent_name, text,
                run_move=lambda t: self._route_one_park_move(
                    t, channel, channel_id, share, host, on_note),
                ask=ask,
                awaiting=lambda: host.awaiting_answer(self.agent_name),
                cancelled=lambda: self._turn_cancelled(channel_id))
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "park turn", e)
            result = None
        finally:
            if reopened:
                try:
                    self._end_turn(channel_id)
                except Exception:
                    pass
        if result is not None and getattr(result, "spent_allowance", False):
            try:
                from kin_persistence import load_app_prompt
                _spent = load_app_prompt(
                    "park_moves_spent", self.agent_name).replace(
                        "{moves}", str(result.moves))
                if self._block_lines_on():
                    self._post_sync(channel, _spent)
                # Kept in the kin's history too: what the bot posts isn't
                # saved, so the kin never learned its moves were spent
                # (audit, 20 Nov 2026).
                self._persist_turn(channel_id, "system", _spent, share)
            except Exception:
                pass

    def _block_lines_on(self):
        """"Show what reply blocks did" for this kin on Discord (23 Nov
        2026). Every kind of block follows it: a message to another kin, a
        manual part, a file saved from the reply, a park move."""
        try:
            import reply_actions
            return reply_actions.block_lines_on(
                (self.get_config() or {}).get("discord") or {})
        except Exception:
            return True

    def _route_one_park_move(self, text, channel, channel_id, share, host,
                             on_note=None):
        """Run one `> command` from `text`. True if a move actually ran.
        The channel sees the plain result; the kin's own copy carries the
        trimmings (what other tenants did, one thing worth doing on a look)
        -- decorate(), never a bare result."""
        try:
            import park_keeper
            cmd, res = park_keeper.route_reply(
                text, lambda c, s="": host.run(self.agent_name, c, say=s))
            if not res:
                return False
            if self._block_lines_on():
                self._post_sync(channel, "🌳 " + res)
            try:
                kin_res = host.decorate(self.agent_name, cmd, res)
            except Exception:
                kin_res = res
            from kin_persistence import load_app_prompt
            note = (load_app_prompt("park_result_single", self.agent_name)
                    .replace("{command}", str(cmd))
                    .replace("{result}", str(kin_res)))
            self._persist_turn(channel_id, "system", note, share)
            if on_note is not None:
                on_note(note)
            return True
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "park routing", e)
            return False

    def _persist_tool_turn(self, channel_id, turn, share):
        """Store one tool round-trip turn. Kept apart from _persist_turn
        because these carry tool_calls / tool_call_id rather than plain text,
        and dropping those fields is what made them unstorable before."""
        import datetime
        row = {k: v for k, v in turn.items() if k in (
            "role", "content", "tool_calls", "tool_call_id", "name")}
        row.setdefault("ts", datetime.datetime.now().isoformat(
            timespec="seconds"))
        try:
            if share:
                from kin_persistence import append_agent_conversation_turn
                append_agent_conversation_turn(
                    self.agent_name,
                    {**row, **self._dm_mark(channel_id),
                     "source": f"discord:{channel_id}"})
            else:
                from kin_persistence import append_discord_turn
                append_discord_turn(self.agent_name, channel_id, row)
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "persist tool turn", e)

    def _run_model(self, schemas, executor, model, messages, cfg, common, ed,
                   channel, channel_id, intermediate_seen, tool_names_called):
        """The model call itself, tools or not. Split out of _generate purely
        so the in-flight bookkeeping around it can be a plain try/finally
        rather than wrapping eighty lines.

        Returns (text, thinking, added_turns). The turns come back rather
        than staying in here because the caller needs them twice over: to
        store the tool round-trips, and to decide whether a narrated tool
        call was a gesture or a description of real work."""
        import llm_backend

        if schemas:
            with self._turn_lock:
                _entry = self._active_turns.get(channel_id) or {}
            _remote_cap = _entry.get(
                "tool_cap", _cfg_count(cfg, "remote_max_tool_calls", 15))
            _notice_at = _cfg_count(cfg, "remote_tool_call_notice", 10)
            _dc = cfg.get("discord") or {}
            _show_calls = bool(_dc.get("show_tool_calls", False))
            # Telegram's "still working" message, shared (chat_helpers).
            from chat_helpers import ProgressPing
            _progress = ProgressPing(
                _dc.get("tool_progress_interval_secs", 90),
                lambda t: self._post_from_thread(channel, t))

            def on_tool_call(name, args, result, is_error):
                # Append-only tool display, same discipline as Telegram:
                # a call posts what it did + a result preview; never edits.
                tool_names_called.append(name)
                _progress.called(name, may_ping=not _show_calls
                                 and not is_error)
                if len(tool_names_called) == _notice_at:
                    self._announce_long_reply(channel_id, len(tool_names_called))
                try:
                    if bool((cfg.get("discord") or {}).get(
                            "show_tool_calls", False)):
                        self._post_from_thread(
                            channel, f"🔧 {name}({self._short_args(args)})")
                        self._post_from_thread(
                            channel, f"→ {str(result)[:400]}")
                    elif is_error:
                        # A failure is always shown, as on Telegram.
                        self._post_from_thread(
                            channel, f"⚠️ {str(result)[:400]}")
                except Exception:
                    pass

            # Keep the last non-empty thing the kin said before a tool call
            # cleared the buffer. Some models (Haiku 4.5 with side-action
            # tools especially) write their real reply, call a tool, then
            # emit almost nothing after the tool result — the intermediate
            # IS the reply, and dropping it is how a good answer becomes
            # silence. The editor already buffers per turn; this only reads
            # it on the way past.
            def _on_turn():
                held = (ed._buf or "").strip()
                if held:
                    intermediate_seen.append(held)
                ed.reset_turn()

            result = llm_backend.run_tool_loop(
                model, messages, tools=schemas, tool_executor=executor,
                on_tool_call=on_tool_call,
                should_stop=lambda: self._turn_cancelled(channel_id),
                # What they say mid-reply reaches the reply before its next
                # step, as on Telegram (12 Nov 2026).
                interject=lambda: self._take_interjections(channel_id),
                # The person's ceiling: their own number, else the kin's.
                max_tool_calls=_remote_cap or None,
                on_content=ed.feed, on_turn=_on_turn,
                surface=f"{self._surface_label}-tool",
                tool_result_cap=int(cfg.get("tool_result_cap", 8000) or 8000),
                max_iterations=int(cfg.get("max_tool_iterations", 8) or 8),
                **common)
            text = (result.content or "").strip()
            thinking = (getattr(result, "thinking", "") or "").strip()
        else:
            # Plain reply. chat_collect rather than iterating chat(stream=True)
            # by hand: it is the same streamed call, but it polls should_stop
            # once per chunk and KEEPS what was collected so far, which is the
            # only place a stop can exist — chat(stream=False) hands back one
            # finished answer with no point inside it to interrupt.
            result = llm_backend.chat_collect(
                model, messages, on_content=ed.feed,
                should_stop=lambda: self._turn_cancelled(channel_id),
                surface=self._surface_label, **common)
            text = (result.content or "").strip()
            thinking = (getattr(result, "thinking", "") or "").strip()
        # A tool loop reports the turns it added; a plain reply has none.
        added = list(getattr(result, "messages_added", None) or [])
        return text, thinking, added

    def _log_empty_reply(self, *, model, channel_id, user_id, raw_content,
                         post_cleanup, intermediate_content, tool_calls_made,
                         salvaged):
        """Always-on diagnostic for an empty Discord reply, mirroring
        TelegramBot._log_empty_reply and the desktop's. Written regardless of
        the session-log toggle, because the whole point is that this failure
        is invisible from the chat itself.

        `raw_content` is what the model returned before cleanup;
        `post_cleanup` is what survived it. When those differ, the cleanup
        chain ate the reply (usually the kin opened with its own name tag).
        When both are empty the model genuinely produced nothing."""
        from kin_persistence import LOGS_DIR
        import datetime
        try:
            path = LOGS_DIR / "empty_replies.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            surface = "discord [salvaged]" if salvaged else "discord"
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    f"{ts} [{self.agent_name}] surface={surface} "
                    f"model={model} chat={channel_id} user={user_id} "
                    f"raw={raw_content!r} "
                    f"post_cleanup={post_cleanup!r} "
                    f"intermediate={(intermediate_content or '')!r} "
                    f"tools={list(tool_calls_made or [])!r}\n"
                )
        except Exception:
            pass

    def _wrap_webcam_for_discord(self, inner_webcam, message):
        """Gate a `use_webcam` call from Discord on the operator's approval.

        Deliberately ALWAYS asks. The Telegram surface offers a per-user
        ask/auto/deny radio because the operator curates that list person by
        person; the Discord tab has no such per-person screen yet, and the
        safe reading of a missing setting is "ask", never "auto". If that
        radio arrives for Discord later, this is where it plugs in.

        Posts a line in-channel before blocking, so a request that is sitting
        on a desktop dialog doesn't read as the kin having frozen."""
        import json

        def wrapped(args):
            who = (getattr(message.author, "display_name", None)
                   or getattr(message.author, "name", "") or "")
            try:
                self._post_from_thread(
                    message.channel,
                    "📷 Asking the operator to approve a webcam capture — "
                    "one moment…")
            except Exception:
                pass
            try:
                decision = self._request_webcam_approval(
                    who, getattr(message.author, "id", ""))
            except Exception as e:
                return json.dumps({
                    "ok": False,
                    "error": f"Webcam approval failed: {e}",
                })
            if decision == "allow":
                return inner_webcam(args)
            if decision == "unavailable":
                # Nobody was shown the request. Say exactly that — telling the
                # kin it was refused invents a decision the operator never
                # made, and the kin then apologises for it.
                return json.dumps({
                    "ok": False,
                    "error": "Couldn't reach the operator to approve the "
                             "webcam capture right now — nobody refused it, "
                             "the request just couldn't be put to them. Try "
                             "again in a moment.",
                })
            return json.dumps({
                "ok": False,
                "error": "The operator saw the webcam request and declined it.",
            })

        return wrapped

    async def _download_images(self, message):
        """Return local temp-file paths for image attachments on `message`,
        but ONLY when the kin's model can actually see images — otherwise
        it's a pointless download. Capped in count and size. Best-effort:
        a bad attachment is skipped, never fatal. Caller deletes the files
        after the turn."""
        paths = []
        try:
            from llm_backend import model_supports_images
            model = (self.get_config() or {}).get("model", "")
            if not model or not model_supports_images(model):
                return paths
        except Exception:
            return paths
        import tempfile
        for att in list(getattr(message, "attachments", []) or [])[:DISCORD_MAX_IMAGES]:
            ctype = (getattr(att, "content_type", "") or "")
            if not ctype.startswith("image/"):
                continue
            try:
                data = await att.read()
            except Exception:
                continue
            if not data or len(data) > DISCORD_MAX_IMAGE_BYTES:
                continue
            suffix = os.path.splitext(getattr(att, "filename", "") or "")[1] or ".png"
            try:
                fd, path = tempfile.mkstemp(prefix="discord_img_", suffix=suffix)
                os.close(fd)
                with open(path, "wb") as f:
                    f.write(data)
                paths.append(path)
            except Exception:
                continue
        return paths

    async def _other_file_notes(self, message, voice=None):
        """A note per attachment that isn't a picture, a text file or the
        voice message: what it is, and for a sound file where it's saved.
        Never raises."""
        import reading_bridge
        notes = []
        for att in list(getattr(message, "attachments", []) or []):
            try:
                if att is voice:
                    continue
                name = getattr(att, "filename", "") or ""
                mime = (getattr(att, "content_type", "") or "").lower()
                if mime.startswith("image/") or reading_bridge.is_text_attachment(
                        name, mime):
                    continue
                saved = None
                if reading_bridge.is_sound_file(name, mime):
                    kind = "sound file"
                    if int(getattr(att, "size", 0) or 0) <= \
                            reading_bridge.MAX_SOUND_BYTES:
                        data = await att.read()
                        saved = reading_bridge.save_sound_file(
                            self.agent_name, name, data)
                elif mime.startswith("video/"):
                    kind = "video"
                elif mime == "application/pdf" or name.lower().endswith(".pdf"):
                    kind = "PDF"
                else:
                    kind = "file"
                notes.append(reading_bridge.file_arrival_note(kind, name, saved))
            except Exception:
                continue
        return notes

    @staticmethod
    def _voice_attachment(message):
        """The voice message attached to `message`, or None. Discord marks
        them; an Ogg audio file is treated the same, since that's what one
        is underneath."""
        for att in list(getattr(message, "attachments", []) or []):
            try:
                if att.is_voice_message():
                    return att
            except Exception:
                pass
            if (getattr(att, "content_type", "") or "").startswith("audio/ogg"):
                return att
        return None

    async def _hear_voice(self, message, att):
        """Read and transcribe a voice message. Returns the words, or None
        after telling the person why it couldn't be heard. The words are
        echoed back first, so the person knows what the kin received."""
        import voice_notes
        ch = message.channel
        if getattr(self, "transcribe_voice", None) is None:
            await self._send_chunked(ch, (
                f"{self.agent_name} can't hear voice messages here: "
                "Hearthkin's dictation isn't available. Please type it."))
            return None
        if (getattr(att, "size", 0) or 0) > voice_notes.MAX_VOICE_BYTES:
            await self._send_chunked(
                ch, "That voice message is too long for me to transcribe.")
            return None
        try:
            data = await att.read()
            async with ch.typing():
                heard = await self._loop.run_in_executor(
                    None, self.transcribe_voice, data)
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "voice message", e)
            await self._send_chunked(ch, (
                f"I couldn't make out that voice message ({e}). Please try "
                "again, or type it."))
            return None
        await self._send_chunked(ch, f"🎙 Heard: {heard}")
        return heard

    async def _download_text_documents(self, message):
        """Return [(filename, bytes)] for attached NON-image files we'd read as
        text. Unlike images this needs no model capability — every model reads
        text — so there's no vision gate here.

        Previously these were skipped by the image-only filter and dropped
        silently: the kin received the caption with no idea a file had been
        sent. A failed read yields empty bytes rather than vanishing, so the
        block reports "could not load" instead of saying nothing."""
        out = []
        try:
            import reading_bridge
        except Exception:
            return out
        for att in list(getattr(message, "attachments", []) or []):
            name = getattr(att, "filename", "") or "attachment"
            ctype = (getattr(att, "content_type", "") or "")
            if ctype.startswith("image/"):
                continue  # the image path owns these
            if not reading_bridge.is_text_attachment(name, ctype):
                continue
            if len(out) >= DISCORD_MAX_IMAGES:
                break  # same per-message fan-out cap as images
            try:
                data = await att.read()
            except Exception as e:
                append_failure_log("discord_failures.log", self.agent_name,
                                   f"text attachment read failed {name!r}", e)
                data = b""
            out.append((name, data or b""))
        return out

    def _post_from_thread(self, channel, text):
        """Send a Discord message from the executor thread by scheduling it
        on the Gateway event loop (channel.send is a coroutine)."""
        try:
            asyncio.run_coroutine_threadsafe(
                channel.send((text or "")[:DISCORD_MSG_LIMIT]), self._loop)
        except Exception:
            pass

    def _post_sync(self, channel, text):
        """Blocking chunked send from the executor thread — the fallback
        when the streamed finalize edit fails."""
        from chat_helpers import split_for_limit
        for i, chunk in enumerate(split_for_limit(text or "",
                                                  DISCORD_MSG_LIMIT)):
            try:
                asyncio.run_coroutine_threadsafe(
                    channel.send(chunk), self._loop).result(timeout=20)
            except Exception as e:
                # Told, not silently cut short (21 Nov 2026), as in
                # _send_chunked.
                append_failure_log("discord_failures.log", self.agent_name,
                                   f"send channel={channel.id} piece={i}", e)
                try:
                    asyncio.run_coroutine_threadsafe(channel.send(
                        "(Part of this reply couldn't be sent: Discord "
                        "refused it. The rest is missing here, though "
                        f"{self.agent_name} still has the whole of it.)"),
                        self._loop).result(timeout=20)
                except Exception:
                    pass
                return

    @staticmethod
    def _is_allowed(author_id, allow_from):
        """True if this Discord user may get a reply. DENY-BY-DEFAULT:

          - empty allow_from → False (nobody) — a tool-capable kin must not
            be reachable by "anyone in any server" out of the box;
          - "*" in the list  → True (operator opted into open access);
          - otherwise        → only the listed IDs.

        Compared as strings so int/str shapes match."""
        allow = [str(x) for x in (allow_from or []) if str(x).strip()]
        if not allow:
            return False
        if "*" in allow:
            return True
        return str(author_id) in allow

    @staticmethod
    def _short_args(args):
        """Compact one-line tool-args preview for the channel display."""
        if not isinstance(args, dict):
            return str(args)[:120]
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 60:
                s = s[:57] + "…"
            parts.append(f"{k}={s}")
        return ", ".join(parts)[:200]

    async def _send_chunked(self, channel, text):
        """Send `text` within Discord's 2000-character limit, split by the
        shared splitter (chat_helpers.split_for_limit): counted cautiously,
        so an emoji-heavy piece can't come out over, and broken at a line or
        a space, never mid-word.

        Discord refuses anything over its limit rather than trimming it.
        If a piece is refused anyway, the person is TOLD part of the reply
        didn't arrive. It used to stop silently there, and everything after
        the refused piece was simply gone (21 Nov 2026). The kin's own copy
        of its reply is whole either way; only the delivery fell short.
        Rate limits need nothing here: the discord library waits and retries
        those itself."""
        from chat_helpers import split_for_limit
        pieces = split_for_limit(text, DISCORD_MSG_LIMIT)
        for i, chunk in enumerate(pieces):
            try:
                await channel.send(chunk)
            except Exception as e:
                append_failure_log("discord_failures.log", self.agent_name,
                                   f"send channel={channel.id} piece={i}", e)
                try:
                    await channel.send(
                        "(Part of this reply couldn't be sent: Discord "
                        "refused it. The rest is missing here, though "
                        f"{self.agent_name} still has the whole of it.)")
                except Exception:
                    pass
                return

    # ── helpers ────────────────────────────────────────────────────

    def status_label(self):
        """The last status line, for the settings window to show when it
        opens (it was always blank there; audit, 20 Nov 2026)."""
        return getattr(self, "_last_status", "") or "Off"

    def _status(self, msg):
        self._last_status = msg
        try:
            if self.on_status:
                self.on_status(msg)
        except Exception:
            pass
