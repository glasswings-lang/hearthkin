"""StatusVoiceMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    DEFAULT_CONFIG, _num_ctx_of, agent_dir, estimate_tokens, llm_backend,
    migrate_dictation_config,
    load_memory, load_soul, nvda_speak, play_alert, play_chime,
    strip_model_annotation, stt, threading, time, voice_module, wx,
)
from kin_persistence import (
    memory_log_folder_signature, refresh_memory_log_index,
)


class StatusVoiceMixin:

    # --- Status & close --- #

    def _invalidate_kin_text_cache(self, kin_name):
        """Re-read soul.md and memory.md from disk for `kin_name` if
        it's the currently-loaded kin. Called by EditKinDialog after
        Save soul / Save memory so the in-memory cache used by
        _update_token_display and the Usage tab stays current.
        Silently no-ops if the named kin isn't currently active —
        cache is only kept for the current kin, others read fresh
        when loaded."""
        if not kin_name or kin_name != self.current_agent:
            return
        try:
            self._soul_cache = load_soul(kin_name) or ""
        except Exception:
            pass
        try:
            self._memory_cache = load_memory(kin_name) or ""
        except Exception:
            pass

    def _refresh_kin_text_cache_if_stale(self):
        """Re-read soul.md / memory.md when the files have changed on
        disk since the cache was filled. Cheap: two stat() calls.

        `_invalidate_kin_text_cache` covers the path where the PERSON
        edits these in Settings, and it covered every writer that existed
        when it was written. It no longer does, and the gap is invisible:
        **a kin writing its own memory.md with a file tool never touches
        it.** The cache is filled once when the kin is selected, so from
        that moment the kin cannot see anything it writes to its own
        memory for the rest of the session.

        The symptom is easy to misread. A kin can write a full memory.md
        through a tool call, then later compose a memory entry into the
        chat and say it has no memory file to put it in. It is not
        confabulating — the file is on disk and its system prompt
        contains none of it. From the outside that looks like a model
        with no memory. It is a stale string in this process.

        Validating against mtime instead of adding another invalidation
        call site on purpose: the writers are the kin's tools, the
        Settings dialog, the distiller, the consolidation pass and a cron
        subprocess in a DIFFERENT PROCESS entirely — which cannot call an
        in-process invalidator at all. A signature check covers every one
        of them, including the ones nobody has written yet."""
        name = getattr(self, "current_agent", "")
        if not name:
            return
        try:
            d = agent_dir(name)
            sig = []
            for fname in ("soul.md", "memory.md"):
                p = d / fname
                try:
                    st = p.stat()
                    sig.append((st.st_mtime_ns, st.st_size))
                except OSError:
                    sig.append(None)      # absent is a state too — a file
                    # appearing later must count as a change
            # Which depth logs exist, by name. The '## Memory logs'
            # index inside memory.md is code's to maintain, and it was
            # only ever rebuilt as a side effect of distillation — so a
            # kin whose distillation is behind writes log after log into
            # an index that stopped listing them, and can then only find
            # them by opening each one. Rebuilt here instead, when the
            # SET of logs changes. Names only: editing a log's contents
            # must not rewrite memory.md and throw the prompt cache away.
            logs_sig = memory_log_folder_signature(name)
            sig = (name, tuple(sig), logs_sig)
        except Exception:
            return
        if getattr(self, "_kin_text_sig", None) == sig:
            return
        if logs_sig != (getattr(self, "_kin_text_sig", None) or (None, None, None))[-1]:
            try:
                if refresh_memory_log_index(name):
                    # memory.md just changed on disk; re-stat so the
                    # signature stored below matches what we're about to
                    # read, or the next send would refresh all over again.
                    try:
                        st = (d / "memory.md").stat()
                        sig = (name, (sig[1][0], (st.st_mtime_ns, st.st_size)),
                               logs_sig)
                    except OSError:
                        pass
            except Exception:
                pass
        self._kin_text_sig = sig
        try:
            self._soul_cache = load_soul(name) or ""
        except Exception:
            pass
        try:
            self._memory_cache = load_memory(name) or ""
        except Exception:
            pass

    def _speak_status_phase(self, phase):
        """Speak a phase transition through NVDA, **once per phase per
        turn**. Used to announce model state ("Thinking", "Typing",
        "Still loading") without requiring the user to tab into the
        Activity field. The status_label TextCtrl still updates for
        sighted users and for any blind user who does focus it; the
        speech just makes the change audible regardless of where focus
        is.

        Guarded by `self._spoken_phase` so the same phase doesn't speak
        twice in a row (e.g. a streaming model emits dozens of content
        chunks — only the first one transitions us into "Typing"). The
        guard resets at the start of each turn (see _reset_spoken_phase)
        so the next turn can announce again.

        Respects the NVDA-mode preference: off → no speech (and no flood
        no matter how many phase changes happen).
        """
        if getattr(self, "_spoken_phase", None) == phase:
            return
        self._spoken_phase = phase
        if self.config.get("nvda_mode") == "off":
            return
        try:
            nvda_speak(phase)
        except Exception:
            pass

    def _reset_spoken_phase(self):
        """Clear the phase guard so the next turn can announce its first
        phase. Called from _on_send (1-on-1) and the room turn loop at
        the start of each kin's turn."""
        self._spoken_phase = None
        # Also reset the per-turn "feature unavailable" announce guard
        # so the Activity field announces voice / audio / etc. failures
        # ONCE per turn (not every sentence or every chunk).
        self._unavailable_announced = set()

    def _announce_unavailable(self, feature_key, message):
        """Drop a one-line `feature unavailable: <reason>` into the Activity
        field AND speak it via NVDA, gated to once per turn per feature key.

        This is the user-visible companion to silent fallbacks that would
        otherwise leave the user wondering why something didn't happen
        (voice didn't speak, image got dropped, audio got rejected, etc.).
        The feature_key (string like "voice", "image", "audio_in") groups
        related announces so multiple sentence-boundary calls during one
        turn don't repaint the Activity field with the same notice.

        nvda_speak fires alongside _set_status because the Activity field
        is only audible when the user tabs to it, and a 4-second status
        message can come and go before a busy user notices. Speaking the
        notice means the user always hears it, even if they're focused
        elsewhere in the app.
        """
        if not hasattr(self, "_unavailable_announced"):
            self._unavailable_announced = set()
        if feature_key in self._unavailable_announced:
            return
        self._unavailable_announced.add(feature_key)
        try:
            self._set_status(message, speak=True)
        except Exception:
            pass

    def _dictation_cfg(self):
        """The app-level dictation settings, merged key-by-key over the
        defaults.

        Read through the migration rather than straight, for two
        reasons that are really one. A setting added in a later version
        has to reach an install whose config file predates it — the
        app's top-level merge is shallow, so a nested dict would
        otherwise freeze at the shape it was first saved with, and an
        option nobody can receive is the same as no option. And these
        settings have already changed shape once, so a file written
        under the old one must still say what its owner meant."""
        try:
            return migrate_dictation_config(self.config.get("dictation"))
        except Exception:
            return dict(DEFAULT_CONFIG.get("dictation") or {})

    def _dictation_ready(self):
        """Can dictation work at all right now? Returns (ok, reason).

        Cheap by design — this is called on every kin switch to decide
        whether the Talk button is shown, so it must not import the
        speech library (tens of seconds the first time) or touch the
        network. It answers "is a transcription model configured and
        reachable in principle", not "is the microphone plugged in";
        that question is the Talk button's to answer, out loud, when
        pressed."""
        d = self._dictation_cfg()
        route = stt.route_for(d.get("model"), d.get("host"))
        if route == stt.ROUTE_LOCAL:
            if not stt.available_locally():
                return False, (
                    "the speech library is not installed on this computer, "
                    "and no other machine is named to do it instead")
            return True, ""
        if route == stt.ROUTE_SERVER:
            # Reachability is deliberately NOT checked here. A machine
            # that is asleep is a thing to be told about when you press
            # Talk, not a reason to remove the button — hiding it would
            # make a sleeping box look like a missing feature.
            return True, ""
        try:
            key = llm_backend.resolve_provider_key("elevenlabs")
        except Exception:
            key = ""
        if not (key or "").strip():
            return False, "no ElevenLabs API key is set"
        return True, ""

    def _refresh_talk_button_visibility(self):
        """Show the Talk button whenever dictation can actually work.

        It used to be shown only when the kin had a paid ElevenLabs
        voice picked — which tied speaking TO a kin to that kin
        speaking back, two unrelated things, and put dictation behind a
        subscription. Nothing about putting your words into the input
        box depends on the kin having a voice, and for anyone who finds
        typing hard this is the difference between the app being usable
        and not.

        Shown in rooms too. Dictation is input; the room's unsolved
        question is whose voice reads the replies OUT, which is a
        different problem and not this button's."""
        if not hasattr(self, "talk_btn"):
            return
        ok, _why = self._dictation_ready()
        should_show = bool(
            (self.current_agent or self.current_room) and ok
        )
        try:
            if should_show:
                self.talk_btn.Show()
            else:
                self.talk_btn.Hide()
            parent = self.talk_btn.GetParent()
            if parent is not None:
                parent.Layout()
        except Exception:
            pass

    def _on_talk(self, _event):
        """Click-to-toggle dictation. First press starts the
        microphone; second press stops it, transcribes, and puts the
        words in the input box.

        Click-toggle rather than hold-to-talk because focus loss during
        a hold kills the recording on most desktop UI stacks."""
        engine = getattr(self, "_voice_engine", None)
        if engine is None:
            self._set_status("Dictation is unavailable.", speak=True)
            return

        if not self._is_recording:
            ok, why = self._dictation_ready()
            if not ok:
                self._set_status(
                    f"Cannot dictate: {why}. See Preferences → Dictation.",
                    speak=True)
                return
            # Flip the flag before the microphone actually opens, so a
            # fast second press is understood as "stop" rather than
            # starting a second recording.
            self._is_recording = True
            self._dictation_gen = getattr(self, "_dictation_gen", 0) + 1
            self.talk_btn.SetLabel("Stop talking")
            self._set_status("Listening — press Stop talking when done.",
                             speak=True)
            # The microphone opens a beat after the press, and the tone
            # in _begin_dictation_capture marks the moment it is really
            # open. That gap is for the PERSON and for the audio device:
            # a stream takes a moment to start, and "press and speak in
            # one motion" otherwise clips the first word. It is not an
            # attempt to finish speaking before the microphone opens —
            # the screen reader is very often still talking at that
            # point, and that is fine.
            #
            # Being told the recording has started matters more than a
            # theoretically cleaner recording, and in practice the two
            # do not conflict: this has not put the announcement into a
            # transcript. If a screen reader ever does end up in one,
            # the answer is to silence speech (NVDA+S cycles speech
            # mode) — NOT to tell somebody to buy headphones, which
            # assumes a spare that a person who most needs dictation may
            # well not have.
            wx.CallLater(450, self._begin_dictation_capture,
                         self._dictation_gen)
        else:
            self._is_recording = False
            self.talk_btn.SetLabel("Talk")
            self.talk_btn.Disable()
            self._set_status("Transcribing…", speak=True)
            threading.Thread(
                target=self._transcribe_worker, daemon=True,
            ).start()

    def _begin_dictation_capture(self, gen):
        """Open the microphone, a beat after the press, and mark the
        moment with a tone.

        The tone is the part that means "speak now" — a sound rather
        than a word, so it reads instantly and does not have to wait its
        turn in the screen reader's queue behind whatever else is being
        said. The spoken announcement still happens; this just does not
        depend on it having finished.

        `gen` guards against a stale timer: press Talk, change your
        mind, press it again to stop — this fires afterwards and must
        not reopen the microphone behind you."""
        if gen != getattr(self, "_dictation_gen", 0):
            return
        if not self._is_recording:
            return
        engine = getattr(self, "_voice_engine", None)
        if engine is None:
            return
        try:
            engine.start_recording()
        except Exception as e:
            self._is_recording = False
            try:
                self.talk_btn.SetLabel("Talk")
            except Exception:
                pass
            self._set_status(f"Could not start the microphone: {e}",
                             speak=True)
            return
        # A short rising tone: the "speak now" signal. Deliberately a
        # tone and not a word — see the comment in _on_talk.
        try:
            play_chime(freq=1046, dur=90,
                       volume=float(self.config.get("chime_volume", 0.8)),
                       name="listening")
        except Exception:
            pass

    def _transcribe_worker(self):
        """Background: close the microphone, transcribe, put the words
        into the input box on the UI thread.

        Errors land in the Activity field and are spoken, rather than
        in a modal, so nothing blocks and nothing has to be dismissed
        before the next attempt."""
        settings = self._dictation_cfg()

        # If the speech model has not been loaded in this process yet,
        # say so before the wait rather than after it. The first load
        # can take tens of seconds and is indistinguishable from a
        # hang if nothing says what is happening.
        try:
            if stt.route_for(settings.get("model"),
                             settings.get("host")) == stt.ROUTE_LOCAL:
                warm = stt.loaded_as(
                    settings.get("model"),
                    settings.get("device") or "auto",
                    settings.get("compute") or "auto")
                if warm is None:
                    wx.CallAfter(
                        self._set_status,
                        "Loading the speech model — this happens once.",
                        True)
        except Exception:
            pass

        text = ""
        err = None
        try:
            text = self._voice_engine.stop_recording_and_transcribe(settings)
        except Exception as e:
            err = str(e)

        def finish():
            try:
                self.talk_btn.Enable()
            except Exception:
                pass
            if err:
                self._set_status(f"Dictation failed: {err}", speak=True)
                return
            if not text:
                self._set_status("Nothing was heard.", speak=True)
                return
            # Append rather than overwrite, so several passes can be
            # dictated one after another, and so anything already typed
            # is not destroyed by pressing Talk.
            try:
                existing = self.input_box.GetValue()
                joined = (existing + " " + text).strip() if existing else text
                self.input_box.SetValue(joined)
                self.input_box.SetInsertionPointEnd()
                self.input_box.SetFocus()
            except Exception:
                pass
            if settings.get("auto_send"):
                self._set_status("Sending what you said.", speak=True)
                try:
                    self._on_send(None)
                except Exception:
                    pass
                return
            # Speak the transcript back. Without this the only way to
            # know what was heard is to read the input box, and a
            # wrong word noticed before sending is a correction rather
            # than a misunderstanding.
            self._set_status(f"Heard: {text}", speak=True)

        wx.CallAfter(finish)

    def _maybe_speak_sentence(self, sentence):
        """Speak a piece of the kin's reply aloud. Called from the
        chat-stream chunk handler at every sentence boundary AND from
        the stream-done handler for the unpainted tail (and, on the
        tool-loop path, once with the whole reply — that path lands the
        entire content here in one call).

        Two independent audio routes, chosen per call:

          1. Paid per-kin voice (ElevenLabs) — used when the kin has
             voice enabled AND a voice_id picked AND the engine is up.
             That voice owns the audio for this kin.

          2. Free NVDA readout — used when the "NVDA reads replies"
             preference is set to "Streaming" (nvda_mode == "stream").
             This is the volama-style "hear each sentence as it
             streams" path — sentence-by-sentence for a tool-less kin,
             the whole reply in one call for a tool kin until the
             tool-loop streaming keystone lands. Distinct from "Full
             reply" (nvda_mode == "full"), which reads the whole reply
             once at completion via _on_stream_done's mode branch. It
             goes through the NVDA Controller Client's speech API
             (nvda_speak), which queues sentences in order and — unlike
             the visual AppendText paint — does NOT touch the UIA
             text-change event layer, so it carries none of the
             streaming-flood crash risk documented in the "Critical
             NVDA gotcha" note. The two routes are mutually exclusive
             per call so a kin with a paid voice never double-speaks.

        No-op when the sentence is whitespace-only, no kin is loaded,
        or (route 2) nvda_mode is not "stream".
        """
        if not (sentence or "").strip():
            return
        if not self.current_agent:
            return
        engine = getattr(self, "_voice_engine", None)
        v_cfg = (self.agent_cfg or {}).get("voice") or {}
        voice_enabled = bool(v_cfg.get("enabled", False))
        voice_id = (v_cfg.get("voice_id") or "").strip()
        paid_voice_active = engine is not None and voice_enabled and bool(voice_id)

        if paid_voice_active:
            # Announce the Speaking phase once per turn (same guard as
            # Thinking / Typing). After this fires, subsequent sentence-
            # boundary speak calls don't re-announce.
            if getattr(self, "_spoken_phase", None) != "Speaking":
                self._set_status("Speaking…")
                self._speak_status_phase("Speaking")
            try:
                engine.speak_sentence(sentence, v_cfg)
            except voice_module.VoiceEngineError as e:
                # Catch the structured error specifically so the user gets
                # a real reason ("no ElevenLabs API key configured") rather
                # than a swallowed-failure mystery. Generic Exception still
                # falls through to the bare except below so TTS never
                # crashes the chat loop.
                self._announce_unavailable("voice", f"Voice unavailable: {e}")
            except Exception:
                self._announce_unavailable(
                    "voice",
                    "Voice unavailable: unexpected TTS error (see logs).",
                )
            return

        # Paid voice not handling it. If the kin has voice enabled but
        # no voice_id picked, surface that once per turn (unchanged
        # behavior) — but still fall through to the NVDA readout below
        # so the reply isn't silently lost.
        if voice_enabled and not voice_id:
            self._announce_unavailable(
                "voice",
                "Voice unavailable: no voice picked for this kin (Settings → Voice).",
            )

        # Route 2: free NVDA readout of the reply content itself — only
        # in "Streaming" mode ("stream"), where the reply is spoken live
        # as it arrives. ("Full reply" reads the whole thing once at
        # completion via _on_stream_done; "Short" speaks a brief "Reply
        # ready" notice; "Off" stays silent.)
        if (self.config.get("nvda_mode") or "off") != "stream":
            return
        try:
            nvda_speak(sentence)
        except Exception:
            pass

    def _set_status(self, msg, speak=False):
        # SetValue (not SetLabel) — TextCtrl's displayed text comes from
        # the value, and SetLabel on a TextCtrl is inherited from wxWindow
        # and is a no-op for the visible content.
        self.status_label.SetValue(msg)
        # The single "say it, don't just print it" pipe. speak=True is for
        # important user-facing one-offs — errors, "no reply", hangs, action
        # results — that a screen-reader user would otherwise miss because the
        # Activity field only updates visually. It speaks regardless of
        # nvda_mode (which deliberately gates the flood-prone phase/reply
        # readout, not critical notices). Callers pass speak=True here instead
        # of duplicating a bespoke nvda_speak() call afterward.
        if speak:
            try:
                nvda_speak(msg)
            except Exception:
                pass
        # After 4 seconds of no new _set_status call, revert to a
        # computed context line (kin/room + model + token usage). Means
        # the status bar always reads as "something useful" when idle,
        # not just stale notifications or "Ready.". Each new call
        # cancels the previous timer so rapid-fire updates don't fight.
        pending = getattr(self, "_status_revert_timer", None)
        if pending is not None and pending.IsRunning():
            pending.Stop()
        try:
            self._status_revert_timer = wx.CallLater(
                4000, self._status_revert_to_default,
            )
        except Exception:
            pass

    def _status_revert_to_default(self):
        """Replace the status bar text with the computed context line.
        Called on a timer after the last _set_status call. Safe to call
        any time — composes the line from current state."""
        # Skip during a kin load — agent_cfg / current_agent are
        # partially set and the composed line would read stale (audit
        # H23). The next _set_status call after the load finishes
        # will re-arm the timer.
        if getattr(self, "_loading_agent", False):
            return
        # While a reply is in flight, the idle summary line is the wrong
        # thing to show — it tells the operator NOTHING about the wait
        # they're sitting through (worst during a multi-minute cold model
        # load, where this used to revert to the static summary after 4s
        # and leave the field uninformative for the rest of the load).
        # Instead keep the Activity field on a live, self-refreshing
        # progress line, and re-arm so the elapsed counter stays current.
        # The moment the turn ends (_streaming goes False) the next revert
        # falls through to the idle summary below.
        if getattr(self, "_streaming", False):
            try:
                self.status_label.SetValue(self._compose_in_flight_status())
            except Exception:
                pass
            try:
                pending = getattr(self, "_status_revert_timer", None)
                if pending is not None and pending.IsRunning():
                    pending.Stop()
                self._status_revert_timer = wx.CallLater(
                    4000, self._status_revert_to_default,
                )
            except Exception:
                pass
            return
        try:
            self.status_label.SetValue(self._compose_default_status())
        except Exception:
            pass

    def _own_background_on_the_model(self, include_foreground=False,
                                     skip_bot=None):
        """Short phrase naming whatever is holding the model right now, or ""
        when nothing is.

        Ollama answers one request at a time. So when anything else has the
        model, a reply the person just sent doesn't fail — it *queues*, and
        produces nothing at all until that finishes. A distillation bite
        routinely runs thirteen minutes.

        Nothing said so, and two things went wrong because of it. The
        streaming watchdog declared the waiting turn a hang after five
        minutes and painted "[no response — possible hang]" over a turn that
        was perfectly healthy and simply queued. And, worse than any of the
        machinery, the person stopped sending: not knowing whether the model
        was free meant every message carried a "should I even send this, will
        it get read" — which is a cost this app has no business imposing.

        So: name it. This answers the one question that removes both problems
        — is something else the reason nothing is coming back?

        **`include_foreground` decides whether another live CONVERSATION
        counts, and the honest answer differs by surface.** In the main window
        the person can see a reply arriving and a room round running, so
        naming those back at them is noise — the desktop callers leave it off.
        Over Telegram they can see none of it: a kin busy with the desktop,
        with a room, or with somebody else's DM is invisible from there and
        waits just as long as a distillation does. That surface passes True.

        `skip_bot` names one kin whose remote turn to ignore — for the bot
        asking about a turn of its own, in the same chat as the person it is
        about to answer. They just sent that message; telling them their kin
        is replying to them is exactly the ordinary turn-taking this app
        deliberately doesn't narrate.

        Order is rough priority: the long invisible things first, live
        conversations after, because "it is distilling" is more use than "it
        is talking to someone" when both are true.

        Fails open to "" throughout — an unexplained wait is a much smaller
        harm than a status line that raises, and this runs on every repaint."""
        try:
            for kin in sorted(getattr(self, "_distilling", None) or {}):
                return f"{kin} is saving notes to its memory"
        except Exception:
            pass
        try:
            for kin, when in sorted(getattr(self, "_cron_workers", None) or set()):
                return f"{kin} is answering a scheduled wake-up"
        except Exception:
            pass
        try:
            # A scheduled wake-up can also run in the standalone cron process,
            # which shares no state with us and reports through a marker file.
            # Imported here rather than at module scope: a NameError would be
            # swallowed by the guard and this probe would silently never fire,
            # which is exactly the class of bug this method exists to end.
            from frame_shared import cron_helpers
            for kin, when in sorted(cron_helpers.cron_running_kin()):
                return f"{kin} is answering a scheduled wake-up"
        except Exception:
            pass
        try:
            for kin in sorted(getattr(self, "_heartbeat_workers", None) or set()):
                return f"{kin} is deciding whether to reach out"
        except Exception:
            pass
        # A kin mid-reply to somebody else holds the model exactly as firmly
        # as a distillation does. Each bot answers for its own remote turn
        # (it takes the lock rather than letting us read _active_turn
        # half-written), and `skip_bot` drops the one turn the asker already
        # knows about.
        if include_foreground:
            try:
                for name, bot in sorted((getattr(self, "bots", None) or {}).items()):
                    if skip_bot is not None and name == skip_bot:
                        continue
                    try:
                        line = bot.active_turn_label()
                    except Exception:
                        line = None
                    if line:
                        return line
            except Exception:
                pass
            try:
                if getattr(self, "_room_active", False):
                    room = getattr(self, "current_room", None) or "a room"
                    return f'the room "{room}" is part-way through a round'
            except Exception:
                pass
            try:
                if getattr(self, "_streaming", False) and self.current_agent:
                    return (f"{self.current_agent} is part-way through a "
                            f"reply in the main window")
            except Exception:
                pass
        return ""

    def _compose_in_flight_status(self):
        """Live progress line shown in the Activity field while a reply is
        being generated, in place of the idle summary. Distinguishes the
        cold-load / prefill wait (no first token yet) from an actively
        arriving reply, and shows elapsed seconds so a long wait reads as
        progressing rather than hung. Kept fresh by the ~4s revert re-arm
        in _status_revert_to_default."""
        started = getattr(self, "_stream_started_at", None)
        try:
            elapsed = int(time.monotonic() - started) if started else 0
        except Exception:
            elapsed = 0
        if getattr(self, "_stream_chunks_seen", 0) > 0:
            return f"Receiving reply… {elapsed}s elapsed"
        # If the app's own background work has the model, say so and say what
        # it means. "Working…" with no cause is what turned a queued turn into
        # a worry about whether the message would be read at all.
        holding = self._own_background_on_the_model()
        if holding:
            return (
                f"Waiting on the model — {holding}. Your message is queued "
                f"and will be answered when that finishes; nothing is lost. "
                f"{elapsed}s elapsed."
            )
        timeout_min = getattr(self, "_stream_watchdog_minutes", 5)
        return (
            f"Working… {elapsed}s — model loading or reading your "
            f"message (no reply token yet). Cold starts and big prefills can "
            f"take minutes; watchdog at {timeout_min} min."
        )

    def _compose_default_status(self):
        """Build the persistent context line shown when no transient
        message is active. Format:
          kin:  "Kin: <name> · <model> · <pct>% cap"
          room: "Room: <name> · N members · <pct>% cap"
          none: "No kin or room loaded — pick one from the Chat tab."
        Token percent uses the effective ceiling (num_ctx - 2K headroom)
        to match what actually gates the chat path."""
        if self.current_room is not None:
            room = self.current_room
            members = (self.room_cfg or {}).get("members") or []
            convo_text = "\n".join(
                (m.get("content") or "") for m in (self.room_conversation or [])
            )
            tokens = estimate_tokens(convo_text)
            # Rooms don't have a single num_ctx (each kin's varies). Just
            # show the raw token count — the Usage tab has the full
            # per-kin breakdown.
            return (
                f"Room: {room} · {len(members)} member"
                f"{'s' if len(members) != 1 else ''} · "
                f"~{tokens:,} tokens in convo"
            )
        if not self.current_agent:
            return "No kin or room loaded — pick one from the Chat tab."
        cfg = self.agent_cfg or {}
        model = strip_model_annotation(str(cfg.get("model", "") or "")).strip()
        num_ctx = _num_ctx_of(cfg)
        effective = max(512, num_ctx - 2000)
        # AUTHORITATIVE: use the provider's reported prompt-tokens from
        # the most recent send. That's what actually went out on the wire
        # post-truncation. Same fix shape as commit 291a6d1 applied to
        # context_status — see _update_token_display for the full
        # rationale. Fall back to a capped estimate when no real number
        # exists yet (first turn of a session).
        try:
            real_in = llm_backend.last_reported_prompt_tokens(
                self.current_agent)
        except Exception:
            real_in = None
        if real_in:
            gauge = real_in
            gauge_label = ""
        else:
            soul_text = self._soul_cache
            memory_text = self._memory_cache
            convo_total = self._conversation_token_estimate(model)
            raw_total = (
                estimate_tokens(soul_text)
                + estimate_tokens(memory_text)
                + convo_total
            )
            # Cap the estimate at effective ceiling — actual sends are
            # truncated to fit, so a 10x archive shouldn't render as
            # 1000% cap when the real send will be ~98%.
            gauge = min(raw_total, effective)
            gauge_label = " est"
        pct = (gauge / effective * 100) if effective > 0 else 0
        # Pct gets a warning marker so the user can spot truncation risk
        # from the status bar alone, without opening the Usage tab.
        if pct >= 100:
            pct_str = f"{pct:.0f}% cap (trimming oldest turns){gauge_label}"
        elif pct >= 85:
            pct_str = f"{pct:.0f}% cap (close){gauge_label}"
        else:
            pct_str = f"{pct:.0f}% cap{gauge_label}"
        model_str = model or "(no model)"
        line = f"Kin: {self.current_agent} · {model_str} · {pct_str}"
        # Append an "update available" suffix so a screen-reader user
        # discovers the news on any read of the default-state Activity
        # line, not just once at startup when other speech might bury
        # the announcement.
        upd = getattr(self, "_update_available_version", None)
        if upd:
            line += f" · v{upd} available"
        # Per-turn memory recall cue: how many of the kin's own depth-log
        # chunks the last desktop reply drew on, so recall is visible while
        # testing (the legibility surface the per-turn-retrieval design asks
        # for). NVDA-reachable because the Activity field reads this line.
        recalled = getattr(self, "_last_recall_used", None) or []
        if recalled:
            line += f" · {len(recalled)} memories recalled"
        # Say when the app's own background work has the model, BEFORE anything
        # is sent. Ollama answers one request at a time, so a message sent now
        # waits its turn — it is answered, but not for a while. Knowing that in
        # advance is the difference between choosing to wait and wondering
        # whether the message was worth sending at all. Idle-line only, so it
        # is there to be read rather than announced at anyone.
        holding = self._own_background_on_the_model()
        if holding:
            line += f" · busy: {holding} (a reply now will queue behind it)"
        return line

    # Tone per cue. Rising pitch tracks progress — request out (low), first
    # token (mid), finished (high) — so the sequence is legible by ear without
    # anyone having to learn which beep is which. `working` sits below all of
    # them and is short, because it repeats and must not become a nag.
    _CHIME_TONES = {
        "send": (440, 60),
        "first": (660, 60),
        "working": (330, 45),
        "done": (880, 140),
    }

    # A full octave below every tone above (330-880) — distinctly LOWER
    # than any existing cue, so it doesn't need to be consciously compared
    # against the others to tell apart by ear. Used only by
    # _tick_distilling_sound, below. This is the "still reading" tone: the
    # model has the bite and hasn't written a word of the summary yet.
    _DISTILLING_TONE = (220, 90)

    # Once the summary starts arriving, the cue climbs this ladder
    # instead. STEPS, not a smooth glide, and that's the whole design:
    # these beeps are 20 seconds apart, and nobody can hold a pitch in
    # their head for 20 seconds accurately enough to hear a 1% rise. Three
    # rungs, three or more semitones apart, mean a change of note is
    # unmistakable and the same note twice genuinely says "no real
    # progress since". Fewer, wider rungs beat more, finer ones here: a
    # distinction nobody can hear is the flat beep this replaced.
    #
    # None of them equals an existing cue's frequency (330/440/660/880),
    # and the whole ladder sits below the 440-880 reply octave, so a
    # distillation at full stretch is never mistaken for a kin answering.
    # The bottom rung is clear of the flat 220 reading tone, so "it has
    # started writing" lands on the first beep of the new phase.
    _DISTILLING_WRITE_STEPS = (262, 311, 392)

    # Characters of summary at which the ladder tops out. Runs are capped
    # at num_predict 6000 tokens but land far short of it in practice, so
    # the rungs are spread across the range runs actually occupy rather
    # than compressed into the bottom of a ceiling nothing reaches. A run
    # that overshoots simply holds the top note — "lots" is the honest
    # reading, and it was still climbing right up to that point.
    _DISTILLING_FULL_CHARS = 4000

    def _chime_setting(self, stage):
        """(enabled, volume) for one cue.

        Per-cue settings win; when absent, fall back to the old single
        `reply_chime` + `chime_volume` pair so an existing install keeps
        behaving exactly as it did. Nobody should get new noise from an
        upgrade they didn't ask for.
        """
        try:
            master = bool(self.config.get("reply_chime"))
        except Exception:
            master = False
        try:
            base = float(self.config.get("chime_volume", 0.8) or 0.0)
        except (TypeError, ValueError):
            base = 0.8
        try:
            entry = (self.config.get("chime_stages") or {}).get(stage)
        except Exception:
            entry = None
        if not isinstance(entry, dict):
            return master, base
        try:
            on = bool(entry.get("on", master))
        except Exception:
            on = master
        try:
            vol = float(entry.get("volume", base))
        except (TypeError, ValueError):
            vol = base
        return on, vol

    def _chime(self, stage):
        """Play one sound cue, if it's enabled.

        Cues: 'send' (request dispatched), 'first' (first token back),
        'working' (periodic, while a call is in flight), 'done' (complete).
        Safe to call from any thread.

        These carry real information for someone who can't see the window:
        a prefill can run four minutes with no output at all, and without a
        sound there is nothing to distinguish "thinking" from "died". They are
        sound rather than speech because a screen reader with character echo
        on has no free moment to say anything.
        """
        # Keep the tick-driven detector in step BEFORE deciding whether to
        # play. The desktop and room paths call this directly the instant they
        # send or finish, which is nicer than waiting up to 5s for the tick to
        # notice — but without priming, the tick would then see the same
        # transition and sound it a second time. Done unconditionally so the
        # state stays honest even when a cue is switched off.
        if stage in ("send", "done"):
            try:
                import time as _t
                self._work_sound_busy = (stage == "send")
                self._work_sound_last_tick = _t.monotonic()
            except Exception:
                pass
        on, vol = self._chime_setting(stage)
        if not on or vol <= 0:
            return
        freq_dur = self._CHIME_TONES.get(stage, (880, 140))
        # Pass the stage name so anyone can drop
        # ~/.hearthkin/sounds/{send,first,working,done}.wav to replace a tone
        # with something of their own.
        play_chime(freq_dur[0], freq_dur[1], volume=vol, name=stage)

    # Pitch range for the per-chunk redistill cue: one octave, low at the
    # start and high at the end. Same idiom as the reply sequence above
    # (rising pitch tracks progress), which is what makes it legible
    # without anyone being taught it.
    _CHUNK_TONE_LOW = 440
    _CHUNK_TONE_HIGH = 880

    def _chime_progress(self, fraction):
        """Sound one completed chunk of a redistill, pitched by how far
        through it is.

        The per-chunk report used to be spoken — a line naming the token
        counts and how many messages were left. For anyone typing while
        it runs, that line is never heard: a screen reader with character
        echo has no free moment, and this is a constant, not an
        occasional collision. So the redistill's only progress signal
        reached nobody, and a long one was indistinguishable from a
        stalled one by ear.

        A pitch carries the one number that matters. Rising = getting
        there; the same beep twice = stuck. It replaces the clock-driven
        "still working" cue for the length of a redistill rather than
        adding to it (see _tick_work_sounds) — same rough rate, but it
        means something.

        A user-supplied ~/.hearthkin/sounds/chunk.wav wins as usual and
        plays flat; a file can't be re-pitched, and mangling someone's
        chosen sound would be worse than losing the gradient.
        """
        on, vol = self._chime_setting("chunk")
        if not on or vol <= 0:
            return
        try:
            f = min(1.0, max(0.0, float(fraction)))
        except (TypeError, ValueError):
            f = 0.0
        freq = int(self._CHUNK_TONE_LOW
                   + (self._CHUNK_TONE_HIGH - self._CHUNK_TONE_LOW) * f)
        # Short: this fires once per chunk for the length of a long
        # redistill, and anything with presence becomes a nag.
        play_chime(freq, 55, volume=vol, name="chunk")

    def _play_problem_alert(self):
        """The audible half of "background work stopped".

        Same shape and same reasoning as `_play_approval_alert`, for the
        other thing that happens while nobody is watching: a distillation
        or consolidation that failed, or a redistill-from-start that
        stopped early. Gated on `problem_alert` (default on), loudness
        from `chime_volume`, independent of `reply_chime` — someone who
        keeps reply chimes off still needs to hear that the thing they
        left running isn't running any more. Replaceable by dropping
        ~/.hearthkin/sounds/problem.wav.

        Safe to call from any thread.
        """
        try:
            if not self.config.get("problem_alert", True):
                return
            try:
                vol = float(self.config.get("chime_volume", 0.8) or 0.0)
            except (TypeError, ValueError):
                vol = 0.8
            if vol > 0:
                play_alert(volume=vol, name="problem")
        except Exception:
            pass

    def _tick_work_sounds(self):
        """Sound the start, continuation and end of ANY model call.

        Called from the 5-second timer. Works by watching whether the machine
        is busy rather than by hooking each surface, and that is the whole
        point: before this, chimes were wired by hand into desktop chat and
        rooms only, so a Telegram reply, a cron wake-up and a heartbeat were
        all completely silent — exactly the cases where nobody is looking at
        the window and a sound is the only thing that could tell you. Wiring
        surfaces by hand is how that gap happened, and how the confirm-on-close
        dialog shipped missing two. Watching state covers surfaces that don't
        exist yet.

        Costs up to 5 seconds of latency against a direct hook, which is
        nothing next to a reply that takes minutes.
        """
        try:
            busy = bool(self._machine_busy())
        except Exception:
            return
        was = getattr(self, "_work_sound_busy", False)
        try:
            import time as _t
            now = _t.monotonic()
            if busy and not was:
                self._chime("send")
                self._work_sound_last_tick = now
            elif was and not busy:
                self._chime("done")
            elif busy and not getattr(self, "_walking_from_start", None):
                # A redistill reports its own progress per chunk
                # (_chime_progress), at a similar rate and carrying more
                # — how far through it is, not merely that it lives. Two
                # reassurance cues at once is just noise, so the clock
                # one stands down for the length of a redistill.
                #
                # This used to be an early `return`, which also skipped
                # the distilling cue at the bottom of this method — so a
                # walk was completely silent for the whole of each chunk,
                # which is the twenty-to-forty-minute part. Standing one
                # cue down must not silence the others.
                try:
                    every = float(self.config.get("chime_working_secs", 30) or 0)
                except (TypeError, ValueError):
                    every = 30.0
                last = getattr(self, "_work_sound_last_tick", 0.0)
                if every > 0 and (now - last) >= every:
                    self._work_sound_last_tick = now
                    self._chime("working")
        finally:
            self._work_sound_busy = busy
        try:
            self._tick_distilling_sound()
        except Exception:
            pass

    def _distilling_progress_chars(self):
        """The largest amount of summary any in-flight distillation has
        written so far, or None when none of them has written anything.

        Largest rather than a sum: two kins distilling at once are two
        separate stories and one tone can only tell one, so it tells the
        one that's furthest along. In practice the slot allows one at a
        time per kin and this is nearly always a single number.
        """
        prog = getattr(self, "_distill_progress", None)
        if not isinstance(prog, dict) or not prog:
            return None
        vals = [v for v in prog.values() if isinstance(v, int)]
        return max(vals) if vals else None

    def _tick_distilling_sound(self):
        """Periodic cue while any distillation or consolidation is
        running for any kin — and, once the summary starts arriving, a
        RISING one.

        Reported live: "Distill selected surface now" and "Distill all
        surfaces now" gave no way to tell by ear that a distillation
        specifically was still alive — they only ever got the same
        generic send/working/done ticks _tick_work_sounds already plays
        for a chat reply, a cron wake-up, or a heartbeat. All identical,
        so "is a distillation actually happening" had no answer for
        someone who can't see the window, on top of these very calls
        sometimes running 20-40 minutes.

        The flat version of this cue answered "alive?" and nothing else,
        which after the tenth identical beep is barely different from
        silence: it's a distillation, it's an LLM call like any other,
        and what anyone actually wants to know is whether it's getting
        anywhere. So the pitch now carries the one fact available from
        inside the call — how much of the summary has been written:

          * nothing written yet -> _DISTILLING_TONE, flat and low. This
            is the prefill, where the model is reading a bite that can
            run 10k tokens, and it is honestly the same beep each time
            because nothing has changed yet.
          * writing -> a rise from _DISTILLING_WRITE_LOW toward
            _DISTILLING_WRITE_HIGH as the text accumulates. The step up
            off the flat tone is itself the news that it's started
            writing.

        Same idiom as _chime_progress (rising = getting there, the same
        beep twice = stuck), and deliberately kept in the low register,
        below every reply cue, so a distillation is never mistaken for a
        kin answering.

        Runs during a WALK too. A walk's own cue (_chime_progress) fires
        once per CHUNK, in the bright 440-880 register; this one fires
        inside a chunk, underneath it. They're different facts, not two
        copies of the same reassurance — and standing this down for the
        length of a walk meant each chunk was 20-40 minutes of total
        silence, which is where a walk is easiest to lose.

        Rides the existing reply_chime + chime_volume settings (via
        _chime_setting, same as every other cue here) rather than adding
        a new toggle — someone who already has chimes on gets this
        immediately, which is the point; someone who wants it off already
        has the master switch. A user-supplied
        ~/.hearthkin/sounds/distilling.wav wins as usual and plays flat:
        a file can't be re-pitched, and mangling someone's chosen sound
        would be worse than losing the gradient (same rule as chunk.wav).
        """
        if not getattr(self, "_distilling", None):
            self._distilling_sound_last = 0.0
            return
        on, vol = self._chime_setting("distilling")
        if not on or vol <= 0:
            return
        now = time.monotonic()
        last = getattr(self, "_distilling_sound_last", 0.0)
        try:
            every = float(self.config.get("chime_distilling_secs", 20) or 0)
        except (TypeError, ValueError):
            every = 20.0
        if every <= 0 or (now - last) < every:
            return
        self._distilling_sound_last = now
        freq = self._distilling_freq(self._distilling_progress_chars())
        play_chime(freq, self._DISTILLING_TONE[1], volume=vol,
                   name="distilling")

    def _distilling_freq(self, written):
        """Which note this beep should be, for `written` characters of
        summary so far. None or 0 — nothing written, or no counter at all
        — is the flat reading tone: a missing count must never silence
        the cue or invent progress that hasn't happened."""
        try:
            written = int(written or 0)
        except (TypeError, ValueError):
            written = 0
        if written <= 0:
            return self._DISTILLING_TONE[0]
        steps = self._DISTILLING_WRITE_STEPS
        fraction = min(0.999, written / float(self._DISTILLING_FULL_CHARS))
        return steps[int(fraction * len(steps))]
