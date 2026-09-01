# SPDX-License-Identifier: CC0-1.0

"""
Hearthkin — a warm place to sit and talk with your kin.

A wxPython chat interface for local Ollama models. Each agent has
their own soul file (a first-person self-account, not a system prompt
for a tool) and their own model config. Built to be screen-reader
friendly and forgiving of plural / multi-agent workflows.
"""

# hearthkin.pyw is the assembler for the Hearthkin frame. As of the 2026-07
# modularisation the frame's 250+ methods live in concern-focused mixins under
# frame/ (combined below via multiple inheritance); every module-level import,
# constant, and helper the frame and its mixins share lives in frame_shared.py.
# This file keeps only __init__, main(), and the class declaration that ties
# them together. `self.method` resolution is identical to the pre-split monolith.

from frame_shared import (
    APP_NAME, CONFIG_FILE, DEFAULT_CONFIG, _bring_existing_hearthkin_to_front,
    _ensure_foreground_lock_disabled, _force_foreground, append_failure_log, cron_helpers,
    list_agents, list_rooms, llm_backend, load_agent_config, load_json,
    migrate_global_ollama_host, os, resolve_kin_ollama_host, seed_bundled_game,
    migrate_dictation_config, seed_kin_manual, stt, sys, threading, time,
    tray, voice_module, wx,
)
from frame import (
    DiagnosticsMixin,
    MenusMixin,
    UsageMixin,
    PrefsMixin,
    KinMgmtMixin,
    InputAttachMixin,
    ChatSendMixin,
    ChatStreamMixin,
    FileMenuMixin,
    RenderMixin,
    PrefsTogglesMixin,
    RoomsMixin,
    MemoryMixin,
    BotIntegrationMixin,
    StatusVoiceMixin,
    CronExecMixin,
    LifecycleMixin,
)


