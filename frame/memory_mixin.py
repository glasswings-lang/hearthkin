"""MemoryMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    _DISTILL_CALLBACK_GRACE_SECS, _DISTILL_REREAD_OVERLAP, _DISTILL_RESERVE_TOKENS,
    _DISTILL_WATCHDOG_SECS, _WALK_RETRY_MAX, _WALK_RETRY_SECS,
    _model_context_length, _num_ctx_of,
    append_failure_log, append_staging, consolidate_memory_blocking, datetime,
    distill_memory_blocking, estimate_message_tokens, estimate_tokens, list_agents,
    list_rooms,
    live_distill_bookmark, llm_backend, load_agent_config, load_agent_conversation,
    load_distill_prompt, load_memory, load_room_config, load_room_conversation,
    load_soul, nvda_speak, save_agent_config,
    save_memory, strip_model_annotation, think_effort_of, threading, time, wx,
)


class MemoryMixin:

    # --- Memory --- #

    # --- Memory background tasks (UI lives in EditKinDialog) --- #

    def _dialog_for(self, agent_name):
        """Return the EditKinDialog instance if it's open for this kin, else None."""
        dlg = getattr(self, "_edit_kin_dialog", None)
        if dlg is not None and dlg.kin == agent_name:
            return dlg
        return None

    def _is_distill_in_flight(self, agent_name):
        """Return True if a distillation or consolidation worker is
        actively running for this agent.

        ASK THE THREAD, DON'T GUESS FROM A CLOCK. This used to declare
        any run older than five minutes wedged and clear the flag — on
        an operation that routinely takes longer than that. A local
        model reading a 10k-token distillation bite can spend well over
        five minutes in prefill alone, so the watchdog fired on perfectly
        healthy work: the Memory tab announced "previous distill cleared
        — was stuck" about a call that was still running, and worse, the
        cleared flag let a SECOND distillation start on the same kin
        while the first was still going. During a redistill that means
        two chunks running against the same un-advanced bookmark,
        digesting the same turns and staging two sets of notes for them.

        A worker thread that is alive is working, however long it takes.
        The watchdog now only fires when the thread is genuinely gone
        without having reported back — which is the actual failure it
        was written for, and it can say so definitively instead of
        guessing. (Same lesson as the cron marker sweep: liveness is a
        question you ask the thing, not the clock.)

        The grace window covers the one real race: a worker finishes,
        posts its wx.CallAfter, and ends — for the moment between the
        thread dying and the callback being processed, "not alive" is
        not "wedged". A queued CallAfter lands in milliseconds, so the
        window is enormously generous.

        The clock is kept only as a fallback for state with no thread
        recorded (a run that started before this frame, hand-poked
        state), and at a length that no longer libels a slow model.
        """
        started = self._distilling.get(agent_name)
        if started is None:
            return False
        threads = getattr(self, "_distill_threads", None) or {}
        th = threads.get(agent_name)
        if th is not None:
            try:
                alive = th.is_alive()
            except Exception:
                alive = True   # can't tell → assume working; never cut a run short
            dead_since = getattr(self, "_distill_dead_since", None)
            if dead_since is None:
                dead_since = self._distill_dead_since = {}
            if alive:
                dead_since.pop(agent_name, None)
                return True
            first_seen_dead = dead_since.setdefault(agent_name, time.time())
            if time.time() - first_seen_dead < _DISTILL_CALLBACK_GRACE_SECS:
                return True
            self._clear_wedged_distill(agent_name)
            return False
        try:
            elapsed = time.time() - float(started)
        except (TypeError, ValueError):
            self._clear_wedged_distill(agent_name)
            return False
        if elapsed > _DISTILL_WATCHDOG_SECS:
            self._clear_wedged_distill(agent_name)
            return False
        return True

    def _clear_wedged_distill(self, agent_name):
        """Release a distillation slot whose worker is definitively gone
        without having reported back, and say so on the Memory tab if
        it's open."""
        self._distilling.pop(agent_name, None)
        for attr in ("_distill_threads", "_distill_dead_since",
                     "_distill_progress"):
            d = getattr(self, attr, None)
            if isinstance(d, dict):
                d.pop(agent_name, None)
        dlg = self._dialog_for(agent_name)
        if dlg is not None:
            try:
                dlg.distill_btn.Enable()
                dlg.consolidate_btn.Enable()
                dlg.memory_status.SetLabel(
                    "(previous distill/consolidate cleared — its worker "
                    "stopped without reporting back)")
            except Exception:
                pass

    def _release_distill_slot(self, agent_name):
        """Normal end of a distillation/consolidation: drop the slot and
        the worker bookkeeping together. Kept as one call so a future
        completion path can't free the slot while leaving a dead thread
        registered against the kin."""
        self._distilling.pop(agent_name, None)
        for attr in ("_distill_threads", "_distill_dead_since",
                     "_distill_progress"):
            d = getattr(self, attr, None)
            if isinstance(d, dict):
                d.pop(agent_name, None)

    def _note_distill_progress(self, agent_name, chars):
        """Record how much of this kin's summary has been written so far.

        Called from the distillation worker thread on every streamed
        delta, so it does exactly one thing: store an int. The 5-second
        UI timer reads it (see StatusVoiceMixin._tick_distilling_sound)
        and turns it into a rising tone. No locking — a plain dict
        assignment of an int is atomic under the GIL, and a cue reading
        a value one delta stale is not a defect.
        """
        d = getattr(self, "_distill_progress", None)
        if d is None:
            d = self._distill_progress = {}
        try:
            d[agent_name] = int(chars)
        except (TypeError, ValueError):
            pass

    def _start_distill_progress(self, agent_name):
        """Zero the progress counter as a run begins.

        Zero and absent mean different things to the cue: absent is
        "nothing running", zero is "running, nothing written yet" —
        which is the long silent prefill, and the one stretch where
        someone most needs to hear that anything is happening at all.
        """
        d = getattr(self, "_distill_progress", None)
        if d is None:
            d = self._distill_progress = {}
        d[agent_name] = 0

    def _register_distill_thread(self, agent_name, thread):
        """Record the worker holding this kin's distillation slot, so
        _is_distill_in_flight can ask it whether it's still working."""
        threads = getattr(self, "_distill_threads", None)
        if threads is None:
            threads = self._distill_threads = {}
        threads[agent_name] = thread
        dead_since = getattr(self, "_distill_dead_since", None)
        if isinstance(dead_since, dict):
            dead_since.pop(agent_name, None)

    # --- Redistill-from-start walk state --- #
    #
    # A walk is "this kin is chewing through one surface's whole history
    # in chunks, firing the next one each time a chunk lands". It lives
    # in TWO places, deliberately:
    #
    #   self._walking_from_start[(kin, scope)] — in-memory, means "the
    #       chain is live right now in this process".
    #   cfg["distill_walk_scopes"] on disk — means "this walk was started
    #       and hasn't finished", and survives quitting.
    #
    # The pair is what makes a walk resumable. In-memory alone, quitting
    # ended it permanently and invisibly; on-disk alone, we couldn't tell
    # a running chain from a stalled one.

    def _walk_scopes_on_disk(self, agent_name):
        """Scopes with an unfinished walk recorded in the kin's config.
        Tolerant of junk: a hand-edited config holding a string or a
        dict here must not break the Memory tab."""
        try:
            raw = (load_agent_config(agent_name) or {}).get(
                "distill_walk_scopes")
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        return [s for s in raw if isinstance(s, str) and s]

    def _persist_walk(self, agent_name, scope_key, active):
        """Add or remove `scope_key` from the kin's on-disk walk list.
        Load-modify-save, like every other writer here, so a bookmark
        advanced by the chunk that just finished isn't clobbered."""
        try:
            cfg = load_agent_config(agent_name) or {}
            scopes = [s for s in (cfg.get("distill_walk_scopes") or [])
                      if isinstance(s, str) and s]
            if active and scope_key not in scopes:
                scopes.append(scope_key)
            elif not active and scope_key in scopes:
                scopes = [s for s in scopes if s != scope_key]
            else:
                return  # already in the state we want — don't rewrite
            cfg["distill_walk_scopes"] = scopes
            save_agent_config(agent_name, cfg)
        except Exception as e:
            # Losing this doesn't corrupt anything — the walk still runs
            # in memory, it just won't survive a quit, which is exactly
            # the old behaviour. Leave a trace rather than failing loud.
            try:
                append_failure_log(
                    "save_failures.log", agent_name,
                    f"distill walk state (scope={scope_key}, "
                    f"active={active})", e,
                )
            except Exception:
                pass

    # Where the bookmark stood BEFORE a redistill-from-start rewound it,
    # so Cancel can put it back. Without this, cancelling only stopped the
    # walk machinery and left the kin with a bookmark near zero and tens of
    # thousands of undistilled messages — which is over the ordinary
    # `memory_distill_at_pct` threshold by a mile, so the routine
    # auto-distill trigger immediately took the same work over, from the
    # same place, chunk after chunk. Indistinguishable from outside, and
    # nothing stops it: Cancel only knows about walks. Someone cancelled a
    # redistill, watched it carry on, quit the app, and it was still going.

    def _walk_prior_offset(self, agent_name, scope_key):
        """The pre-redistill bookmark for this scope, or None if none was
        recorded. Tolerant of a hand-edited config holding junk."""
        try:
            raw = (load_agent_config(agent_name) or {}).get(
                "distill_walk_prior_offsets")
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        val = raw.get(scope_key)
        return val if isinstance(val, int) and val >= 0 else None

    def _persist_walk_prior(self, agent_name, scope_key, value):
        """Record (or clear, with value=None) the pre-redistill bookmark.
        Load-modify-save like every other writer here, so a bookmark
        advanced by the chunk that just finished isn't clobbered."""
        try:
            cfg = load_agent_config(agent_name) or {}
            priors = dict(cfg.get("distill_walk_prior_offsets") or {})
            if value is None:
                if scope_key not in priors:
                    return
                priors.pop(scope_key, None)
            else:
                if priors.get(scope_key) == int(value):
                    return
                priors[scope_key] = int(value)
            cfg["distill_walk_prior_offsets"] = priors
            save_agent_config(agent_name, cfg)
        except Exception as e:
            try:
                append_failure_log(
                    "save_failures.log", agent_name,
                    f"distill walk prior bookmark (scope={scope_key}, "
                    f"value={value})", e,
                )
            except Exception:
                pass

    def _restore_walk_bookmark(self, agent_name, scope_key):
        """Put this scope's bookmark back to where it stood before the
        redistill, and forget the recorded value. Returns the restored
        position, or None when there was nothing recorded (a redistill
        started before this existed, or one that already finished).

        Only ever moves the bookmark BACKWARD-to-forward — i.e. forward,
        to where it was. A cancelled redistill has been chewing from zero,
        so the live bookmark is behind the recorded one; restoring it
        un-does the rewind rather than discarding real progress. If the
        live bookmark is somehow already further along, leave it be: work
        that was genuinely distilled must not be re-billed."""
        prior = self._walk_prior_offset(agent_name, scope_key)
        if prior is None:
            return None
        restored = None
        try:
            cfg = load_agent_config(agent_name) or {}
            offsets = dict(cfg.get("distill_offsets") or {})
            current = offsets.get(scope_key)
            current = current if isinstance(current, int) else 0
            if prior > current:
                offsets[scope_key] = prior
                cfg["distill_offsets"] = offsets
                save_agent_config(agent_name, cfg)
                restored = prior
        except Exception as e:
            try:
                append_failure_log(
                    "save_failures.log", agent_name,
                    f"restore distill bookmark on cancel "
                    f"(scope={scope_key}, prior={prior})", e,
                )
            except Exception:
                pass
        self._persist_walk_prior(agent_name, scope_key, None)
        return restored

    # --- Redistill pacing --- #
    #
    # A walk's default shape is unattended: rewind to 0, then chunk
    # through the whole conversation on its own, chunk after chunk,
    # stopping only on error, on quitting, or on Cancel. That is the
    # wrong shape for someone who wants to LISTEN to what a redistill
    # produces as it goes rather than let it run unattended overnight —
    # and Cancel's only lever for that was rewind-vs-restore, an
    # all-or-nothing choice with no stop in between.
    #
    # Pacing gives the walk a unit smaller than "the whole thing":
    #   "unattended" — today's behavior, chunk after chunk to the end.
    #   "day"        — auto-chains chunks (a big day can still take
    #                   several) but STOPS before crossing into the
    #                   next calendar day, and stops there even if a
    #                   day's content is smaller than one chunk. The
    #                   existing Continue-redistilling button becomes
    #                   "give me the next day" — no new button needed.
    #   "hour"       — identical, bucketed by hour instead of day.
    #   "chunk"      — pause after every single bite, always.
    #
    # Stored per (agent, scope_key), same shape as distill_walk_prior_offsets,
    # so a paused walk remembers which pacing it's using across a quit
    # and a relaunch.

    _WALK_PACING_UNATTENDED = "unattended"
    _WALK_PACING_DAY = "day"
    _WALK_PACING_HOUR = "hour"
    _WALK_PACING_CHUNK = "chunk"
    _WALK_PACINGS = (_WALK_PACING_UNATTENDED, _WALK_PACING_DAY,
                     _WALK_PACING_HOUR, _WALK_PACING_CHUNK)

    def _walk_pacing_on_disk(self, agent_name, scope_key):
        """This scope's recorded pacing, or 'unattended' if none is
        recorded (a walk started before this existed, or one whose
        pacing was never set) or the config holds junk."""
        try:
            raw = (load_agent_config(agent_name) or {}).get(
                "distill_walk_pacing")
        except Exception:
            return self._WALK_PACING_UNATTENDED
        if not isinstance(raw, dict):
            return self._WALK_PACING_UNATTENDED
        val = raw.get(scope_key)
        return val if val in self._WALK_PACINGS else self._WALK_PACING_UNATTENDED

    def _persist_walk_pacing(self, agent_name, scope_key, pacing):
        """Record this scope's chosen pacing. Load-modify-save like
        every other writer here. A failed write falls back to
        'unattended' behavior on the next read rather than raising —
        losing the PACING choice must never stop the walk itself from
        running."""
        if pacing not in self._WALK_PACINGS:
            pacing = self._WALK_PACING_UNATTENDED
        try:
            cfg = load_agent_config(agent_name) or {}
            pacings = dict(cfg.get("distill_walk_pacing") or {})
            if pacings.get(scope_key) == pacing:
                return
            pacings[scope_key] = pacing
            cfg["distill_walk_pacing"] = pacings
            save_agent_config(agent_name, cfg)
        except Exception as e:
            try:
                append_failure_log(
                    "save_failures.log", agent_name,
                    f"distill walk pacing (scope={scope_key}, "
                    f"pacing={pacing})", e,
                )
            except Exception:
                pass

    def _walk_boundary_ts(self, pacing, ts_iso):
        """The ISO timestamp marking the first moment that belongs to
        the NEXT calendar day/hour after `ts_iso` — the cap a bite must
        stop before, for 'day'/'hour' pacing. None for 'unattended' /
        'chunk' (no calendar boundary applies) and None if `ts_iso`
        can't be parsed — fail OPEN, since an unparseable timestamp
        must never silently block a bite from advancing at all."""
        if pacing not in (self._WALK_PACING_DAY, self._WALK_PACING_HOUR):
            return None
        if not ts_iso:
            return None
        try:
            dt = datetime.datetime.fromisoformat(ts_iso)
        except (ValueError, TypeError):
            return None
        if pacing == self._WALK_PACING_DAY:
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            boundary = start + datetime.timedelta(days=1)
        else:
            start = dt.replace(minute=0, second=0, microsecond=0)
            boundary = start + datetime.timedelta(hours=1)
        return boundary.isoformat()

    def _format_walk_pause_when(self, pacing, ts_iso):
        """Human-readable point-in-time for a pacing-pause announcement
        ('2026-01-02' for day pacing, '2026-01-02 09:00' for hour
        pacing), or '' if ts_iso is missing/unparseable — the
        announcement falls back to a plain count of messages left in
        that case rather than failing."""
        if not ts_iso:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(ts_iso)
        except (ValueError, TypeError):
            return ""
        if pacing == self._WALK_PACING_HOUR:
            return dt.strftime("%Y-%m-%d %H:00")
        return dt.strftime("%Y-%m-%d")

    def _walk_should_pause_after_bite(self, pacing, hit_boundary):
        """Should a walk pause here instead of auto-chaining into the
        next bite, given this scope's pacing and whether the bite that
        just finished is what hit a calendar boundary?

        Pure decision, no side effects, kept separate from
        _on_distill_done's much larger body so it's directly
        testable. 'chunk' pacing always pauses. 'day'/'hour' pacing
        pauses only when hit_boundary is True — a big day/hour
        spanning several bites keeps auto-chaining through the rest
        of it exactly like unattended pacing does; only the bite that
        actually reaches the boundary pauses. Anything else
        (unattended, or an unrecognized pacing) never pauses here."""
        if pacing == self._WALK_PACING_CHUNK:
            return True
        if pacing in (self._WALK_PACING_DAY, self._WALK_PACING_HOUR):
            return bool(hit_boundary)
        return False

    def _start_walk(self, agent_name, scope_key, pacing=None):
        """Mark a walk live, in memory and on disk.

        `pacing`, when given, is the operator's FRESH choice from
        "Redistill from start" and overwrites whatever was recorded
        before. Omitted on every RESUME path — "Continue redistilling"
        and _resume_pending_distill_walks on launch both call this with
        no pacing — so a walk's already-persisted pacing survives being
        resumed instead of reverting to unattended.

        Getting this backwards was a real, confirmed bug: a walk
        explicitly started as 'day' pacing paused correctly after day
        one, and "Continue redistilling" silently turned it back into
        an unattended walk with no further pauses at all — the previous
        code treated a merely-omitted pacing the same as an explicit
        request for 'unattended', which is wrong for every caller
        except the fresh-start one. _walk_pacing_on_disk already
        defaults to 'unattended' for a scope that never had a pacing
        recorded, so there is nothing to default here — leaving the
        on-disk value untouched is correct in every omitted-pacing
        case."""
        walking = getattr(self, "_walking_from_start", None)
        if walking is None:
            self._walking_from_start = walking = {}
        walking[(agent_name, scope_key)] = True
        self._persist_walk(agent_name, scope_key, True)
        if pacing is not None:
            self._persist_walk_pacing(agent_name, scope_key, pacing)

    def _start_catchup(self, agent_name, scope_key, conversation):
        """Chain distillation FORWARD from where the bookmark already
        stands, unattended, until the surface has caught up.

        This is "Redistill from start" minus its first step. The chaining,
        the resume-after-quit, the Cancel button and the sounds are all the
        walk's; the only difference is that the bookmark is left exactly
        where it is instead of being reset to zero.

        That difference is the whole point. A kin can fall a long way
        behind — one real surface sat 87,134 messages back — and the three
        existing ways to catch it up all failed for the same reason in
        different clothes: the automatic trigger only fires after a reply,
        so nothing happens overnight; "Distill now" does one bite per
        press, which is several hundred presses; and the walk chains by
        itself but starts over, re-billing every message already done. The
        one mode that runs unattended was bolted to the one that throws
        away the work.

        Deliberately unattended pacing, whatever the scope's recorded walk
        pacing happens to be: this exists to be started and walked away
        from. Someone who wants to step through a bite at a time already
        has the button that does that.

        NOTHING is rewound here, so nothing is recorded to rewind BACK to.
        Any stale prior from an older walk is cleared for that reason --
        without it, cancelling a catch-up could hand the bookmark to a
        value recorded for a different run, and _restore_walk_bookmark only
        ever moves the bookmark FORWARD, which for a catch-up would mean
        silently skipping undistilled messages rather than re-reading them.
        Skipping is the one failure this must not have: re-reading costs a
        little money, skipping costs a kin its memory of that stretch and
        says nothing.
        """
        self._persist_walk_prior(agent_name, scope_key, None)
        self._start_walk(agent_name, scope_key, pacing="unattended")
        # 'catchup-' is deliberately not an 'every-'/'ctx-' source, so the
        # backlog brake never holds it back -- this is something the person
        # pressed, and those are never paced.
        self._kick_off_distillation(
            agent_name, conversation,
            source_label=f"catchup-{scope_key}",
            scope_key=scope_key)

    def _end_walk(self, agent_name, scope_key, keep_on_disk=False):
        """Stop a walk. `keep_on_disk=True` means "paused, not finished"
        — the in-memory chain stops but the on-disk record stays, so
        Resume (and the next launch) can pick it up. That's what a
        failed chunk and a slot that never came free both get: the
        difference between finished and interrupted is exactly the
        thing the old code threw away."""
        walking = getattr(self, "_walking_from_start", None) or {}
        walking.pop((agent_name, scope_key), None)
        if not keep_on_disk:
            self._persist_walk(agent_name, scope_key, False)
        self._refresh_walk_ui(agent_name)

    def _refresh_walk_ui(self, agent_name):
        """Repaint the Memory tab's walk buttons if it happens to be
        open on this kin. No-op otherwise — the walk does not need the
        dialog to exist, and must never depend on it."""
        dlg = self._dialog_for(agent_name)
        if dlg is None:
            return
        try:
            dlg._refresh_walk_controls()
        except Exception:
            pass

    def _announce_problem(self, message):
        """Say a background-work failure out loud, and sound it.

        Distillation runs unattended by design, so a failure that only
        writes to the Activity field is a failure nobody learns about:
        that field isn't spoken, and it reverts to the idle context line
        after four seconds. Anything that stops background work reaches
        the user through this."""
        try:
            self._set_status(message, speak=True)
        except Exception:
            pass
        try:
            self._play_problem_alert()
        except Exception:
            pass

    def _kick_off_consolidation(self, agent_name, source_label="manual"):
        if self._is_distill_in_flight(agent_name):
            return
        existing_memory = load_memory(agent_name)
        dlg = self._dialog_for(agent_name)
        if not (existing_memory or "").strip():
            if dlg is not None:
                dlg.memory_status.SetLabel("(memory is empty — nothing to consolidate)")
            return
        self._distilling[agent_name] = time.time()
        self._start_distill_progress(agent_name)
        cfg = load_agent_config(agent_name)
        chat_model = strip_model_annotation(cfg.get("model", ""))
        memory_model = (cfg.get("memory_model") or "").strip() or chat_model

        if dlg is not None:
            dlg.consolidate_btn.Disable()
            dlg.distill_btn.Disable()
            dlg.memory_status.SetLabel(f"Consolidating memory ({source_label})...")
        self._set_status(f"Consolidating memory for {agent_name}...")

        options = {
            "temperature": 0.2,
            "num_predict": 4000,
            "num_ctx": max(int(cfg.get("num_ctx", 2048)), 4096),
        }

        def worker():
            try:
                new_memory = consolidate_memory_blocking(
                    existing_memory, memory_model, options,
                    kin_name=agent_name,
                    on_progress=lambda n: self._note_distill_progress(
                        agent_name, n))
                wx.CallAfter(self._on_consolidate_done, agent_name, new_memory, None)
            except Exception as e:
                wx.CallAfter(self._on_consolidate_done, agent_name, None, str(e))

        th = threading.Thread(target=worker, daemon=True)
        self._register_distill_thread(agent_name, th)
        th.start()

    def _on_consolidate_done(self, agent_name, new_memory, error):
        self._release_distill_slot(agent_name)
        dlg = self._dialog_for(agent_name)
        if error:
            if dlg is not None:
                dlg.memory_status.SetLabel(f"Consolidate error: {error[:80]}")
                dlg.consolidate_btn.Enable()
                dlg.distill_btn.Enable()
            try:
                append_failure_log(
                    "distill_errors.log", agent_name, "consolidate", error)
            except Exception:
                pass
            self._announce_problem(
                f"Tidying memory failed for {agent_name}: {error[:120]}")
            return
        try:
            save_memory(agent_name, new_memory)
        except Exception as e:
            self._announce_problem(
                f"Tidied {agent_name}'s memory but couldn't save it: {e}")
            # Re-enable both buttons so the user can retry rather than
            # being stuck with two greyed-out buttons (audit H10).
            if dlg is not None:
                dlg.consolidate_btn.Enable()
                dlg.distill_btn.Enable()
            return
        # Stamp the cooldown anchor on every successful consolidation
        # (auto or manual). The auto-after-distill trigger reads this
        # to skip re-firing within the cooldown window — see
        # _on_distill_done. Manual consolidations also count: pressing
        # Consolidate followed by busy Telegram traffic shouldn't
        # then auto-fire another consolidation 90 seconds later.
        self._last_consolidation_at[agent_name] = time.time()
        self._invalidate_kin_text_cache(agent_name)
        if dlg is not None:
            if not dlg._memory_dirty:
                dlg._suppress_memory_dirty = True
                dlg.memory_editor.SetValue(new_memory)
                dlg._suppress_memory_dirty = False
                dlg._mark_memory_clean()
                dlg.memory_status.SetLabel(f"Memory consolidated {datetime.datetime.now().strftime('%H:%M')}")
            else:
                dlg.memory_status.SetLabel("Memory consolidated on disk (your edits unsaved)")
            dlg.consolidate_btn.Enable()
            dlg.distill_btn.Enable()
        self._set_status(f"Memory consolidated for {agent_name}.")

    def _distill_scope_for_telegram_user(self, agent_name, user_id):
        """Compute the distillation scope for a Telegram DM with
        `user_id`. If that user has share-with-desktop on, returns
        "desktop" — their activity counts toward the shared scope.
        Otherwise, returns "tg:user:<id>" — its own independent scope
        that distills into memory.md at its own cadence without
        affecting the desktop counter."""
        cfg = load_agent_config(agent_name) or {}
        tg = (cfg.get("telegram") or {})
        share = tg.get("user_share_desktop") or {}
        uid_str = str(user_id)
        if (isinstance(share, dict)
                and (share.get(uid_str) or share.get(user_id))):
            return "desktop"
        return f"tg:user:{uid_str}"

    def _distill_scope_for_telegram_group(self, agent_name, chat_id):
        """Same as the user variant but for groups. Shared groups go
        to the desktop scope; non-shared groups have their own."""
        cfg = load_agent_config(agent_name) or {}
        tg = (cfg.get("telegram") or {})
        share = tg.get("group_share_desktop") or {}
        cid_str = str(chat_id)
        if (isinstance(share, dict)
                and (share.get(cid_str) or share.get(chat_id))):
            return "desktop"
        return f"tg:group:{cid_str}"

    def _distill_scope_for_room(self, room_name):
        """Compute the distillation scope for a room. Unlike the two
        Telegram resolvers there's no share-with-desktop variant: a
        room always gets its own scope, never "desktop".

        Rooms are multi-speaker, and folding them into the desktop
        scope would splice a three-way conversation into the middle of
        the kin's 1-on-1 timeline with the operator — the same mistake
        the v0.2.33 per-surface read-time filtering exists to prevent.
        Whether the room reaches memory AT ALL is the room's
        distill_to_memory flag (default off); this only names the
        scope once that's on."""
        return f"room:{room_name}"

    def _room_scopes_for_kin(self, agent_name):
        """Every "room:<name>" scope this kin distills into: the rooms
        it's a member of that have distill_to_memory on. Rooms without
        the flag are omitted entirely — no scope, no counter, no
        staging file, exactly as before the flag existed."""
        scopes = []
        try:
            rooms = list_rooms() or []
        except Exception:
            return scopes
        for room_name in rooms:
            try:
                rcfg = load_room_config(room_name) or {}
            except Exception:
                continue
            if not rcfg.get("distill_to_memory", False):
                continue
            if agent_name in (rcfg.get("members") or []):
                scopes.append(self._distill_scope_for_room(room_name))
        return scopes

    def _room_convo_slice_for_kin(self, agent_name, room_name):
        """Build `agent_name`'s own slice of a room transcript, shaped
        for the summarizer.

        Mirrors the per-kin view the room turn-builder hands the model:
        this kin's own turns sit in the assistant slot bare, and every
        other voice — the human and the other kin alike — sits in the
        user slot tagged "[Name] ". So the summarizer reads the room
        the way this kin lived it, and the attribution the distill
        prompt already knows how to preserve (same "[Name] " shape as
        an attributed Telegram group turn) rides in the content.

        Read from disk, not self.room_conversation — same reasoning as
        the desktop scope above. This runs on the auto-distill worker
        thread, and room_conversation is UI-thread state; the room save
        in _on_room_kin_done lands before the counter bump that gets us
        here, so disk is current.

        Per-kin rather than one shared summary: what the room meant to
        one member is not what it meant to another, and merging them
        into a single blob re-introduces exactly the identity-
        convergence risk multi-kin-rooms-shared-history.md warns about.
        """
        try:
            room_convo = list(load_room_conversation(room_name) or [])
        except Exception:
            return []
        user_name = (self.config.get("user_name", "") or "").strip()
        user_prefix = f"[{user_name}] " if user_name else ""
        out = []
        for m in room_convo:
            role = m.get("role")
            content = m.get("content", "")
            speaker = m.get("speaker", "")
            if role == "user":
                out.append({"role": "user", "content": user_prefix + content})
            elif role != "assistant":
                # Harness bookkeeping — today the salvage system notes.
                # These carry a `speaker` like a real turn does, so they
                # must be filtered on ROLE, not on speaker: keyed off
                # speaker alone, the salvage note lands in every other
                # member's slice as words that kin never said, and in
                # its own slice as its own voice saying "[hearthkin:
                # your post-tool reply was empty]". It's a note about a
                # turn, not part of what was said, and has no place in
                # what anyone remembers of the room.
                continue
            elif speaker and speaker != agent_name:
                out.append({
                    "role": "user",
                    "content": f"[{speaker}] {content}",
                })
            elif speaker == agent_name:
                out.append({"role": "assistant", "content": content})
        return out

    def _convo_for_distill_scope(self, agent_name, scope_key):
        """Return the conversation history that should be distilled
        for the given (agent, scope) pair. For the unified "desktop"
        scope, that's the kin's main conversation.jsonl (which holds
        desktop turns + any shared Telegram surface's turns). For a
        non-shared Telegram user/group scope, that's the bot's
        segregated in-memory history for that surface (or the file
        if the bot isn't running).

        Desktop scope always reads from disk, even for the current
        kin. self.conversation is the desktop UI's view and is kept
        in sync with conversation.jsonl by the 5-second mtime poll —
        but the Telegram bot writes appends to disk directly, so
        there's a race window where a just-arrived Telegram message
        is on disk but not yet in self.conversation. _on_telegram_activity
        fires distillation triggers immediately on append, well inside
        that race window, so distillation reading self.conversation
        would consistently see a stale view and advance its bookmark
        over content it never actually digested. Reading from disk
        eliminates the race; conversation.jsonl is the source of
        truth for every surface that writes to it."""
        if scope_key == "desktop":
            try:
                return list(load_agent_conversation(agent_name) or [])
            except Exception:
                return []
        # Telegram-side non-shared scopes
        bot = (getattr(self, "bots", None) or {}).get(agent_name)
        if scope_key.startswith("tg:user:"):
            uid_str = scope_key[len("tg:user:"):]
            if bot is not None:
                try:
                    with bot._histories_lock:
                        msgs = []
                        try:
                            int_key = int(uid_str)
                            msgs.extend(bot._histories.get(int_key, []) or [])
                        except (ValueError, TypeError):
                            pass
                        msgs.extend(bot._histories.get(uid_str, []) or [])
                        return msgs
                except Exception:
                    pass
            # Fallback: read the file
            try:
                from telegram_bot import load_telegram_history
                h = load_telegram_history(agent_name) or {}
                return list(h.get(uid_str) or [])
            except Exception:
                return []
        if scope_key.startswith("tg:group:"):
            cid_str = scope_key[len("tg:group:"):]
            history_key = f"group:{cid_str}"
            if bot is not None:
                try:
                    with bot._histories_lock:
                        return list(bot._histories.get(history_key, []) or [])
                except Exception:
                    pass
            try:
                from telegram_bot import load_telegram_history
                h = load_telegram_history(agent_name) or {}
                return list(h.get(history_key) or [])
            except Exception:
                return []
        if scope_key.startswith("room:"):
            # This kin's own slice of the room transcript. Gated by the
            # room's distill_to_memory flag at the two points that
            # create work (the counter bump in _on_room_kin_done and
            # _room_scopes_for_kin), not here — a scope that already
            # has a bookmark and staged notes should still resolve if
            # the operator later turns the flag back off, so "Distill
            # selected surface" on an existing room scope keeps working.
            return self._room_convo_slice_for_kin(
                agent_name, scope_key[len("room:"):])
        if scope_key.startswith("discord:"):
            # Non-shared Discord channel: its slice of discord_history.json.
            # (Shared channels distill under the "desktop" scope above,
            # since their turns live in conversation.jsonl.)
            cid = scope_key[len("discord:"):]
            try:
                from kin_persistence import load_discord_history
                return list(
                    (load_discord_history(agent_name) or {}).get(str(cid))
                    or [])
            except Exception:
                return []
        return []

    def _all_scopes_for_kin(self, agent_name, cfg=None):
        """Return the full list of distillation scope_keys configured
        for this kin: always "desktop", plus one per non-shared
        Telegram DM, plus one per non-shared group. Shared surfaces
        roll into the "desktop" scope and aren't listed separately."""
        if cfg is None:
            cfg = load_agent_config(agent_name) or {}
        keys = ["desktop"]
        tg = cfg.get("telegram") or {}
        user_share = tg.get("user_share_desktop") or {}
        group_share = tg.get("group_share_desktop") or {}
        for uid in (tg.get("allow_from") or []):
            sid = str(uid)
            if bool(user_share.get(sid) or user_share.get(uid)):
                continue
            keys.append(f"tg:user:{sid}")
        for chat_id in (tg.get("groups") or {}).keys():
            sid = str(chat_id)
            if bool(group_share.get(sid) or group_share.get(chat_id)):
                continue
            keys.append(f"tg:group:{sid}")
        # Discord channels aren't pre-configured (the bot learns them at
        # runtime), so enumerate the ones with segregated history on disk.
        # Shared Discord history rolls into the "desktop" scope instead.
        if not bool((cfg.get("discord") or {}).get("share_desktop", False)):
            try:
                from kin_persistence import load_discord_history
                for cid in (load_discord_history(agent_name) or {}).keys():
                    keys.append(f"discord:{cid}")
            except Exception:
                pass
        # Rooms the kin is in that have distill_to_memory on. Always
        # their own scope — a room never rolls into "desktop".
        keys.extend(self._room_scopes_for_kin(agent_name))
        return keys

    def _distill_all_scopes(self, agent_name):
        """Manually fire distillation across every configured scope
        for this kin that has pending content (turns past its
        distill bookmark). Builds a queue; the first scope fires
        now, the rest are drained by _on_distill_done as each one
        finishes. Each scope still goes through _distill_bite, so a
        huge surface only gets one bite per "Distill all surfaces"
        press — re-press to take the next round."""
        if not agent_name:
            return
        if self._is_distill_in_flight(agent_name):
            self._set_status(
                f"{agent_name} is already distilling — wait for the "
                "current run to finish, then try again.")
            return
        cfg = load_agent_config(agent_name) or {}
        offsets = cfg.get("distill_offsets") or {}
        queue = []
        for sk in self._all_scopes_for_kin(agent_name, cfg):
            convo = self._convo_for_distill_scope(agent_name, sk)
            if not convo:
                continue
            bm = live_distill_bookmark(offsets.get(sk, 0), len(convo))
            if bm >= len(convo):
                continue   # nothing new past the bookmark on this scope
            queue.append(sk)
        if not queue:
            msg = (f"Nothing to distill for {agent_name} — all "
                   "scopes caught up.")
            self._set_status(msg)
            try:
                nvda_speak(msg)
            except Exception:
                pass
            return
        first = queue.pop(0)
        self._distill_queue[agent_name] = queue
        convo = self._convo_for_distill_scope(agent_name, first)
        self._kick_off_distillation(
            agent_name, convo,
            source_label=f"all-{first}", scope_key=first)

    def _maybe_distill_on_close(self, agent_name):
        """If the agent has unsaved messages + on-close is enabled, distill now.
        Walks every scope that has unsaved messages for this kin and
        fires distillation for the one with the most pending turns
        (since we serialize distillations via the in-flight gate, we
        pick the busiest scope first; the others will catch up on
        their own next-tick after restart)."""
        cfg = load_agent_config(agent_name)
        if not cfg.get("memory_distill_on_close", True):
            return
        # Pick the scope for this kin with the most pending turns.
        candidates = [
            (sk, n) for (kn, sk), n in self._messages_since_distill.items()
            if kn == agent_name and n > 0
        ]
        if not candidates:
            return
        if self._is_distill_in_flight(agent_name):
            return
        scope_key, _n = max(candidates, key=lambda x: x[1])
        convo = self._convo_for_distill_scope(agent_name, scope_key)
        if not convo:
            return
        self._kick_off_distillation(agent_name, convo, source_label=f"on-close-{scope_key}", scope_key=scope_key)

    def _backlog_pace_holds(self, agent_name, scope_key):
        """True if this scope is mid-BACKLOG and the automatic triggers are
        being made to wait before firing again.

        The percent trigger asks "is the undistilled tail a big share of the
        context window?" That is the right question for a conversation that
        has outgrown its notes, and the wrong one for a bulk history import,
        which buries the bookmark under thousands of messages at once. In the
        second case one run cannot possibly clear the tail, so the trigger is
        still tripped when the next reply finishes — and it fires again, and
        again, after almost every reply, for as long as the backlog lasts.

        Measured on a real kin: a 5,872-message tail, 738,000 tokens, against
        a threshold it exceeded by twenty-two times. In one day that scope
        spent 66 minutes distilling and the person got 24 minutes of
        conversation — on the same local model, so the two were taking the
        model from each other. It also cost the prompt cache every time, which
        is the thing that had just been fixed one layer down.

        So a run that ends still behind starts a wait (`distill_backlog_pace_
        mins`). The backlog still gets done, in the background, at a pace that
        leaves the model free in between. A normal catch-up — one where the
        run actually finished the job — sets no wait at all and behaves
        exactly as it always did.

        Deliberately in memory only, not persisted: a restart clears it, so
        the worst a stale wait can cost is one extra run. Persisting it would
        mean a crash mid-backlog could silence a kin's memory for an hour with
        nothing on screen to say why."""
        until = (getattr(self, "_backlog_distill_pause_until", None) or {}).get(
            (agent_name, scope_key))
        if not until:
            return False
        if time.monotonic() >= until:
            self._backlog_distill_pause_until.pop((agent_name, scope_key), None)
            return False
        return True

    def _note_backlog_pace(self, agent_name, scope_key, digested, remaining):
        """Called when an automatic run finishes. Start a wait if the run left
        more behind than it took — i.e. one more run won't finish this either.

        Comparing what was digested against what is left is deliberate, and
        cheaper to trust than a threshold: it needs no guess about bite sizes
        or token ratios, and it answers the only question that matters, which
        is whether chasing this after the next reply would accomplish anything.
        A scope within one run of being caught up is not a backlog however
        many messages it holds."""
        try:
            if digested is None or remaining is None:
                return
            if remaining <= max(0, int(digested)):
                # Caught up, or one more run will finish it. No wait — and
                # clear any wait left from when it WAS behind, so the tail
                # end of a backlog isn't paced for no reason.
                pending = getattr(self, "_backlog_distill_pause_until", None)
                if pending:
                    pending.pop((agent_name, scope_key), None)
                return
            cfg = load_agent_config(agent_name) or {}
            mins = int(cfg.get("distill_backlog_pace_mins", 30) or 0)
            if mins <= 0:
                return
            pending = getattr(self, "_backlog_distill_pause_until", None)
            if pending is None:
                pending = self._backlog_distill_pause_until = {}
            pending[(agent_name, scope_key)] = time.monotonic() + mins * 60
            self._log(
                f"{agent_name}/{scope_key}: {remaining:,} messages still to "
                f"distill after digesting {digested:,} — pacing the next "
                f"automatic run by {mins} min so it doesn't run every turn")
        except Exception:
            pass

    def _maybe_auto_distill(self, agent_name, scope_key="desktop"):
        """Called after each completed reply on `scope_key`. Fires
        distillation if either trigger trips for THIS scope:

          - count: this scope's message counter has reached the
            per-kin `memory_distill_every_n` threshold;
          - context %: the undistilled tail of this scope's
            conversation (turns past its distill bookmark) has reached
            `memory_distill_at_pct` percent of num_ctx.

        Whichever trips first fires; the count check is cheap so it's
        tried first. Other scopes' counters and tails are untouched —
        independent cadences."""
        if not agent_name:
            return
        cfg = load_agent_config(agent_name)
        every_n = int(cfg.get("memory_distill_every_n", 0) or 0)
        at_pct = int(cfg.get("memory_distill_at_pct", 0) or 0)
        if every_n <= 0 and at_pct <= 0:
            return
        if self._is_distill_in_flight(agent_name):
            return
        if self._backlog_pace_holds(agent_name, scope_key):
            return
        trigger = None
        if every_n > 0:
            count = self._messages_since_distill.get((agent_name, scope_key), 0)
            if count >= every_n:
                trigger = f"every-{every_n}"
        # The conversation is needed for the pct estimate AND the
        # kickoff — only load it once a trigger is actually in play.
        if trigger is None and at_pct <= 0:
            return
        # The conversation load (load_agent_conversation — a multi-MB
        # parse for a long-lived kin's archive) + the pct estimate used to
        # run right here on the UI thread, per desktop reply AND per
        # Telegram activity tick (audit M-F12). Both now run on a
        # daemon worker; the trigger decision marshals back via
        # wx.CallAfter. The per-(kin, scope) guard keeps overlapping
        # ticks from stacking workers.
        guard_key = (agent_name, scope_key)
        pending = getattr(self, "_auto_distill_checks", None)
        if pending is None:
            pending = self._auto_distill_checks = set()
        if guard_key in pending:
            return
        pending.add(guard_key)

        def worker():
            convo = None
            fired_trigger = trigger
            try:
                convo = self._convo_for_distill_scope(agent_name, scope_key)
                if convo and fired_trigger is None:
                    pct = self._undistilled_context_pct(
                        agent_name, scope_key, convo, cfg)
                    if pct < at_pct:
                        convo = None
                    else:
                        fired_trigger = f"ctx-{at_pct}pct"
            except Exception:
                convo = None

            def decide():
                pending.discard(guard_key)
                if not convo or self._closing:
                    return
                self._kick_off_distillation(
                    agent_name, convo,
                    source_label=f"{fired_trigger}-{scope_key}",
                    scope_key=scope_key)

            wx.CallAfter(decide)

        threading.Thread(target=worker, daemon=True).start()

    def _undistilled_context_pct(self, agent_name, scope_key, convo, cfg):
        """Estimate the undistilled tail of `convo` — turns after this
        scope's distill bookmark (distill_offsets[scope_key]) — as a
        percentage of the kin's num_ctx.

        This is what the %-of-context distillation trigger measures.
        Measuring the *tail* rather than the whole conversation is
        what makes the figure self-reset each distillation:
        _on_distill_done advances the bookmark to the conversation's
        end, so the tail drops back to ~empty and the trigger won't
        immediately re-fire. The raw character estimate is scaled by
        the kin's learned real/estimate token ratio so the percentage
        tracks the real billed prompt, not the optimistic estimate."""
        try:
            num_ctx = int(cfg.get("num_ctx", 8192) or 8192)
        except (TypeError, ValueError):
            num_ctx = 8192
        if num_ctx <= 0:  # corrupt config — fall back, don't divide by zero
            num_ctx = 8192
        offsets = cfg.get("distill_offsets") or {}
        bookmark = live_distill_bookmark(offsets.get(scope_key, 0), len(convo))
        tail = convo[bookmark:]
        if not tail:
            return 0.0
        model = strip_model_annotation(cfg.get("model", "") or "")
        est = sum(estimate_message_tokens(m, model) for m in tail)
        ratio = llm_backend.token_calibration_ratio(agent_name)
        return 100.0 * est * ratio / num_ctx

    def _distill_bite(self, conversation, cfg, scope_key, agent_name, pacing=None):
        """Compute the slice of `conversation` to hand to the
        summarizer for this scope, the new bookmark, and the num_ctx
        to pass to the summarizer.

        Returns (conversation_slice, distilled_through, budget_ctx,
        hit_boundary).

        The slice starts _DISTILL_REREAD_OVERLAP turns before this
        scope's bookmark (a stale or slightly-off bookmark just
        re-reads a few already-seen turns — never skips one), and
        is capped at the summarizer's actual context window minus
        _DISTILL_RESERVE_TOKENS. The new bookmark advances only to
        where the slice ended — on a legacy huge undistilled tail,
        multiple trigger fires absorb it across multiple bounded
        runs instead of one impossible swallow that would either
        overrun the summarizer's window or burn a chunk of paid
        budget in one shot.

        `pacing` ('day' / 'hour' / None) ALSO caps the slice so it
        never crosses a calendar boundary — a bite stops before the
        first message that belongs to the next day/hour, even if the
        token budget had room left. `hit_boundary` reports whether
        THAT was what actually capped this bite (as opposed to the
        token budget, or simply reaching the end of the conversation)
        — the caller's signal that this unit is genuinely finished and
        the walk should pause rather than auto-chain into the next
        one."""
        full_len = len(conversation)
        _offsets = cfg.get("distill_offsets") or {}
        # Past-the-end bookmark (restarted conversation) re-reads from 0
        # rather than clamping to the end and reporting "caught up".
        _prev = live_distill_bookmark(_offsets.get(scope_key, 0), full_len)
        # Bookmark already at the conversation's end — nothing new to
        # distill. Return an empty slice so the caller's empty-bite
        # guard skips the run entirely. Without this, the re-read
        # overlap made the slice non-empty even when fully caught up,
        # so a stale trigger billed a duplicate-overlap distill
        # (audit L-B17).
        if _prev >= full_len:
            return [], _prev, 0, False
        _start = max(0, _prev - _DISTILL_REREAD_OVERLAP)
        slice_full = conversation[_start:]

        kin_ctx = max(_num_ctx_of(cfg), 4096)
        chat_model = strip_model_annotation(cfg.get("model", "") or "")
        memory_model = ((cfg.get("memory_model") or "").strip()
                        or chat_model)
        try:
            sum_ctx_real = _model_context_length(memory_model)
        except Exception:
            sum_ctx_real = None
        budget_ctx = (kin_ctx if not sum_ctx_real
                      else min(kin_ctx, int(sum_ctx_real)))
        # Reserve room for the fixed input the conversation bite can't crowd
        # out. _DISTILL_RESERVE_TOKENS is the flat part (response cap + prompt
        # scaffolding + margin); the soul and existing memory.md are variable
        # per-kin, so measure them and reserve on TOP. Since distillation
        # started loading the kin's soul (so notes come back in-voice), a flat
        # reserve under-counted — a big-souled kin (~3k tok of soul)
        # overshot the summarizer's window by roughly a soul's worth. Charging
        # them here shrinks the bite to fit instead.
        soul_tokens = estimate_tokens(load_soul(agent_name) or "")
        memory_tokens = estimate_tokens(load_memory(agent_name) or "")
        reserve = _DISTILL_RESERVE_TOKENS + soul_tokens + memory_tokens
        budget = max(2048, budget_ctx - reserve)
        ratio = llm_backend.token_calibration_ratio(agent_name)

        cap_idx = len(slice_full)
        running = 0.0
        for i, m in enumerate(slice_full):
            running += estimate_message_tokens(m, chat_model) * ratio
            if running > budget:
                cap_idx = i
                break
        token_cap_idx = cap_idx

        # Day/hour pacing: the boundary is derived from the FIRST
        # genuinely new message (at _prev), not from anything in the
        # re-read overlap before it — the overlap is already-seen
        # content being re-shown for continuity, and its timestamps
        # have nothing to do with how far into new territory this bite
        # is allowed to go. By construction the boundary always sits
        # strictly after that anchor message's own day/hour, so the
        # loop below can never cap at or before `overlap_len` — a bite
        # always covers at least one new message even under the
        # tightest boundary.
        boundary_cap_idx = None
        if pacing in (self._WALK_PACING_DAY, self._WALK_PACING_HOUR):
            overlap_len = _prev - _start
            anchor_ts = conversation[_prev].get("ts") if _prev < full_len else None
            boundary_ts = self._walk_boundary_ts(pacing, anchor_ts)
            if boundary_ts is not None:
                for i in range(overlap_len, len(slice_full)):
                    m_ts = slice_full[i].get("ts")
                    if m_ts and m_ts >= boundary_ts:
                        boundary_cap_idx = i
                        break
                if boundary_cap_idx is not None:
                    cap_idx = min(cap_idx, boundary_cap_idx)
        # True iff the boundary is the (tied-or-)tighter constraint —
        # i.e. this bite stopped because the unit ended, not because
        # the token budget ran out first with more of the same
        # day/hour still waiting.
        hit_boundary = (boundary_cap_idx is not None
                        and boundary_cap_idx <= token_cap_idx)

        # Always send at least one message. If the very first one is
        # over budget on its own we'd loop forever otherwise; let the
        # provider truncate or fail instead.
        if cap_idx == 0 and slice_full:
            cap_idx = 1
        # Forbid a no-advance bite. When the re-read overlap fills the
        # budget and the next message (the one just past _prev) is too
        # big to also fit, the unguarded calculation produces
        # cap_idx == (_prev - _start), so _start + cap_idx == _prev —
        # the bookmark would be "advanced" to where it already was.
        # A walk-from-start in that state schedules its next chunk
        # forever and never makes progress: dozens of bites in a few
        # minutes, the bookmark stuck where it started, and the staging
        # file filling with slight rewordings of the same overlap window.
        # When this would happen, force cap_idx to at least one message
        # past _prev so the bookmark genuinely moves forward. The bite
        # may then overrun the budget by one message — the provider
        # will either swallow it or return an error, both of which are
        # better outcomes than an infinite loop that silently burns
        # cycles on a paid model.
        if slice_full and _start + cap_idx <= _prev:
            needed = (_prev - _start) + 1
            if needed <= len(slice_full):
                cap_idx = needed
        # The bookmark never moves backwards: a tiny budget that caps
        # the bite inside the re-read overlap must not regress
        # distilled_through below the previous bookmark, or every
        # subsequent run re-bills the same overlap (audit L-B17).
        bite = self._fit_oversized_messages(
            slice_full[:cap_idx], budget, chat_model, ratio)
        return (bite, max(_start + cap_idx, _prev),
                budget_ctx, hit_boundary)

    def _fit_oversized_messages(self, bite, budget, model, ratio):
        """Trim any SINGLE message that is bigger than the whole bite
        budget, for the summariser's copy only. Never touches disk.

        The rule just above deliberately sends at least one message even
        when it overruns — otherwise a giant message would cap the bite
        at zero and the walk would spin forever without advancing. That
        was right while "overruns" meant a little. It is fatal when one
        message is several times the entire context window.

        Observed live: a kin's history contained a single pasted user turn
        of 440,659 characters — about 110,000 tokens against a 32,768
        window. The bite handed it over whole, on the reasoning that the
        provider would "truncate or fail". Local Ollama did neither: it
        chewed for roughly forty minutes and timed out, the bookmark did
        not move, the walk queued the identical chunk again, and it failed
        three times in a row overnight with no way to get past it. An
        infinite timeout loop is the same deadlock as the infinite
        no-advance loop that guard exists to prevent, only slower and
        with the model held the whole time.

        Truncating beats skipping. Distillation is summarising, and a
        summary of the first N thousand characters of a huge paste is
        worth having; skipping the message would advance the bookmark
        past content that then never reaches memory at all, silently.
        The marker says what happened and where the whole thing lives, so
        the summariser can say so rather than inventing continuity.

        Fail-soft: any error returns the bite untouched. A sizing helper
        must never be the reason a distillation does not run."""
        try:
            budget = max(512, int(budget))
            out, changed = [], False
            for m in (bite or []):
                text = m.get("content") if isinstance(m, dict) else None
                if not isinstance(text, str) or not text:
                    out.append(m)
                    continue
                est = estimate_message_tokens(m, model) * (ratio or 1.0)
                if est <= budget:
                    out.append(m)
                    continue
                # Convert the token budget back to characters through the
                # same estimate, so this tracks whatever that estimator
                # does rather than a second guess about tokenisation.
                keep = max(1000, int(len(text) * (budget / est)))
                trimmed = dict(m)
                trimmed["content"] = (
                    text[:keep]
                    + "\n\n[hearthkin: this message is "
                    + f"{len(text):,} characters and was cut to {keep:,} "
                    + "here so it fits the summariser's window. The whole "
                    + "of it is in conversation.jsonl — summarise what "
                    + "is present, and do not infer how it ended.]")
                out.append(trimmed)
                changed = True
            if changed:
                try:
                    self._log("distillation: one oversized message was cut "
                              "down to fit the summariser's window")
                except Exception:
                    pass
            return out
        except Exception:
            return bite

    def _log_distill_trigger(self, agent_name, source_label, scope_key,
                             conversation, cfg):
        """One always-on line each time a distillation starts, naming WHICH
        trigger fired and how far behind the scope actually was.

        There are four ways a distillation can begin — the every-N counter,
        the %-of-context measure, leaving a kin (or a room, or the app), and
        somebody pressing a button — and until now none of them left a record.
        So "it keeps distilling" could not be answered except by theorising,
        and the theories were wrong three times running: recall bloating the
        context (it is never persisted), the calibration ratio (it cancels
        out), a half-finished walk (there wasn't one).

        The leaving-a-kin trigger is the one worth being able to see, because
        its threshold is ONE message — not the 70%-of-context figure the
        status line borrows its wording from. Two very different triggers
        described in the same words is exactly what made the numbers look
        wrong, and a reader could not tell them apart.

        Never raises: a logging fault must not cost a kin its memory write."""
        try:
            from kin_persistence import LOGS_DIR
            import datetime
            offsets = cfg.get("distill_offsets") or {}
            bm = int(offsets.get(scope_key, 0) or 0)
            total = len(conversation or [])
            behind = max(0, total - bm)
            pct = ""
            try:
                pct = f" pct={self._undistilled_context_pct(agent_name, scope_key, conversation, cfg):.0f}%"
            except Exception:
                pass
            path = LOGS_DIR / "distill_triggers.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                    f"[{agent_name}] trigger={source_label} scope={scope_key} "
                    f"bookmark={bm} turns={total} behind={behind}{pct} "
                    f"at_pct={cfg.get('memory_distill_at_pct')} "
                    f"every_n={cfg.get('memory_distill_every_n')} "
                    f"on_leave={cfg.get('memory_distill_on_close')}\n")
        except Exception:
            pass

    def _kick_off_distillation(self, agent_name, conversation, source_label="manual", scope_key="desktop"):
        """Run distillation in a background thread. Updates memory.md on success
        and refreshes the editor if the user is still viewing this kin.

        `scope_key` is the (agent, scope) counter that triggered this
        distillation — used on completion to reset only THAT scope's
        counter, leaving other scopes' progress untouched.

        The per-run slice is bounded by `_distill_bite` — on a kin
        with a huge undistilled tail, the first distillation absorbs
        only what fits in the summarizer's window, advances the
        bookmark to where it actually stopped, and the next trigger
        fire takes the next bite. _on_distill_done flags 'more
        pending' on the status line + NVDA when the new bookmark is
        still short of the conversation's current length, so the
        operator can hear that catch-up is ongoing."""
        if self._is_distill_in_flight(agent_name):
            return
        # Heartbeats are the least urgent thing this app does; a
        # distillation is real work the operator or a redistill walk
        # explicitly asked for. Tell any heartbeat that's currently running
        # to stand down before this one starts — otherwise one kin's
        # heartbeat can hold a model for several minutes while another
        # kin's redistill sits waiting for it, with no way to interrupt
        # short of quitting Hearthkin. See
        # CronExecMixin._signal_heartbeats_to_stop for what this can and
        # can't actually stop.
        self._signal_heartbeats_to_stop()
        cfg = load_agent_config(agent_name)
        self._log_distill_trigger(agent_name, source_label, scope_key,
                                  conversation, cfg)
        chat_model = strip_model_annotation(cfg.get("model", ""))
        memory_model = (cfg.get("memory_model") or "").strip() or chat_model
        existing_memory = load_memory(agent_name)

        # Pacing only ever applies to an ACTIVE walk on this exact
        # (agent, scope) — an ordinary auto-distill trigger (not a
        # walk at all) always behaves as pure token-budget chunking,
        # regardless of whatever pacing a past walk on this scope
        # happened to leave recorded.
        walking_now = bool(getattr(self, "_walking_from_start", None)
                           and self._walking_from_start.get((agent_name, scope_key)))
        pacing = (self._walk_pacing_on_disk(agent_name, scope_key)
                 if walking_now else None)

        # Incremental + bounded: see _distill_bite. distilled_through
        # is where the bookmark will advance to on success — not
        # necessarily the full conversation length, so a legacy huge
        # tail catches up across multiple runs instead of choking on
        # one impossible swallow.
        conversation, distilled_through, budget_ctx, hit_boundary = (
            self._distill_bite(conversation, cfg, scope_key, agent_name,
                               pacing=pacing))
        # _distill_bite returns an empty slice when the bookmark has
        # already caught up to the conversation end — e.g. a stale
        # counter trips _maybe_auto_distill after a manual distill
        # just cleared the tail. Skip the worker entirely; running it
        # would bill an LLM call on an empty conversation and leave
        # _distilling stuck until completion (audit H17). We set
        # _distilling AFTER this check so an empty bite is a clean
        # no-op rather than a brief flag-then-clear.
        if not conversation:
            # An empty bite during a walk means the bookmark has reached
            # the end — the walk is DONE, and this is the one path that
            # reaches that conclusion without going through
            # _on_distill_done. Without closing it out here the walk
            # stayed flagged forever: no chunks firing, Cancel the only
            # way out, and "Redistill from start" refusing because a
            # walk was supposedly still running.
            walking = getattr(self, "_walking_from_start", None) or {}
            if walking.get((agent_name, scope_key)):
                self._end_walk(agent_name, scope_key)
                # Matches the other walk-complete path in
                # _on_distill_done: nothing left to undo once a walk
                # has genuinely finished.
                self._persist_walk_prior(agent_name, scope_key, None)
                self._announce_walk_complete(agent_name, scope_key)
            self._drain_distill_queue(agent_name)
            return
        self._distilling[agent_name] = time.time()
        self._start_distill_progress(agent_name)

        dlg = self._dialog_for(agent_name)
        if dlg is not None:
            dlg.distill_btn.Disable()
            dlg.memory_status.SetLabel(f"Distilling memory ({source_label})...")
        self._set_status(f"Distilling memory for {agent_name}...")

        # Temperature is intentionally omitted: distill_memory_blocking owns
        # it (setdefault 0.6) so the in-voice framing has a little room. Setting
        # it here would override that and silently pin the old flat 0.3.
        options = {
            "num_predict": 6000,
            "num_ctx": budget_ctx,
        }

        prompt_template = load_distill_prompt(agent_name)

        def worker():
            try:
                result = distill_memory_blocking(
                    agent_name, conversation, existing_memory, memory_model,
                    sys_prompt_template=prompt_template, options=options,
                    on_progress=lambda n: self._note_distill_progress(
                        agent_name, n),
                    think_effort=think_effort_of(cfg),
                )
                wx.CallAfter(self._on_distill_done, agent_name, scope_key,
                             result.get("new_entries", ""), None, result,
                             distilled_through, source_label,
                             hit_boundary=hit_boundary)
            except Exception as e:
                wx.CallAfter(self._on_distill_done, agent_name, scope_key,
                             None, str(e), None, distilled_through, source_label,
                             hit_boundary=hit_boundary)

        # Registered BEFORE start so the slot is never briefly held with
        # no thread against it — a check landing in that gap would fall
        # through to the fallback clock.
        th = threading.Thread(target=worker, daemon=True)
        self._register_distill_thread(agent_name, th)
        th.start()

    def _announce_walk_complete(self, agent_name, scope_key):
        done_line = (f"Redistilling {scope_key} finished for "
                     f"{agent_name}.")
        try:
            self._set_status(done_line, speak=True)
        except Exception:
            pass

    def _walk_next_chunk(self, agent_name, scope_key, attempt=0):
        """Fire the next chunk of a redistill-from-start walk.

        This is the whole chain — _on_distill_done schedules it, and it
        schedules itself again when the slot is busy. Everything it can
        decide has an audible outcome; the one thing it must never do is
        stop without saying so, which is what the plain
        wx.CallLater(_kick_off_distillation) it replaced did on every
        collision.
        """
        walking = getattr(self, "_walking_from_start", None) or {}
        if not walking.get((agent_name, scope_key)):
            return   # cancelled, or finished by another path
        if getattr(self, "_closing", False):
            # Quitting mid-walk. Leave the on-disk record alone so the
            # next launch resumes; don't touch the UI on the way out.
            walking.pop((agent_name, scope_key), None)
            return
        if self._is_distill_in_flight(agent_name):
            # Something else holds the slot — most often an ordinary
            # auto-distill on one of this kin's other surfaces that
            # slipped into the gap between chunks. Wait it out.
            if attempt < _WALK_RETRY_MAX:
                wx.CallLater(
                    int(_WALK_RETRY_SECS * 1000), self._walk_next_chunk,
                    agent_name, scope_key, attempt + 1)
                return
            self._end_walk(agent_name, scope_key, keep_on_disk=True)
            self._announce_problem(
                f"Redistilling {scope_key} for {agent_name} is paused — "
                f"something else has been using the memory model for "
                f"two minutes. Press Continue redistilling in Settings, "
                f"Memory when it's free.")
            return
        convo = self._convo_for_distill_scope(agent_name, scope_key)
        if not convo:
            self._end_walk(agent_name, scope_key)
            self._announce_walk_complete(agent_name, scope_key)
            return
        self._kick_off_distillation(
            agent_name, convo,
            source_label=f"walk-from-start-{scope_key}",
            scope_key=scope_key)
        # _kick_off_distillation returns quietly in two cases: the slot
        # was taken between our check and the call, or the bite was
        # empty (which it handles as walk-complete itself). If the walk
        # is still flagged but nothing is running, we're in the first
        # case — retry rather than leave a live flag with a dead chain.
        walking = getattr(self, "_walking_from_start", None) or {}
        if (walking.get((agent_name, scope_key))
                and not self._is_distill_in_flight(agent_name)):
            if attempt < _WALK_RETRY_MAX:
                wx.CallLater(
                    int(_WALK_RETRY_SECS * 1000), self._walk_next_chunk,
                    agent_name, scope_key, attempt + 1)
            else:
                self._end_walk(agent_name, scope_key, keep_on_disk=True)
                self._announce_problem(
                    f"Redistilling {scope_key} for {agent_name} is "
                    f"paused — it couldn't get started. Press Continue "
                    f"redistilling in Settings, Memory to try again.")

    def _resume_pending_distill_walks(self):
        """On startup, pick up any walk that was interrupted.

        Quitting part-way through used to end a walk for good. The work
        already done survived (the bookmark is on disk), but nothing
        continued it and the only button offered reset the bookmark to
        zero — so finishing a long redistill meant leaving the app open
        for its whole duration, and the natural thing to press threw the
        progress away.

        Resuming is deliberate rather than optional: the user already
        agreed to this walk, including its cost estimate, when they
        started it. What's added is the saying-so — it announces itself,
        and Cancel redistill is live from the moment it starts.
        """
        try:
            agents = list_agents() or []
        except Exception:
            return
        resumed = []
        for agent_name in agents:
            try:
                scopes = self._walk_scopes_on_disk(agent_name)
            except Exception:
                continue
            for scope_key in scopes:
                try:
                    convo = self._convo_for_distill_scope(
                        agent_name, scope_key)
                    cfg = load_agent_config(agent_name) or {}
                    offsets = cfg.get("distill_offsets") or {}
                    bm = live_distill_bookmark(
                        offsets.get(scope_key, 0), len(convo))
                    if not convo or bm >= len(convo):
                        # Finished after all (or the surface is gone) —
                        # clear the stale record silently. Announcing
                        # "resumed and immediately finished" is noise.
                        self._persist_walk(agent_name, scope_key, False)
                        continue
                    self._start_walk(agent_name, scope_key)
                    resumed.append(
                        (agent_name, scope_key, len(convo) - bm))
                except Exception:
                    continue
        if not resumed:
            return
        # One line covering everything picked up, rather than one per
        # walk: on a machine with several kin this is the first thing
        # said after launch and it should not be a list read aloud.
        if len(resumed) == 1:
            kin, scope, left = resumed[0]
            line = (f"Picking up where redistilling {scope} left off for "
                    f"{kin} — {left:,} messages to go. Cancel it in "
                    f"Settings, Memory.")
        else:
            total = sum(r[2] for r in resumed)
            names = ", ".join(sorted({r[0] for r in resumed}))
            line = (f"Picking up {len(resumed)} unfinished redistills "
                    f"({names}) — {total:,} messages to go. Cancel them "
                    f"in Settings, Memory.")
        try:
            self._set_status(line, speak=True)
        except Exception:
            pass
        # Stagger the starts: the in-flight gate is per-kin, so two kin
        # could genuinely run at once, but firing everything on the same
        # tick makes the first seconds after launch needlessly busy.
        for i, (agent_name, scope_key, _left) in enumerate(resumed):
            wx.CallLater(1500 + i * 2000, self._walk_next_chunk,
                         agent_name, scope_key)

    def _drain_distill_queue(self, agent_name):
        """Kick off the next scope in this kin's 'Distill all surfaces'
        queue, if any. No-op when nothing is queued. Called from every
        termination path of _on_distill_done — including errors and
        save-failures — so a partial failure on one scope doesn't
        orphan the rest of the queue (audit H12)."""
        queue = self._distill_queue.get(agent_name)
        if not queue:
            return
        cfg2 = load_agent_config(agent_name) or {}
        offsets2 = cfg2.get("distill_offsets") or {}
        while queue:
            next_scope = queue.pop(0)
            next_convo = self._convo_for_distill_scope(
                agent_name, next_scope)
            if not next_convo:
                continue
            nbm = live_distill_bookmark(offsets2.get(next_scope, 0), len(next_convo))
            if nbm >= len(next_convo):
                continue
            self._distill_queue[agent_name] = queue
            wx.CallLater(
                500, self._kick_off_distillation,
                agent_name, next_convo,
                f"all-{next_scope}", next_scope)
            return
        self._distill_queue.pop(agent_name, None)

    def _on_distill_done(self, agent_name, scope_key, new_entries, error, usage_info=None, distilled_through=None, source_label=None, hit_boundary=False):
        # Under the 2026-06-01 staging architecture (see
        # docs/design/memory-architecture-and-ritual-framing.md), the
        # summarizer's output goes to a per-scope staging file the kin
        # reads during nightly tending — NOT to memory.md. The kin is
        # the arbiter of what becomes canonical memory; nothing
        # automatic touches memory.md anymore. The previous parameter
        # `new_memory` (which carried the full spliced file) was
        # repurposed: it now carries `new_entries` (only the new
        # content the summarizer produced this pass) and we append to
        # staging rather than overwriting memory.md.
        self._release_distill_slot(agent_name)
        dlg = self._dialog_for(agent_name)
        if error:
            # A walk-from-start on this scope stops here — we don't know
            # why the chunk failed (provider outage, key, model unloaded)
            # and hammering the next chunk into the same fault would burn
            # through the rest of the history producing nothing.
            #
            # It PAUSES rather than dying: the on-disk record stays, so
            # Resume (and the next launch) continues from the bookmark
            # instead of the beginning. And it is SAID OUT LOUD. Both of
            # those are the point — a walk used to fail at chunk 7 of 40
            # with the only notice a line in the Activity field that
            # isn't spoken and vanishes after four seconds, so the first
            # anyone knew was finding no progress hours later and
            # pressing the from-start button again.
            # Write it down. Until this existed, a distillation failure
            # reached the Activity field and the Memory tab label and
            # nowhere else — both transient — so by the time anyone
            # noticed no progress, the reason had been gone for hours and
            # the only honest answer to "what did it say?" was that
            # nobody knew. These runs are unattended, which is exactly
            # why they need the always-on-log treatment every other
            # background failure in this app already gets.
            try:
                append_failure_log(
                    "distill_errors.log", agent_name,
                    f"distill (scope={scope_key}, source={source_label}, "
                    f"model={(load_agent_config(agent_name) or {}).get('memory_model') or 'chat model'})",
                    error)
            except Exception:
                pass
            walking = getattr(self, "_walking_from_start", None) or {}
            was_walking = bool(walking.get((agent_name, scope_key)))
            if was_walking:
                self._end_walk(agent_name, scope_key, keep_on_disk=True)
            if dlg is not None:
                dlg.memory_status.SetLabel(f"Distill error: {error[:80]}")
                dlg.distill_btn.Enable()
            if was_walking:
                self._announce_problem(
                    f"Redistilling {scope_key} for {agent_name} stopped: "
                    f"{error[:120]}. Your progress is kept — press "
                    f"Continue redistilling in Settings, Memory to carry "
                    f"on.")
            else:
                self._announce_problem(
                    f"Saving notes failed for {agent_name}: {error[:120]}")
            # Continue draining the "Distill all surfaces" queue —
            # one failed scope shouldn't orphan the rest (audit H12).
            self._drain_distill_queue(agent_name)
            return
        # Append new entries to the per-scope staging file. memory.md
        # is NOT touched. If the summarizer produced nothing (empty
        # new_entries), skip the staging write — the bookmark still
        # advances below so the next pass doesn't reprocess these
        # turns.
        try:
            if (new_entries or "").strip():
                append_staging(agent_name, scope_key, new_entries,
                               source_label=source_label)
        except Exception as e:
            try:
                append_failure_log(
                    "distill_errors.log", agent_name,
                    f"staging write (scope={scope_key})", e)
            except Exception:
                pass
            if dlg is not None:
                dlg.distill_btn.Enable()
            # Same pause-don't-die treatment as an outright distill
            # failure above: the notes for this chunk are lost, but the
            # bookmark hasn't moved, so resuming re-reads them.
            walking = getattr(self, "_walking_from_start", None) or {}
            was_walking = bool(walking.get((agent_name, scope_key)))
            if was_walking:
                self._end_walk(agent_name, scope_key, keep_on_disk=True)
                self._announce_problem(
                    f"Redistilling {scope_key} for {agent_name} stopped — "
                    f"couldn't write the notes: {e}. Your progress is "
                    f"kept; press Continue redistilling in Settings, "
                    f"Memory.")
            else:
                self._announce_problem(
                    f"Distilled but couldn't stage for {agent_name}: {e}")
            self._drain_distill_queue(agent_name)
            return
        # No need to invalidate kin text cache — memory.md didn't
        # change.
        # Reset just the scope that triggered this run; other scopes
        # keep their progress toward their own next distillation.
        self._messages_since_distill[(agent_name, scope_key)] = 0
        # Advance this scope's distillation bookmark so the next run
        # digests only turns after this point. Persisted in the kin's
        # config. Reached only on success (memory saved just above) — a
        # failed distillation leaves the bookmark put, so the next run
        # re-reads those turns rather than skipping them.
        digested_this_run = None
        if distilled_through is not None:
            try:
                bcfg = load_agent_config(agent_name)
                offsets = dict(bcfg.get("distill_offsets") or {})
                # How far this run actually got, before the bookmark moves.
                # Compared against what's left, it's what tells a backlog
                # (chasing it every turn accomplishes nothing) apart from an
                # ordinary catch-up. See _note_backlog_pace.
                try:
                    digested_this_run = max(
                        0, int(distilled_through)
                        - int(offsets.get(scope_key, 0) or 0))
                except (TypeError, ValueError):
                    digested_this_run = None
                offsets[scope_key] = int(distilled_through)
                bcfg["distill_offsets"] = offsets
                # Stamp WHEN the bookmark moved. The Memory tab's counter
                # is the only window an operator has onto whether
                # distillation is alive — and a bare "N undistilled" looks
                # exactly the same whether a walk is chewing through
                # chunks or died twenty minutes ago. Without a time
                # attached, a number that's merely stale is
                # indistinguishable from one that's wrong, which sent a
                # real operator hunting a bug that wasn't there.
                stamps = dict(bcfg.get("distill_advanced_at") or {})
                stamps[scope_key] = datetime.datetime.now().replace(
                    microsecond=0).isoformat()
                bcfg["distill_advanced_at"] = stamps
                save_agent_config(agent_name, bcfg)
            except Exception as e:
                # A silently-lost bookmark means the next trigger
                # re-distills (re-bills) the same turns, repeatedly —
                # at minimum leave a trace (audit M-F11).
                try:
                    append_failure_log(
                        "save_failures.log", agent_name,
                        f"distill bookmark advance (scope={scope_key}, "
                        f"through={distilled_through})", e,
                    )
                except Exception:
                    pass
        # How many messages still sit past the new bookmark? Surfaces
        # in the cost line + NVDA as "N more pending" so a legacy
        # catch-up is audible — and the operator can hear progress as
        # the count shrinks across successive trigger fires.
        messages_remaining = 0
        if distilled_through is not None:
            try:
                current_len = len(self._convo_for_distill_scope(
                    agent_name, scope_key))
                messages_remaining = max(
                    0, current_len - int(distilled_through))
            except Exception:
                pass
        # A run that ends still behind means the automatic triggers should
        # wait before firing again — otherwise they fire after every single
        # reply for as long as the backlog lasts, taking the model from the
        # conversation. Only the automatic triggers are paced: a walk, a
        # queue drain, an on-close run and anything the person pressed
        # themselves are all deliberate acts and are never held back.
        if str(source_label or "").startswith(("every-", "ctx-")):
            self._note_backlog_pace(
                agent_name, scope_key, digested_this_run, messages_remaining)
        if dlg is not None:
            # memory.md is unchanged — the memory editor stays as it
            # was. We just announce that notes have been staged so
            # the operator (and the kin, on next tending) knows
            # there's something to review.
            stamp = datetime.datetime.now().strftime('%H:%M')
            dlg.memory_status.SetLabel(
                f"Notes staged for tending ({scope_key}) at {stamp}")
            dlg.distill_btn.Enable()
            # Repaint the per-surface counter rows now that this scope's
            # bookmark just moved. Without this, the Memory tab keeps
            # showing the pre-distill numbers until the user manually
            # hits Refresh counters — confusing especially during a
            # walk-from-start where the bookmark is advancing repeatedly.
            try:
                dlg._refresh_chat_counters_display()
            except Exception:
                pass
        # Surface the cost of this distillation in the Activity field so
        # the user can see what just spent money. Without this the only
        # signal was an after-the-fact balance check. Distillation usage
        # is already logged to usage.log via surface="distill" (see
        # llm_backend._log_call_usage), this just bubbles the per-call
        # cost up to the live Activity field as it lands.
        cost_painted = False
        if usage_info and isinstance(usage_info, dict):
            try:
                from llm_backend import _estimate_call_cost, _cached_tokens_from_usage
                p_tok = int(usage_info.get("prompt_tokens") or 0)
                c_tok = int(usage_info.get("completion_tokens") or 0)
                model = str(usage_info.get("model") or "")
                cached_tok = _cached_tokens_from_usage(usage_info)
                cost = _estimate_call_cost(model, p_tok, c_tok, cached_tokens=cached_tok)
                model_short = model.split("/")[-1] if "/" in model else model
                if cost > 0:
                    cost_str = f"~${cost:.4f}"
                else:
                    cost_str = "(local — no charge)"
                cost_line = (
                    f"Staged {agent_name}'s notes ({scope_key}) · "
                    f"{p_tok:,} in / {c_tok:,} out · "
                    f"{cost_str} ({model_short})"
                )
                if messages_remaining > 0:
                    cost_line += f" · {messages_remaining:,} more pending"
                self._set_status(cost_line)
                # Speak via NVDA too — the Activity field is only
                # audible when focused, and a follow-on
                # auto-consolidation (if the memory crossed
                # MEMORY_CONSOLIDATE_THRESHOLD_CHARS) will paint a
                # new status line ~500ms later that hides this one.
                # Speaking guarantees the user hears the cost
                # regardless.
                try:
                    nvda_speak(cost_line)
                except Exception:
                    pass
                cost_painted = True
            except Exception:
                # Fall through to the plain status line below
                pass
        if not cost_painted:
            fallback_line = f"Notes staged for {agent_name} ({scope_key})."
            if messages_remaining > 0:
                fallback_line += f" ({messages_remaining:,} more pending)"
            self._set_status(fallback_line)

        # Walk-from-start auto-chain. If "Redistill selected from
        # start" launched a walk on this (kin, scope), the bookmark
        # has just advanced one chunk's worth. If more content sits
        # past the new bookmark, schedule the next chunk and exit
        # before auto-consolidate / queue-drain — both are
        # inappropriate mid-walk. When the walk finishes (bookmark
        # reaches conversation end) we clear the flag, announce it,
        # and fall through to the normal post-distill housekeeping
        # so a fat post-walk memory.md still gets its consolidation
        # pass.
        walking = getattr(self, "_walking_from_start", None) or {}
        walk_key = (agent_name, scope_key)
        if walking.get(walk_key):
            try:
                walk_convo = self._convo_for_distill_scope(
                    agent_name, scope_key)
                bm = (int(distilled_through)
                      if distilled_through is not None else 0)
                # One chunk done — sound it, pitched by how far through
                # the redistill is. This is the progress report that
                # actually arrives: the spoken cost line above is
                # reliably cut off by the user's own typing, because a
                # screen reader with character echo has no free moment,
                # and that's a constant rather than an occasional
                # collision. So a long redistill sounded exactly like a
                # stalled one. A rising pitch is the whole message.
                try:
                    self._chime_progress(
                        bm / len(walk_convo) if walk_convo else 1.0)
                except Exception:
                    pass
                if bm < len(walk_convo):
                    pending_after = max(0, len(walk_convo) - bm)
                    # Pacing decides whether this bite auto-chains into
                    # the next one or stops here and waits. 'chunk'
                    # pacing always stops. 'day'/'hour' pacing stops
                    # only when THIS bite is what actually hit the
                    # calendar boundary (hit_boundary, computed in
                    # _distill_bite) — a big day spanning several bites
                    # keeps auto-chaining through the rest of that same
                    # day exactly like unattended pacing does; only the
                    # bite that reaches the day's/hour's end pauses.
                    pacing = self._walk_pacing_on_disk(agent_name, scope_key)
                    should_pause = self._walk_should_pause_after_bite(
                        pacing, hit_boundary)
                    if should_pause:
                        # A pause, not a cancel or a finish —
                        # keep_on_disk=True so Continue redistilling
                        # (or the next launch) picks up exactly here.
                        # Same mechanism an error-triggered pause
                        # already uses; only the trigger differs.
                        self._end_walk(agent_name, scope_key,
                                       keep_on_disk=True)
                        last_ts = (walk_convo[bm - 1].get("ts")
                                  if 0 < bm <= len(walk_convo) else None)
                        when = self._format_walk_pause_when(pacing, last_ts)
                        if pacing == self._WALK_PACING_CHUNK:
                            line = (
                                f"Redistilling {scope_key} for {agent_name}: "
                                f"one chunk done, {pending_after:,} "
                                f"messages remain. Press Continue "
                                f"redistilling for the next chunk.")
                        else:
                            unit = ("day" if pacing == self._WALK_PACING_DAY
                                    else "hour")
                            through = f" through {when}" if when else ""
                            line = (
                                f"Redistilling {scope_key} for {agent_name}: "
                                f"distilled{through}, {pending_after:,} "
                                f"messages remain. Press Continue "
                                f"redistilling for the next {unit}.")
                        self._set_status(line, speak=True)
                        return
                    self._set_status(
                        f"Redistilling {scope_key} — next chunk shortly "
                        f"({pending_after:,} messages remaining)")
                    # Goes through _walk_next_chunk, not straight to
                    # _kick_off_distillation: that call returns quietly
                    # when the slot is busy, and a walk that lost a race
                    # with an ordinary auto-distill on another surface
                    # then never fired again — flag still set, chain
                    # dead, and the Memory tab refusing a new walk
                    # because one was "already running".
                    wx.CallLater(
                        1500, self._walk_next_chunk,
                        agent_name, scope_key)
                    return
                # Walk complete: clear flag (in memory AND on disk),
                # announce, fall through so a fat post-walk memory.md
                # can auto-consolidate.
                self._end_walk(agent_name, scope_key)
                # Forget the pre-redistill bookmark. It exists so Cancel
                # can undo the rewind; once the redistill has reached the
                # end there is nothing to undo, and leaving it recorded
                # would let a later Cancel drag the bookmark BACK to a
                # position from before work that really did happen.
                self._persist_walk_prior(agent_name, scope_key, None)
                self._announce_walk_complete(agent_name, scope_key)
            except Exception:
                # Defensive: if anything in the walk-chain logic
                # blows up, clear the flag and fall through to
                # normal behavior rather than getting stuck. Keep the
                # on-disk record so it's resumable — a crash in our
                # bookkeeping is no reason to make them start over.
                self._end_walk(agent_name, scope_key, keep_on_disk=True)

        # Auto-consolidation is DISABLED under the 2026-06-01 staging
        # architecture. Consolidation now only fires when the kin
        # invokes it during tending, or when the operator hits the
        # manual button in Settings → Memory. Memory.md is no longer
        # being auto-rewritten by distillation (notes go to staging
        # instead), so the "memory.md crossed 20k chars" condition
        # cannot fire from distillation alone — it can only happen
        # if the kin or operator edited memory.md directly. In that
        # case they should also decide whether to consolidate.
        # The 30-min cooldown helper (_AUTO_CONSOLIDATE_COOLDOWN_SECS)
        # and `_last_consolidation_at` dict are kept as
        # belt-and-suspenders against any future re-introduction.
        # See docs/design/memory-architecture-and-ritual-framing.md.

        # "Distill all surfaces" queue drain: extracted to a helper so
        # the error / save-fail branches can also drain (audit H12).
        self._drain_distill_queue(agent_name)
