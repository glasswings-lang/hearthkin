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

# How many prior turns from a channel to feed back as context. The kin's
# num_ctx truncation is the real backstop; this just bounds the disk read
# and keeps a busy channel's history from dominating the prompt.
DISCORD_HISTORY_CAP = 40

# Abuse throttles (parity with Telegram's flood handling). A single user
# can't force back-to-back generations faster than the cooldown, and no
# more than N generations run concurrently across all channels — so a
# mention-spammer across many channels can't saturate the Ollama host /
# thread pool (a cheap DoS / cost-amplification otherwise).
DISCORD_USER_COOLDOWN_SECS = 3.0
DISCORD_MAX_CONCURRENT_GENERATIONS = 3

# Matches a Discord user mention token: <@123> or <@!123> (the "!" form
# is the legacy nickname mention). Stripped from content before the
# message goes to the model so the kin doesn't see "<@1234> hey" — it
# just sees "hey".
_MENTION_RE = re.compile(r"<@!?\d+>")

# Words that end an in-flight reply when sent on their own. Discord has no
# slash-command surface here, so the stop has to be something a person would
# plausibly type; kept to an exact match on the whole message so "stop that,
# it's funny" is a remark to the kin, not a control instruction.
_STOP_WORDS = frozenset({"stop", "/stop", "cancel", "/cancel", "!stop"})