class Hearthkin(DiagnosticsMixin, MenusMixin, UsageMixin, PrefsMixin, KinMgmtMixin, InputAttachMixin, ChatSendMixin, ChatStreamMixin, FileMenuMixin, RenderMixin, PrefsTogglesMixin, RoomsMixin, MemoryMixin, BotIntegrationMixin, StatusVoiceMixin, CronExecMixin, LifecycleMixin, wx.Frame):
    def __init__(self):
        super().__init__(None, title=APP_NAME, size=(1000, 740))
        # One-time: fold any leftover global `ollama_host` into the
        # per-kin machine system (pin existing kin to it, register it,
        # drop the key) so removing the global setting doesn't silently
        # reroute every kin to localhost. No-op once migrated. See
        # kin_persistence.migrate_global_ollama_host.
        try:
            migrate_global_ollama_host()
        except Exception:
            pass
        self.config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        # Dictation settings changed shape once — from a key per backend
        # to the model-plus-machine pair every other model here uses.
        # Normalise the stored block on load so an older file keeps
        # saying what its owner meant, and so the superseded keys clear
        # instead of sitting in the file forever confusing whoever opens
        # it next. Never fatal: a settings block that cannot be
        # understood costs the default, not the app.
        try:
            self.config["dictation"] = migrate_dictation_config(
                self.config.get("dictation"))
        except Exception:
            pass
        for k, v in DEFAULT_CONFIG.items():
            self.config.setdefault(k, v)

        # Ensure Windows' foreground-lock is off so the window reliably
        # comes to the front (see _ensure_foreground_lock_disabled). Done
        # at startup, gated by the opt-out preference. No-op off Windows or
        # when already 0; takes effect on next sign-in.
        if self.config.get("manage_foreground_lock", True):
            try:
                _ensure_foreground_lock_disabled()
            except Exception:
                pass

        # Seed the kin manual at ~/.hearthkin/kin_manual.md if it isn't
        # already there. The manual is a reference document kin can
        # read on demand (via read_file with the absolute path) when
        # they want to understand the Hearthkin architecture more
        # deeply than what the always-loaded base prompt covers. The
        # canonical source is docs/kin_manual.md (bundled with the
        # installer); seed_kin_manual copies it into the operator's
        # config dir on first run. Idempotent — no-op if the file
        # already exists, so the operator can edit it freely.
        try:
            seed_kin_manual()
        except Exception:
            pass

        # Seed the bundled Time for Family game into ~/.hearthkin/games/ so
        # the `tff` tool works on a fresh install with nothing to clone. Must
        # be a writable copy (the game writes its own data next to itself), so
        # it can't run from the read-only app bundle. Idempotent; a no-op on a
        # source run with no bundled copy (the tool then falls back to the
        # operator's env var / path file / clone).
        try:
            seed_bundled_game()
        except Exception:
            pass

        self.conversation = []
        self.current_agent = None
        self.agent_cfg = {}
        # In-memory cache of the active kin's soul.md and memory.md
        # text. Refreshed on kin-load and after EditKinDialog saves
        # either file. Used by _update_token_display so we don't hit
        # disk on every keystroke in the input box — load_soul()
        # and load_memory() are cheap individually but per-keystroke
        # adds up to noticeable typing lag on slow disks or with
        # antivirus interception on the agents directory.
        self._soul_cache = ""
        self._memory_cache = ""
        # Set by the update-check workers when GitHub reports a tag
        # strictly newer than __version__. Surfaced in the Activity
        # field's default summary line as "· v<ver> available" so a
        # screen-reader user discovers it whenever the field reverts
        # to default — they don't have to catch the one-time startup
        # speech.
        self._update_available_version = None
        # Number of most-recent conversation messages currently
        # painted into chat_display. Less than or equal to
        # len(self.conversation). Initialized in _load_agent based
        # on the chat_history_window app preference. The "Load
        # older messages" button grows this; it never shrinks
        # except on a fresh kin-load. 0 means "no kin loaded".
        self._render_window = 0
        self.current_convo_file = None
        self._loading_agent = False
        # Tracks the model the current kin actually committed to (saved in
        # their config). model_choice's displayed value may differ during
        # typing; we compare against _active_model to decide when a real
        # swap is happening so we only show the swap-warning dialog once.
        self._active_model = ""

        self._stream_id = 0
        self._streaming = False
        self._stream_buf = ""
        self._stream_user_text = ""
        self._think_buf = ""
        # Salvage state from the last empty-reply path. When a
        # tool-loop's final content is empty AND its intermediate
        # content was substantive, we surface the intermediate as the
        # reply and set these flags so _on_stream_done can splice a
        # system note into the persisted conversation explaining what
        # happened. See _on_stream_done for the full handling.
        self._salvaged_intermediate = False
        self._salvaged_tool_names = []
        # Same shape, applied to the room kin path. _pending_room_tool
        # _history stashes the current turn's added_turns from
        # run_tool_loop so _on_room_kin_done can scan for salvageable
        # intermediate content if the final reply is empty.
        self._room_salvaged_intermediate = False
        self._room_salvaged_tool_names = []
        self._pending_room_tool_history = []
        # Paint cursor: chars of _stream_buf already pushed to chat_display.
        # Lets us paint streamed text sentence-by-sentence instead of all-at-end,
        # without painting per-token (which floods NVDA with text-changed events).
        self._paint_cursor = 0
        # Intermediate tool-call + tool-result turns carried over from the
        # most recent tool-loop invocation. Drained into self.conversation by
        # _on_stream_done between the user message and the final reply, so
        # the kin's persisted history reflects the full round-trip and the
        # model sees its own past tool calls on subsequent turns. Empty for
        # the pure-streaming path.
        self._pending_tool_history = []

        # Count of messages already persisted to the active kin's
        # conversation.jsonl on disk. Used by _persist_current_
        # conversation to know which trailing entries are new and need
        # appending vs already-saved. Reset on kin load.
        self._persisted_msg_count = 0

        # exec-tool approval state. The harness wraps the exec executor
        # with a function that checks the kin's tool_trust + the denylist
        # and (when approval is needed) shows ExecApprovalDialog on the
        # main thread while blocking the worker thread on an Event.
        # _pending_approvals is the list of those events so OnClose can
        # wake them all with a "deny" decision instead of leaving worker
        # threads hung on shutdown.
        self._pending_approvals = []
        self._closing = False

        # Cron lifecycle: clear any stale lock from a previous crashed
        # run, then claim this process's lock so hearthkin_cron.py knows
        # we're up. The lock gets deleted in _on_close. Failure to write
        # is non-fatal (cron just falls back to isolated mode), so we
        # don't surface it.
        try:
            cron_helpers.recover_stale_lock()
            cron_helpers.write_lock()
        except Exception:
            pass
        # Self-heal stale Task Scheduler entries from prior runs. Any
        # earlier launch (from a Claude Code worktree, from a previous
        # EXE build's _MEIPASS temp dir, from a Python install that's
        # since been uninstalled) may have left schtasks /tr commands
        # pointing at paths that don't exist anymore. Re-syncing every
        # kin's cron_entries against schtasks rewrites them to point at
        # this process's invocation shape (Hearthkin.exe --cron for the
        # frozen build; python + canonical script path for source).
        # Runs in a daemon thread because schtasks subprocess calls
        # block for ~100ms each and we don't want to delay UI startup
        # for users with many kins. Silent on success; errors land in
        # ~/.hearthkin/logs/cron_errors.log.
        try:
            threading.Thread(
                target=cron_helpers.sync_all_kins_blocking,
                daemon=True,
            ).start()
        except Exception:
            pass
        # wx.Timer that polls ~/.hearthkin/cron_requests/ every 5 seconds
        # for request files dropped by the cron subprocess when it fired
        # while Hearthkin was running. Started below after the UI is built.
        self._cron_timer = None
        # Proactive heartbeat state (rides the cron timer; app-running only).
        self._heartbeat_last = {}       # kin -> epoch seconds of last heartbeat
        self._heartbeat_last_scan = 0.0  # throttle the per-kin scan to ~1/min

        self.bots = {}  # agent_name -> TelegramBot
        self.discord_bots = {}  # agent_name -> DiscordBot
        self._edit_kin_dialog = None  # set by EditKinDialog while open

        # Image attachment staged for the next outgoing user turn (1-on-1
        # desktop only — rooms and a separate Telegram attach UI are
        # deferred). String is an ABSOLUTE filesystem path the user
        # picked from disk; on send, we copy it into the kin's
        # attachments/ dir under a content-hash name, reference it on
        # the persisted user message, and clear this back to None.
        self._pending_attachment = None
        # Same idea, but for already-saved attachments — the webcam
        # capture path writes the JPG bytes straight into the kin's
        # attachments/ dir and stages the resulting RELATIVE path
        # here. _send_message references whichever is set; the
        # webcam path skips the file-picker save step since the
        # bytes are already content-addressed on disk.
        self._pending_attachment_rel = None

        # Room state (when self.current_room is set, we're in room mode)
        self.current_room = None
        self.room_cfg = {}
        self.room_conversation = []
        self._room_round_count = 0      # total rounds since this room was loaded
        self._room_round_index = 0      # which member is currently speaking within a round
        self._room_round_order = []     # member names in this round's speaking order
        self._room_auto_count = 0       # consecutive auto-rounds since user last typed
        self._room_auto_mode = False    # auto-continue checkbox state
        self._room_paused = True        # True when between rounds (Continue button enabled)
        self._room_last_user_input = time.monotonic()
        self._room_active = False       # mid-round (a kin is currently streaming)

        self._auto_timer = None         # wx.Timer for inactivity check

        # Memory background-task state
        # (agent_name, scope_key) -> count. scope_key is "desktop" for
        # the unified surface (desktop chat + any Telegram surface with
        # share-with-desktop on), or "tg:user:<id>" / "tg:group:<id>"
        # for non-shared Telegram surfaces. Each scope has its own
        # distillation cadence — non-shared Telegram surfaces don't
        # tick the desktop counter, so a private DM doesn't trigger a
        # desktop distillation, and vice versa. All scopes distill
        # INTO the same memory.md (knowledge accumulates from every
        # surface), just at independent paces.
        self._messages_since_distill = {}
        # Maps agent_name -> time.time() when a distillation/consolidation
        # worker was kicked off. Used both as "already in flight, don't
        # start another" gate AND as a staleness detector: if a worker
        # has been "in flight" for more than 5 minutes, the call is
        # presumed wedged (hung network, missed wx.CallAfter, etc.) and
        # the next kickoff silently clears it so the user isn't blocked
        # forever. See _is_distill_in_flight().
        self._distilling = {}
        # The worker thread holding each kin's distillation slot, and when
        # we first noticed one had ended without reporting back. Liveness
        # is read from the thread rather than from elapsed time — see
        # _is_distill_in_flight for what the old clock-based watchdog cost.
        self._distill_threads = {}
        self._distill_dead_since = {}
        # agent_name -> characters of summary written so far by the call
        # holding that kin's slot. Absent means nothing is running; 0
        # means running but still reading. Fed by the worker thread,
        # read by the 5-second cue timer, cleared with the slot.
        self._distill_progress = {}

        # (kin, time_label) for every cron wake-up running on a background
        # thread right now. Feeds the confirm-on-close check: a cron turn is
        # the one kind of work with no visible signal at all, so quitting used
        # to abandon it silently. A set of tuples mutated from worker threads —
        # add/discard on a set are atomic under the GIL, and the reader only
        # ever takes a snapshot copy, so no lock is warranted.
        self._cron_workers = set()

        # Kin whose proactive heartbeat is running on a background thread right
        # now. Same job as _cron_workers and the same reason: without it,
        # quitting mid-heartbeat closed silently while a kin was part-way
        # through deciding whether to reach out. Set operations are atomic under
        # the GIL and the reader only snapshots, so no lock is warranted.
        self._heartbeat_workers = set()

        # Kin part-way through a park TURN on a background thread. A park turn
        # is several model calls and several real moves in a shared save, so
        # quitting through one abandons a kin mid-visit and can leave the game
        # half-tended. Same job as the two sets above, same reason it has to be
        # here rather than inferred: nothing else in this process knows.
        self._park_workers = set()

        # This turn's model, options and messages, so a park turn can ask the
        # kin for its next move without rebuilding the prompt. Set by
        # _send_message, read by _start_park_turn, generation-stamped so a
        # stale one is ignored rather than used.
        self._park_continuation = None

        # Last successful auto-consolidation timestamp per kin. Used by
        # the auto-after-distill trigger to honour a cooldown so a
        # consolidation that trims memory.md to just-under-threshold
        # followed by a fresh distillation that pushes it back over
        # doesn't immediately re-fire. Without this, a kin sitting near
        # the 20k-char MEMORY_CONSOLIDATE_THRESHOLD_CHARS line with
        # busy Telegram traffic produced a runaway spend — repeated
        # paid consolidation calls over a few hours. Manual button bypasses
        # this — the operator pressing Consolidate means they want it
        # NOW regardless of cadence.
        self._last_consolidation_at = {}

        # "Distill all surfaces" queue: agent_name -> list of remaining
        # scope_keys to distill, one per Memory-tab button press. Only
        # one scope's distillation runs at a time (the _distilling /
        # _is_distill_in_flight lock is per-kin), so the queue is
        # drained sequentially: each successful _on_distill_done kicks
        # off the next scope in the queue. Each scope still goes
        # through _distill_bite, so a huge surface only gets one bite
        # per "Distill all" press — re-press to take the next round.
        self._distill_queue = {}
        # "Walking from start" flags per (kin, scope) — set by the
        # Memory tab's "Redistill selected from start" button so
        # _on_distill_done knows to schedule the next chunk after
        # each completes. Initialized here (was previously read-only
        # via getattr(...) or {}) so any future .setdefault on this
        # dict doesn't AttributeError (audit H3).
        self._walking_from_start = {}

        self.logger = None
        self._setup_logging()

        self._build_ui()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        # NOTE: session-end events (EVT_QUERY_END_SESSION,
        # EVT_END_SESSION) MUST be bound on wx.App, not on a frame.
        # Frame-level bindings silently never fire. See main() for
        # the actual handlers — they set self._quitting so the
        # close chain takes the real-exit path instead of bouncing
        # into the tray.
        self.SetMinSize((760, 560))

        # System-tray icon. Stays alive across minimize-to-tray; only
        # destroyed by exit_from_tray. The mini-chat window is created
        # lazily the first time the user opens it from the tray menu.
        self._tray_icon = None
        self._mini_chat = None
        # _quitting is the explicit "user asked to fully exit, not
        # close-to-tray" flag the close handler reads.
        self._quitting = False
        try:
            if wx.adv.TaskBarIcon.IsAvailable():
                self._tray_icon = tray.HearthkinTaskBarIcon(self)
                if not getattr(self._tray_icon, "icon_visible", False):
                    append_failure_log(
                        "tray_failures.log", "init", "SetIcon",
                        RuntimeError(
                            "TaskBarIcon constructed but no visible icon set "
                            "(icon load fell through to fallback chain). "
                            "Close-to-tray will use Iconize() instead of "
                            "Hide() until this is resolved."
                        ),
                    )
            else:
                append_failure_log(
                    "tray_failures.log", "init", "IsAvailable",
                    RuntimeError(
                        "wx.adv.TaskBarIcon.IsAvailable() returned False — "
                        "this platform has no notification area. Close-to-"
                        "tray will minimize to the taskbar instead."
                    ),
                )
        except Exception as e:
            # Log to a real file (pythonw.exe has no visible stderr).
            try:
                append_failure_log(
                    "tray_failures.log", "init", "construct", e,
                )
            except Exception:
                pass

        # Cron request-file poll. Fires every 5 seconds; cheap (one
        # glob over a usually-empty dir). Started here, after _build_ui,
        # because the handler may need wx.CallAfter back to UI bits.
        self._cron_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_cron_timer_tick, self._cron_timer)
        self._cron_timer.Start(5000)

        # Conversation-file mtime poll. Fires every 5 seconds. Cheap —
        # one os.stat call. When the active kin's conversation.jsonl
        # has been written by an external process (Telegram bot in
        # shared mode, cron subprocess) since we last loaded it, this
        # picks up the new lines into self.conversation and re-paints
        # the chat display. Without this, you'd have to switch kins
        # and back to see Telegram messages that arrived while the
        # desktop was open with the kin loaded.
        self._conversation_mtime_seen = None
        self._conversation_poll_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_conversation_poll_tick,
                  self._conversation_poll_timer)
        self._conversation_poll_timer.Start(5000)


        # Point the shared Ollama-probe host at the kin we're about to
        # load, BEFORE the startup reachability probe fires below — its
        # thread launches here, ahead of _load_agent, so without this it
        # would probe localhost and could throw a false "Ollama not
        # running" advisory for an operator whose Ollama lives on a remote
        # box. _load_agent re-sets this per-kin once it runs.
        try:
            _last = self.config.get("last_agent", "")
            if _last and _last in list_agents():
                llm_backend.set_ollama_host(resolve_kin_ollama_host(
                    (load_agent_config(_last) or {}).get("ollama_host_name", "")))
        except Exception:
            pass

        # Materialise any registered prompt that has no file yet, so every
        # editable prompt is browsable in ~/.hearthkin/prompts/ instead of
        # appearing only once its code path happens to fire. Never touches an
        # existing file. Cheap (a stat per slug once the folder is populated)
        # and fail-soft, so it runs inline rather than on a thread.
        try:
            from kin_persistence import seed_all_app_prompts
            seed_all_app_prompts()
        except Exception:
            pass

        # Ollama-detection probe. Fresh installs frequently launch
        # without realizing Hearthkin needs Ollama running on
        # localhost:11434 to use local models. The probe is a daemon
        # thread (no startup delay), and the advisory dialog only
        # shows on negative result — and only if the user hasn't
        # already dismissed it via "don't show again."
        if not self.config.get("ollama_warning_dismissed", False):
            threading.Thread(
                target=self._check_ollama_on_startup,
                daemon=True,
            ).start()

        # Background update check (opt-in via preferences). Quiet
        # surface: Activity field + NVDA speech only when newer
        # available, so it doesn't interrupt startup flow.
        if self.config.get("auto_check_updates_on_startup", False):
            threading.Thread(
                target=self._check_for_updates_worker_quiet,
                daemon=True,
            ).start()

        # Non-modal nudge if a shipped prompt default has improved past the
        # operator's seeded copy. Activity field + NVDA speech only, pointing
        # at Tools → Prompt updates — opt-in, nothing changes until they open
        # that dialog and choose. Deferred so it lands after startup settles.
        wx.CallLater(2500, self._maybe_nudge_prompt_updates)

        # Pick up any "redistill from start" that was part-way through
        # when Hearthkin last closed. The walk flag used to live only in
        # this process, so quitting ended a walk permanently and
        # silently — the progress survived on disk but nothing continued
        # it, and the only button offered reset it to zero. Announces
        # itself and is cancellable from Settings → Memory. Deferred so
        # it lands after startup settles, like the nudge above.
        wx.CallLater(4000, self._resume_pending_distill_walks)

        # Voice subsystem (TTS + STT via ElevenLabs). One engine per
        # frame; per-kin settings flow in on each speak call. The
        # engine reads the API key lazily via the resolver so a key
        # edit in Preferences applies immediately without re-init.
        # The on_async_error hook surfaces playback-worker exceptions
        # (HTTP 401 from missing TTS scope on the API key, network
        # failures, etc.) into the Activity field — without it those
        # would be silently swallowed inside the worker thread and the
        # user would hear silence with no explanation.
        def _voice_async_error(err_msg):
            try:
                wx.CallAfter(self._announce_unavailable, "voice",
                             f"Voice unavailable: {err_msg}")
            except Exception:
                pass
        self._voice_engine = voice_module.VoiceEngine(
            get_api_key=lambda: llm_backend.resolve_provider_key("elevenlabs"),
            on_async_error=_voice_async_error,
        )

        # Warm the speech-recognition model up in the background, so the
        # first dictation is instant rather than a wait.
        #
        # The first faster-whisper import in a process loads native
        # libraries and costs tens of seconds. Paying that AFTER someone
        # has already spoken is indistinguishable, from a chair, from the
        # app having hung — and the person it would happen to is the one
        # who chose to speak rather than type. Deferred past startup and
        # run on a daemon thread so it competes with nothing; it never
        # raises, so a warm-up that fails simply leaves the cost where it
        # already was.
        def _warm_dictation():
            try:
                merged = migrate_dictation_config(self.config.get("dictation"))
                if not merged.get("preload", True):
                    return
                threading.Thread(
                    target=stt.preload, args=(merged,), daemon=True,
                ).start()
            except Exception:
                pass
        wx.CallLater(6000, _warm_dictation)

        agents = list_agents()
        rooms = list_rooms()
        last_kind = self.config.get("last_target_kind", "kin")
        last_room = self.config.get("last_room", "")
        last = self.config.get("last_agent", "")
        if last_kind == "room" and last_room in rooms:
            self._load_room(last_room)
        elif last in agents:
            self._load_agent(last)
        elif agents:
            self._load_agent(agents[0])
        else:
            self._set_status("Welcome. Press 'New kin...' in the header to create your first kin.")
        # _load_agent/_load_room emit transient "Loaded X" status which the
        # revert timer would replace anyway in 4 seconds — but on a cold
        # start the user shouldn't have to wait for that timer to see
        # something useful. Paint the default once now.
        self._status_revert_to_default()

        # Enable/disable Edit Room button based on whether rooms exist
        self._refresh_rooms_state()

        # Auto-start any bots that were left enabled
        for ag in agents:
            cfg = load_agent_config(ag)
            if cfg.get("telegram", {}).get("enabled"):
                self._start_bot_for(ag)
            if cfg.get("discord", {}).get("enabled"):
                self._start_discord_bot_for(ag)


def main():
    # --cron short-circuit. When the frozen EXE is invoked by Windows
    # Task Scheduler with `Hearthkin.exe --cron --kin X --entry-index N`,
    # we behave as the cron subprocess: skip wx.App construction, skip
    # the single-instance check (a running GUI is fine; the cron path
    # already checks the .running.lock and routes accordingly), run the
    # wake-up, and exit. Source devs running `python hearthkin.pyw` can
    # use the same flag for parity, though they usually invoke
    # hearthkin_cron.py directly.
    if "--cron" in sys.argv[1:]:
        argv = [a for a in sys.argv[1:] if a != "--cron"]
        import hearthkin_cron
        sys.exit(hearthkin_cron.main(argv))

    app = wx.App(False)

    # Single-instance guard. Hearthkin owns a system-tray icon,
    # cron polling, Telegram bots — running two copies in parallel
    # would double-fire all of those and fight over per-kin files.
    # SingleInstanceChecker creates a per-user lock file under the
    # AppData dir; if a second launch sees the lock, we try to bring
    # the existing instance's window forward and exit silently.
    #
    # Per-user (not per-machine) — multiple users on one Windows
    # box can each run their own Hearthkin without clobbering each
    # other.
    import getpass
    instance_name = f"Hearthkin-singleton-{getpass.getuser()}"
    instance_checker = wx.SingleInstanceChecker(instance_name)
    if instance_checker.IsAnotherRunning():
        # Try to surface the existing instance so the user sees
        # something. If that fails (e.g. existing instance is hidden
        # in the tray with no top-level window visible), the tray
        # icon is still there for them to find.
        _bring_existing_hearthkin_to_front()
        return

    # Stash on the app so the checker stays alive for the whole
    # process lifetime (it releases its lock on garbage collection).
    app._hearthkin_instance_checker = instance_checker

    frame = Hearthkin()
    frame.Show()
    # wxFrame.Show() doesn't reliably bring the window to the
    # foreground on Windows — Explorer can keep focus during fast
    # launches, and Windows' foreground-lock protection silently
    # rejects focus switches in some cases. For an NVDA user, "window
    # opened but focus stayed elsewhere" reads as "didn't launch":
    # they hear nothing, see nothing. Belt-and-suspenders: wx Raise +
    # Win32 SW_RESTORE + SetForegroundWindow on the main frame's HWND
    # (same pattern as _bring_existing_hearthkin_to_front uses for
    # duplicate launches), then focus the chat input so NVDA has
    # something specific to announce.
    try:
        frame.Raise()
        _force_foreground(frame.GetHandle())
        if hasattr(frame, "input_box") and frame.input_box:
            frame.input_box.SetFocus()
    except Exception:
        pass

    # Restart Manager / Windows shutdown handling. Inno Setup with
    # CloseApplications=yes (and `shutdown -s`, `logoff`, reboot)
    # asks running apps to close via WM_QUERYENDSESSION / WM_ENDSESSION.
    # wxPython routes the corresponding events to the wx.App object,
    # NOT to frames — frame-level bindings silently never fire (this
    # is the bug v0.2.13 shipped with). Bind on the app instance so
    # the handlers actually run.
    #
    # Behavior: set frame._quitting=True before allowing wx's default
    # session-end processing to proceed. The default handler generates
    # EVT_CLOSE on the frame, and the frame's _on_close sees the
    # quitting flag and takes the real-exit path (save state, stop
    # bots, drop cron lock, destroy tray) instead of the close-to-tray
    # hide. Without this, the close request bounces into the tray and
    # the tray icon keeps the process alive past the OS's timeout —
    # the installer either stalls visibly or appears to hang
    # indefinitely (v0.2.13 regression).
    #
    # event.Skip() is critical: don't Veto (we want to allow the
    # shutdown to proceed), and don't swallow the event (we want
    # wx's default handler to fire the EVT_CLOSE on each frame).
    def _on_app_query_end_session(event):
        frame._quitting = True
        event.Skip()

    def _on_app_end_session(event):
        # Defensive: some shutdown paths skip the query phase.
        frame._quitting = True
        event.Skip()

    app.Bind(wx.EVT_QUERY_END_SESSION, _on_app_query_end_session)
    app.Bind(wx.EVT_END_SESSION, _on_app_end_session)

    app.MainLoop()
    # Hard backstop: MainLoop only returns on a real exit (close-to-tray
    # keeps it running). If a non-daemon thread spun up by a third-party
    # library, or any wx remnant, would otherwise keep the interpreter
    # alive, terminate now so an explicit quit ALWAYS ends the process and
    # releases the single-instance lock. State was already persisted in
    # _on_close, so there's nothing left to flush.
    os._exit(0)


if __name__ == "__main__":
    main()