def _stable_history_window(turns, cap):
    """The last `cap`-ish turns, with a start that moves in RARE, BIG steps
    instead of by one every single turn.

    `turns[-cap:]` is the obvious version and it is the same prompt-cache bug
    already fixed one layer up in TelegramBot._trim_history. A local model
    reuses its cached work only for an unbroken run from the very start of the
    prompt, so once a channel passes `cap` messages, every new message pushes
    one off the FRONT and the whole context is read again from cold. A channel
    below the cap is fast and a channel at the cap is slow forever, and the two
    are indistinguishable from a chair.

    So the window is allowed to fill to `cap`, then its start jumps a whole
    `step` at once and sits still until the next jump. Between jumps the prompt
    is genuinely append-only. One turn in `step` pays the re-read instead of
    all of them.

    `ceil`, not `floor`: the window must never EXCEED `cap`, because an
    oversized context on local Ollama returns nothing at all rather than
    degrading. What changes is the floor — the window now runs between
    `cap - step` and `cap` instead of sitting pinned at `cap`.
    """
    n = len(turns)
    if cap <= 0 or n <= cap:
        return list(turns)
    step = max(1, cap // 4)
    start = -(-(n - cap) // step) * step      # ceil((n - cap) / step) * step
    return list(turns[start:])


class DiscordBot:
    """Per-kin Discord bot. One live Gateway connection, mention-only
    replies, all inference off the event loop."""

    def __init__(self, agent_name, get_config, get_soul, get_memory,
                 get_model_options, on_status, on_activity=None,
                 wrap_exec=None, request_webcam_approval=None):
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

    def _begin_turn(self, channel_id, who):
        """Register a reply as being written. Clears any stale stop flag so a
        'stop' typed during an earlier turn can't kill this one."""
        with self._turn_lock:
            self._active_turns[channel_id] = {"who": who, "stop": False}

    def _end_turn(self, channel_id):
        with self._turn_lock:
            self._active_turns.pop(channel_id, None)

    def _turn_cancelled(self, channel_id):
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            return bool(entry and entry.get("stop"))

    def _request_turn_stop(self, channel_id):
        """Ask the reply in this channel to stop. Returns True when there was
        one to stop, so the caller can say something true either way.

        Scoped to the CHANNEL, which is the unit a Discord reply belongs to —
        one channel's stop must not reach into another's."""
        with self._turn_lock:
            entry = self._active_turns.get(channel_id)
            if not entry:
                return False
            entry["stop"] = True
            return True

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
        # Never reply to ourselves or to other bots (a two-bot loop in a
        # server would generate forever).
        if message.author.id == client.user.id or message.author.bot:
            return

        cfg = self.get_config() or {}
        dcfg = cfg.get("discord", {}) or {}
        policy = (dcfg.get("policy") or "mention_only").lower()

        addressed = client.user in message.mentions
        if policy == "mention_only" and not addressed:
            return  # not spoken to — stay quiet (the whole point on a busy server)

        # Location gate (coarse, optional): when guilds/channels allowlists
        # are configured, only engage there — so a bot added to an
        # unexpected server stays silent even if a listed user is present.
        # Empty lists mean "no location restriction" and access falls to
        # allow_from alone.
        guilds = [str(x) for x in (dcfg.get("guilds") or []) if str(x).strip()]
        if guilds:
            gid = getattr(getattr(message, "guild", None), "id", None)
            if gid is None or str(gid) not in guilds:
                return
        channels = [str(x) for x in (dcfg.get("channels") or []) if str(x).strip()]
        if channels and str(message.channel.id) not in channels:
            return

        # Access control (deny-by-default). Empty allow_from = nobody; "*" =
        # anyone; otherwise only listed user IDs. Silent ignore in-channel
        # (no "you're not allowed" chatter), but the empty-allow_from case is
        # surfaced once via on_status so it doesn't look like a dead bot.
        allow_from = dcfg.get("allow_from")
        if not self._is_allowed(message.author.id, allow_from):
            allow = [str(x) for x in (allow_from or []) if str(x).strip()]
            if not allow and not self._warned_empty_allow:
                self._warned_empty_allow = True
                self._status(
                    "Discord: no allow_from configured, so every message is "
                    "ignored (deny-by-default for safety). Add Discord user "
                    "IDs, or \"*\" to allow anyone, in the kin's Discord "
                    "settings.")
            return

        content = _MENTION_RE.sub("", message.content or "").strip()
        if not content:
            return

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
        if content.strip().lower() in _STOP_WORDS:
            if self._request_turn_stop(message.channel.id):
                await self._send_chunked(
                    message.channel,
                    "Stopped. Keeping what was written so far.")
            else:
                await self._send_chunked(
                    message.channel,
                    "Nothing is being written here just now.")
            return

        # Per-user cooldown: drop a too-fast follow-up from the same user
        # (silent — no channel spam). Bounds single-user flood.
        import time
        now = time.monotonic()
        if now - self._user_last_reply.get(message.author.id, 0.0) < \
                DISCORD_USER_COOLDOWN_SECS:
            return
        self._user_last_reply[message.author.id] = now

        # Download any attached images up front (only when the kin's model
        # can actually see them). Cleaned up after the turn either way.
        image_paths = await self._download_images(message)
        # Text documents (.txt/.md/source) were previously skipped by the
        # image filter and dropped without a word — "check this out" arrived
        # with nothing attached. Unlike images these need no model support.
        text_docs = await self._download_text_documents(message)

        # Global concurrency cap across all channels (created lazily on the
        # loop) + per-channel serialize. Together they bound total in-flight
        # generations so a spammer across many channels can't saturate the
        # backend.
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
                await self._send_chunked(message.channel, f"[error: {e}]")
            finally:
                for p in image_paths:
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        if self.on_activity:
            try:
                self.on_activity("discord", message.channel.id)
            except Exception:
                pass

    def _channel_history(self, channel_id, share):
        """This channel's prior turns, model-shaped (role + content only),
        newest DISCORD_HISTORY_CAP kept. `share` picks the store:

          share=True  — MERGED: filter the unified conversation.jsonl for
                        rows tagged source=discord:<channel_id>.
          share=False — SEGREGATED: read the channel's slice of the
                        standalone discord_history.json.

        Either way it's this channel ONLY, so it never sees desktop or
        another channel."""
        if share:
            from kin_persistence import load_agent_conversation
            source = f"discord:{channel_id}"
            try:
                rows = [m for m in (load_agent_conversation(self.agent_name)
                                    or [])
                        if isinstance(m, dict)
                        and (m.get("source") or "") == source]
            except Exception:
                return []
        else:
            from kin_persistence import load_discord_history
            try:
                rows = (load_discord_history(self.agent_name) or {}).get(
                    str(channel_id)) or []
            except Exception:
                return []
        turns = []
        for m in rows:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant") and content:
                turns.append({"role": role, "content": content})
        return _stable_history_window(turns, DISCORD_HISTORY_CAP)

    def _persist_turn(self, channel_id, role, content, share):
        """Store one turn in whichever store `share` selects (merged
        conversation.jsonl vs segregated discord_history.json)."""
        import datetime
        turn = {"role": role, "content": content,
                "ts": datetime.datetime.now().isoformat(timespec="seconds")}
        if share:
            from kin_persistence import append_agent_conversation_turn
            append_agent_conversation_turn(
                self.agent_name,
                {**turn, "source": f"discord:{channel_id}"})
        else:
            from kin_persistence import append_discord_turn
            append_discord_turn(self.agent_name, channel_id, turn)

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
        options = self.get_model_options() or {}
        model = cfg.get("model", "")
        channel_id = message.channel.id
        share = bool(dcfg.get("share_desktop", False))

        # Inline attribution, no colon — same anti-impersonation shape as
        # the Telegram group path (a "[Name]:" token is a speaker-turn
        # attractor; the no-colon bracket isn't). Display name is
        # attacker-controllable, so sanitize control chars first.
        # Through the SHARED helper, not a bracket built here. It produces the
        # same shape today, which is exactly why a second hand-rolled copy is
        # dangerous: the no-colon rule is an anti-impersonation defence, and a
        # copy is what keeps the old shape when the rule next changes. Every
        # surface that names a speaker to a model goes through one function.
        from chat_helpers import speaker_attribution_prefix
        who = sanitize_for_prompt_literal(getattr(
            message.author, "display_name", None) or message.author.name)
        user_text = speaker_attribution_prefix(who) + content

        history = self._channel_history(channel_id, share)

        # Persist the user turn NOW, before generating — if generation
        # fails the message is still on record, never orphaned.
        self._persist_turn(channel_id, "user", user_text, share)

        # Load the kin's tools, gated by THIS user's bucket — the same
        # (kin tools.json) ∩ (per-user bucket) intersection the Telegram
        # surface enforces. A user not listed in discord.user_tools
        # defaults to 'none' (chat only), so write_file/edit_file/note/exec
        # are never handed to an arbitrary Discord member. exec is
        # additionally routed to the operator's desktop for approval
        # (self._wrap_exec); if no approval path is wired, exec is dropped
        # rather than run unguarded.
        schemas, executor = [], {}
        author_id = getattr(message.author, "id", "")
        bucket = (dcfg.get("user_tools") or {}).get(str(author_id), "none")
        enabled = filter_tool_names(load_kin_tools(self.agent_name) or [], bucket)
        if enabled:
            from tools import load_tools
            # confine_paths revokes the absolute-path escape hatch for file
            # tools on this remote surface (audit D1). Confined by DEFAULT;
            # `remote_unconfined_files` in the kin config (Settings -> Tools
            # -> Tool settings) hands this kin desktop-equivalent reach.
            # Read from the kin config, never from anything the model sets.
            _confine = not bool(cfg.get("remote_unconfined_files"))
            schemas, executor = load_tools(
                enabled,
                context={"agent_name": self.agent_name,
                         "confine_paths": _confine},
                model=model)
            if self._wrap_exec is not None:
                executor = self._wrap_exec(executor, self.agent_name)
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
            if "use_webcam" in executor:
                if self._request_webcam_approval is not None:
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
        if tool_names:
            system += load_app_prompt(
                "tool_use_hint", self.agent_name).replace(
                    "{tools}", ", ".join(tool_names))
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
            return clean_kin_reply(t, self.agent_name)[0]

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
        self._begin_turn(channel_id, who)
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
        raw_model_content = text
        text, _ = extract_inline_thinking(text, thinking)
        text, _imp = clean_kin_reply(text, self.agent_name)
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
                cand, _ = clean_kin_reply(cand, self.agent_name)
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
        shown = text
        if dcfg.get("show_recall_summary", True):
            names = []
            for used in (self._last_recall_used or []):
                rel = (used.get("relpath") if isinstance(used, dict) else "") or ""
                if rel and rel not in names:
                    names.append(rel)
            if names:
                shown = text + "\n\n_recalled: " + ", ".join(names) + "_"

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
        # The footer is for the reader, not the record — storing it would put
        # the harness's bookkeeping into the kin's own voice.
        self._persist_turn(channel_id, "assistant", text, share)

        # Tool round-trips, stored so the kin can see what it DID last turn.
        # Without these every turn here began with no idea what happened in
        # the one before, and a kin that cannot see its own past calls either
        # repeats them or denies making them.
        for _turn in added_turns:
            if isinstance(_turn, dict) and _turn.get("role") in (
                    "assistant", "tool"):
                self._persist_tool_turn(channel_id, _turn, share)

        # The steering notes every other surface gives back. All four are
        # shared code (turn_steering) rather than a fourth copy of the same
        # judgement — see that module for why. Best-effort throughout: a
        # steering fault must never cost a reply that has already gone out.
        try:
            import turn_steering
            notes = []
            kin_note, human_note = turn_steering.authoring_bridge_notes(
                self.agent_name, text, tool_names)
            if kin_note:
                notes.append(kin_note)
            if human_note:
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
            for note in notes:
                self._persist_turn(channel_id, "system", note, share)
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "turn steering", e)
        # Text-in/text-out park bridge (per-kin `park` setting = chat|keeper):
        # a `> command` in the reply runs. One move, never a loop -- see
        # _route_park_command for why a Discord channel gets the same
        # single-move treatment Telegram GROUP does rather than the full
        # play_turn turn desktop/Telegram DM/cron get. After the reply, so a
        # park error can never cost a message that already went out.
        self._route_park_command(text, channel, channel_id, share)
        return text

    def _route_park_command(self, text, channel, channel_id, share):
        """Run the `> command` a kin puts in its Discord reply -- ONE move,
        never a whole turn.

        Discord is channel/guild-shaped, not DM-shaped: `dcfg["channels"]`/
        `dcfg["guilds"]` gate WHERE the bot listens, but a listened-to
        channel can still hold several people at once, same as a Telegram
        group. `park_keeper.route_reply`'s own docstring names the exact
        failure a multi-move loop risks there: a kin's turn landing under
        another tenant's name in a feed everyone reads, because the loop
        cannot tell "my move" from "the room". Telegram already drew this
        line -- group chats get `_route_park_command` with no `ask` (one
        move), while Telegram DM, desktop and the cron keeper get the full
        `park_keeper.play_turn` loop. Discord has no DM path at all (see
        discord_bot.py: every message here comes through a guild channel),
        so it never qualifies for the loop side of that line -- it gets the
        same single-move treatment as Telegram group, always.

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
            if park_keeper.extract_command(text):
                ok, why = host.reachable(self.agent_name)
                if not ok:
                    host.log_unreachable(self.agent_name, why, "discord")
                    self._post_sync(
                        channel,
                        "🌳 The park isn't reachable right now, so that "
                        "move didn't run and nothing was changed.")
                    return
            cmd, res = park_keeper.route_reply(
                text, lambda c, s="": host.run(self.agent_name, c, say=s))
            if not res:
                return
            # The channel sees the plain result; the kin's own copy carries
            # the trimmings (what other tenants did, one thing worth doing
            # on a look) -- decorate(), never a bare result, per CLAUDE.md.
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
        except Exception as e:
            append_failure_log("discord_failures.log", self.agent_name,
                               "park routing", e)

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
                    {**row, "source": f"discord:{channel_id}"})
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
            def on_tool_call(name, args, result, is_error):
                # Append-only tool display, same discipline as Telegram:
                # a call posts what it did + a result preview; never edits.
                tool_names_called.append(name)
                try:
                    self._post_from_thread(
                        channel, f"🔧 {name}({self._short_args(args)})")
                    self._post_from_thread(
                        channel, f"→ {str(result)[:400]}")
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
        rest = text or ""
        while rest:
            chunk, rest = rest[:DISCORD_MSG_LIMIT], rest[DISCORD_MSG_LIMIT:]
            try:
                asyncio.run_coroutine_threadsafe(
                    channel.send(chunk), self._loop).result(timeout=20)
            except Exception:
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
        """Send `text` respecting Discord's 2000-char limit. Split on a
        paragraph/line boundary near the limit when possible so a message
        doesn't get sliced mid-word."""
        while text:
            if len(text) <= DISCORD_MSG_LIMIT:
                chunk, text = text, ""
            else:
                cut = text.rfind("\n", 0, DISCORD_MSG_LIMIT)
                if cut < DISCORD_MSG_LIMIT // 2:
                    cut = DISCORD_MSG_LIMIT
                chunk, text = text[:cut], text[cut:].lstrip("\n")
            try:
                await channel.send(chunk)
            except Exception as e:
                append_failure_log("discord_failures.log", self.agent_name,
                                   f"send channel={channel.id}", e)
                return

    # ── helpers ────────────────────────────────────────────────────

    def _status(self, msg):
        try:
            if self.on_status:
                self.on_status(msg)
        except Exception:
            pass
