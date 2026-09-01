"""CronExecMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    ExecApprovalDialog, WebcamApprovalDialog, _CRON_USER_TEXT_MARKER, _force_foreground,
    add_to_allowlist, append_failure_log, cron_helpers, is_in_allowlist, list_agents,
    load_agent_config, match_denylist, os, threading, validate_kin_name, wx,
)


# What the MODEL is told when a desktop-gated exec doesn't run. Mirrors
# telegram_bot._DENY_RESULTS: only a real refusal may be reported as one.
# "unavailable" means we could not ask at all (shutdown in progress, or the
# approval dialog failed to build) — nobody saw it, so nobody said no.
_EXEC_REFUSALS = {
    "deny": "[denied by user]",
    "unavailable": (
        "[NOT RUN — I couldn't ask for approval (Hearthkin is closing, or the "
        "approval dialog failed to open). Nobody refused it and nobody saw it. "
        "Don't tell the operator they denied this — say the request couldn't "
        "be put to them, and offer to try again.]"
    ),
}


class CronExecMixin:

    def _wrap_exec_executor(self, executors, agent_name):
        """If `exec` is in the executor dict, return a copy with that
        entry wrapped in the harness-side approval flow. Other entries
        pass through unchanged. No-op if exec isn't in the set.

        Approval logic (in order):
          1. If the exact command string is in the kin's remembered
             allowlist (~/.hearthkin/kin/<kin>/exec_allowlist.json),
             skip the gate and run. The user already said yes.
          2. Otherwise read the kin's `tool_trust` from config:
             - "full" → no gating, run.
             - "trusted" + no denylist match → run.
             - Anything else → request approval. If denied, return
               "[denied by user]" without running. If allowed +
               remembered, add the command to the allowlist before
               running.

        The wrap closure captures `self` (the frame, needed for the
        dialog) and `agent_name` (needed for the allowlist + config).
        Other tools (read_file etc.) don't get wrapped — their safety
        is enforced inside the tool itself (kin-scoped paths, etc.)."""
        if "exec" not in executors:
            return executors
        raw_exec = executors["exec"]

        def _wrapped(args):
            command = args.get("command", "") if isinstance(args, dict) else ""
            if is_in_allowlist(agent_name, command):
                return raw_exec(args)
            # Read tool_trust fresh per call — wrap-time capture meant
            # a mid-session Settings change didn't apply until the
            # tools were reloaded (audit L-B30).
            try:
                cfg = load_agent_config(agent_name)
            except Exception:
                cfg = {}
            trust = cfg.get("tool_trust", "untrusted")
            if trust == "full":
                return raw_exec(args)
            denylist_match = match_denylist(command)
            if trust == "trusted" and not denylist_match:
                return raw_exec(args)
            if denylist_match:
                reason = f"Matched: {denylist_match}"
            else:
                reason = (
                    "Kin trust level is 'untrusted' — every exec call needs "
                    "your approval. Switch to 'trusted' in the kin's Settings "
                    "to gate only obviously destructive shapes (rm -rf /, "
                    "force-push to main, Windows drive wipes, etc.)."
                )
            decision = self._request_exec_approval(agent_name, command, reason)
            # Fail CLOSED: only an explicit allow/remember runs. This used to
            # refuse on an exact DENY match and let anything else fall through
            # to raw_exec — fine when "deny" was the only other value, a
            # silent gate bypass the moment a new outcome existed.
            if decision == ExecApprovalDialog.DECISION_REMEMBER:
                try:
                    add_to_allowlist(agent_name, command)
                except Exception:
                    pass
            elif decision != ExecApprovalDialog.DECISION_ALLOW:
                return _EXEC_REFUSALS.get(decision, _EXEC_REFUSALS["deny"])
            return raw_exec(args)

        wrapped = dict(executors)
        wrapped["exec"] = _wrapped
        return wrapped

    def _wrap_exec_for_remote(self, executors, agent_name, surface_key):
        """Exec-approval wrapper for a REMOTE surface (Discord, and any
        future non-Telegram remote surface) whose approval pops on the
        OPERATOR's desktop. Deliberately stricter than the desktop wrapper
        (_wrap_exec_executor), matching the Telegram surface's stance:

          1. Denylist match → hard deny. Never run a denylisted shape from
             a remote surface, regardless of trust or a remembered approval.
          2. Exact match in the kin's PER-SURFACE allowlist → run, no prompt.
             (A desktop-remembered command does NOT satisfy this — audit E1.)
          3. Otherwise → operator desktop approval dialog. tool_trust
             (trusted/full) does NOT auto-run a remote exec: the operator's
             local-convenience trust dial must not silently disable approval
             for a request that arrived over the internet (audit A2/B1). An
             operator who genuinely wants unattended remote exec sets
             `remote_unattended_exec: true` in the kin config — an explicit,
             JSON-only opt-in.

        surface_key scopes the remembered-approval list (e.g. "discord")."""
        if "exec" not in executors:
            return executors
        raw_exec = executors["exec"]

        def _wrapped(args):
            command = args.get("command", "") if isinstance(args, dict) else ""
            if not command:
                return ("exec: no command provided — nothing to run. Pass the "
                        "shell command in the `command` argument.")
            denylist_match = match_denylist(command)
            if denylist_match:
                return f"[denied by denylist: {denylist_match}]"
            if is_in_allowlist(agent_name, command, surface=surface_key):
                return raw_exec(args)
            try:
                cfg = load_agent_config(agent_name)
            except Exception:
                cfg = {}
            if (cfg.get("remote_unattended_exec")
                    and cfg.get("tool_trust") in ("trusted", "full")):
                return raw_exec(args)
            reason = (
                f"Remote request from the {surface_key} surface. Exec calls "
                "that arrive over a remote surface always need your approval "
                "here, regardless of the kin's trust level."
            )
            decision = self._request_exec_approval(agent_name, command, reason)
            # Fail CLOSED — see the note on the desktop wrapper above. This is
            # the REMOTE path, so an accidental fall-through would run a
            # command a remote user asked for without the operator ever
            # answering. Explicit allow only.
            if decision == ExecApprovalDialog.DECISION_REMEMBER:
                try:
                    add_to_allowlist(agent_name, command, surface=surface_key)
                except Exception:
                    pass
            elif decision != ExecApprovalDialog.DECISION_ALLOW:
                return _EXEC_REFUSALS.get(decision, _EXEC_REFUSALS["deny"])
            return raw_exec(args)

        wrapped = dict(executors)
        wrapped["exec"] = _wrapped
        return wrapped

    def _cron_staging_suffix(self, kin):
        """What to append to a wake-up prompt about the kin's staging, for the
        live-injection path (the wake-up routes through the ordinary desktop
        send, which is where the notes themselves get inlined).

        A kin WITH tools gets the usual count-only status line, so it knows
        whether there's anything to tend without fishing for it.

        A kin with NO tools gets the wake-up correction instead. The count line
        would sit next to the notes the send path is about to inline — telling
        it two scopes are pending directly above the two, already present — and
        the configured tending prompt tells it to call tools it doesn't have.
        See toolless_memory.py.

        Returns "" on any problem: a wake-up must fire regardless."""
        try:
            from kin_persistence import (
                load_app_prompt, load_kin_tools, staging_status_line,
            )
            import toolless_memory
            from kin_persistence import load_agent_config as _lac
            _m = (_lac(kin).get("model") or "").strip()
            if toolless_memory.use_text_memory_path(
                    load_kin_tools(kin) or [], _m):
                return "\n\n" + load_app_prompt("toolless_tend_note", kin)
            status = staging_status_line(kin)
            return "\n\n" + status if status else ""
        except Exception:
            return ""

    def _fold_unattended_cron_turns(self):
        """Count turns a scheduled wake-up took while this app was CLOSED.

        Distillation has two triggers. The percentage one measures the
        undistilled tail against the context window, reading both off disk, so
        it always saw these turns by itself. The "every N messages" one counts
        in memory, in this process — which a subprocess cannot reach. So a kin
        tended overnight had its turns count toward one trigger and not the
        other, and whether it kept remembering depended on which of the two
        settings its person happened to have chosen.

        Read-and-cleared in one step by the helper, so a fault here loses a
        tick rather than replaying the same night's turns on every future one.
        Runs on the cron timer rather than at startup because that tick is the
        one thing guaranteed to come round again.

        Kept as its own method deliberately: folded into the tick handler it
        pushed that method past the window `test_cron_time_label` slices, and
        a handler that grows until an unrelated guard stops seeing its own
        subject is a handler doing too much."""
        try:
            pending = cron_helpers.take_unattended_turns()
        except Exception:
            return
        for kin, scope, count in pending:
            try:
                if kin not in list_agents():
                    continue          # deleted since; nothing to count
                key = (kin, scope)
                self._messages_since_distill[key] = (
                    self._messages_since_distill.get(key, 0) + count)
                self._maybe_auto_distill(kin, scope_key=scope)
            except Exception:
                continue

    def _on_cron_timer_tick(self, event):
        """Drain any cron request files in ~/.hearthkin/cron_requests/.
        Fires every 5 seconds on the main thread. Files are produced by
        hearthkin_cron.py when it detects Hearthkin is running and
        inject-when-running is enabled for the kin.

        Routing: a request for the current active kin gets injected via
        the normal _send_message path (paints to chat, persists, runs
        the worker). A request for a different kin spawns a daemon
        thread that does the isolated-mode equivalent (chat call,
        persist to that kin's conversation.json, journal, telegram) —
        no GUI side effects. Either way the request file is deleted
        before processing so we don't double-fire if a handler crashes."""
        if self._closing:
            return
        self._fold_unattended_cron_turns()
        try:
            pending = cron_helpers.list_pending_request_files()
        except Exception:
            return
        for req_path in pending:
            payload = cron_helpers.read_and_delete_request_file(req_path)
            if not payload:
                continue
            kin = payload.get("kin", "")
            prompt = payload.get("prompt", "")
            entry_index = payload.get("entry_index", 0)
            if not kin or not prompt:
                continue
            # Security: cron_requests/ is a same-user drop channel — any
            # process running as the user can plant a file here that would
            # otherwise drive a real LLM turn (with that kin's tools, memory
            # writes, and exec) on the next tick. Validate the kin name at
            # the path/traversal layer AND require it to be a real, existing
            # kin before routing, so a planted file can neither traverse a
            # path via `kin` nor invoke an unknown/attacker-named persona.
            if validate_kin_name(kin) or kin not in list_agents():
                try:
                    append_failure_log(
                        "cron_errors.log", kin, "reject_request",
                        ValueError("invalid or unknown kin in cron request "
                                   f"file {os.path.basename(str(req_path))}"))
                except Exception:
                    pass
                continue
            # Resolve the entry's time label so the framing carries the
            # scheduled HH:MM (not the consume-time HH:MM, which can drift
            # by up to the 5s poll interval). Falls back to whatever the
            # request payload says if config has shifted underneath us.
            # The firing task's own label wins: it's the only thing that knows
            # WHICH time went off. A multi-time entry has no "time" key to
            # re-derive from (it has "times": [...]), so the fallback below
            # yields "" for one — journaling it as "(no time)" and stripping
            # the time anchor out of the wake-up framing, but only when
            # Hearthkin happened to be open. The config fallback stays for
            # legacy single-time entries and for request files written by an
            # older cron subprocess that predates the payload field.
            time_label = str(payload.get("time_label", "") or "").strip()
            destinations = None
            try:
                cfg = load_agent_config(kin)
                entries = cfg.get("cron_entries") or []
                if 0 <= entry_index < len(entries) and isinstance(entries[entry_index], dict):
                    if not time_label:
                        time_label = str(entries[entry_index].get("time", "") or "").strip()
                    _dests = entries[entry_index].get("destinations")
                    if isinstance(_dests, list) and _dests:
                        destinations = _dests
            except Exception:
                pass
            framed_prompt = cron_helpers.frame_wake_up_prompt(prompt, time_label, kin_name=kin)
            # A park-keeper kin's wake-up IS a park turn: hearthkin_cron's
            # _run_isolated shows it the park + the mechanism before the call,
            # then runs the `> command` it chose afterward. The live-injection
            # path below does neither — it hands over a bare prompt with no park
            # in front of the kin, and never looks for a `>` line after. So a
            # keeper cron landing here would produce a warm description of
            # tending and move nothing, which is the exact failure park_keeper
            # exists to end. Route keepers to the isolated worker for the same
            # reason addressed crons go there: this path structurally can't
            # honor them.
            #
            # Read fresh per tick (cheap; kin_park_mode re-reads config) so
            # flipping a kin to keeper takes effect without a restart.
            park_keeper_turn = False
            try:
                import park_keeper
                park_keeper_turn = park_keeper.kin_park_mode(kin) == "keeper"
            except Exception:
                park_keeper_turn = False
            # A cron addressed to a specific surface (a Telegram DM/group) must
            # deliver there even when its kin is the active one. The active-kin
            # injection path paints to the live desktop chat but only mirrors to
            # DM users — it can't honor an explicit destination. So route
            # addressed crons through the isolated worker (which calls
            # _run_isolated with the destinations); unaddressed crons keep the
            # historic live-injection behavior.
            if (
                kin == self.current_agent
                and not self._streaming
                and not self._room_active
                and not destinations
                and not park_keeper_turn
            ):
                # Parity with the isolated cron path: tell the kin its staging
                # state up front so it knows whether there's anything to tend
                # instead of fishing/gesturing. (The footer + retry only apply
                # on the isolated paths; here the desktop shows tool calls live,
                # so the operator can already see real-vs-gesture.)
                framed_prompt += self._cron_staging_suffix(kin)
                # Journal parity with the isolated path. _run_isolated ends by
                # calling cron_helpers.append_journal with the kin's reply, so
                # a scheduled wake-up becomes that day's journal entry without
                # the kin writing one by hand. This path hands the turn to the
                # ordinary chat flow, which persists conversation.jsonl and
                # nothing else — so whether a tend reached the journal came
                # down to which kin happened to be selected when the task
                # fired, silently, with the tend itself looking fine either
                # way. Same shape as the keeper carve-out above: the live path
                # can't honor it unaided. Stash what append_journal needs;
                # _on_stream_done performs the write once the reply is whole
                # (the reply IS the entry, so there's nothing to write yet).
                self._pending_cron_journal = {
                    "kin": kin,
                    "time_label": time_label or "(no time)",
                    "prompt": prompt,
                }
                self._send_message(framed_prompt)
                continue
            threading.Thread(
                target=self._cron_isolated_worker,
                args=(kin, entry_index, prompt, time_label),
                daemon=True,
            ).start()

        # Proactive heartbeats ride the same 5s tick (throttled internally to
        # ~1 scan/minute). Kept after cron so a busy cron drain runs first.
        self._maybe_fire_heartbeats()

        # Sound cues for work starting, continuing and finishing anywhere —
        # rides the same 5s tick. Guarded separately so a fault in the sound
        # path can never stop cron or heartbeats from running.
        try:
            self._tick_work_sounds()
        except Exception:
            pass

    def _maybe_fire_heartbeats(self):
        """Give heartbeat-enabled kin a quiet chance to reach out on their own.
        Rides the 5s cron tick but only actually scans ~once a minute. For each
        opted-in kin that's due (per its every_minutes) and inside its active-
        hours window, spawns a background worker that runs one heartbeat. The
        kin stays silent unless it chooses to call reach_out — see
        hearthkin_cron.run_heartbeat. Heartbeats only run while Hearthkin is up;
        they never fire when the app is closed."""
        if self._closing:
            return
        import time as _time
        now = _time.time()
        if now - getattr(self, "_heartbeat_last_scan", 0) < 60:
            return
        self._heartbeat_last_scan = now
        # Never fire into a busy machine.
        #
        # A heartbeat is the least urgent thing Hearthkin does — a kin being
        # offered a chance to speak up if it feels like it — and it costs
        # exactly as much as a real turn: a full prompt prefill. On a host with
        # few context slots that prefill evicts the cached context of the
        # conversation someone is actually having, so their next reply goes
        # from seconds to minutes and nothing tells them why. Measured on this
        # install: heartbeats were 24% of all model calls, and background work
        # of all kinds was 65% — the machine spent most of its capacity talking
        # to itself while a person waited.
        #
        # The gate is machine-wide, not per-kin, because the contention is: a
        # heartbeat for one kin evicts the context of a conversation with
        # another just as effectively as one for that kin would.
        #
        # DEFERRED, NOT SKIPPED — `_heartbeat_last` is deliberately left alone,
        # so a kin that was due stays due and goes at the first quiet moment
        # rather than losing its turn entirely.
        #
        # A pending tool approval counts as busy even though the machine is
        # idle while it waits: a kin blocked mid-tool-loop on a human answer is
        # a bad moment for a different kin to pipe up unprompted. That wait is
        # bounded by `approval_timeout_secs`, so this can't wedge.
        try:
            busy = self._work_in_flight()
        except Exception:
            busy = []
        if busy:
            self._log_heartbeat_deferred(busy)
            return
        try:
            names = list_agents()
        except Exception:
            return
        for kin in names:
            try:
                cfg = load_agent_config(kin) or {}
            except Exception:
                continue
            hb = cfg.get("heartbeat") or {}
            if not hb.get("enabled"):
                continue
            try:
                every = max(1, int(hb.get("every_minutes", 120) or 120))
            except (TypeError, ValueError):
                every = 120
            if now - self._heartbeat_last.get(kin, 0) < every * 60:
                continue
            if not self._within_active_hours(hb):
                continue
            self._heartbeat_last[kin] = now
            threading.Thread(
                target=self._heartbeat_worker, args=(kin, cfg), daemon=True,
            ).start()

    def _log_heartbeat_deferred(self, busy):
        """One line to logs/heartbeat.log when a scan stands down.

        Without this the new gate is invisible: heartbeats would simply stop
        happening on a busy install and look like a broken feature rather than
        a working one. The line names what it stood down for, so "why has
        nobody reached out today" has an answer on disk. Throttled to one line
        per minute-scan, which is already the scan rate.
        """
        try:
            import datetime as _dt
            from kin_persistence import LOGS_DIR
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            ts = _dt.datetime.now().isoformat(timespec="seconds")
            with open(LOGS_DIR / "heartbeat.log", "a", encoding="utf-8") as f:
                f.write(f"{ts} [all] deferred=busy ({'; '.join(busy)})\n")
        except Exception:
            pass

    def _within_active_hours(self, hb):
        """True if the current local time is inside [active_start, active_end).
        Handles a window that wraps past midnight (start > end). Missing/invalid
        bounds default to 'always allowed' so a misconfigured window doesn't
        silently disable heartbeats."""
        import datetime as _dt

        def _parse(s, fallback):
            try:
                h, m = str(s).split(":")
                return int(h) * 60 + int(m)
            except Exception:
                return fallback

        start = _parse(hb.get("active_start"), 0)
        end = _parse(hb.get("active_end"), 24 * 60)
        n = _dt.datetime.now()
        now_min = n.hour * 60 + n.minute
        if start == end:
            return True
        if start < end:
            return start <= now_min < end
        return now_min >= start or now_min < end  # window wraps past midnight

    def _heartbeat_worker(self, kin, cfg):
        """Run one heartbeat for a kin off the UI thread. No UI side effects —
        the kin either reaches out (reach_out delivers + records it) or stays
        silent (nothing happens). run_heartbeat logs its own outcome/errors to
        logs/heartbeat.log.

        Registers itself as in-flight for the confirm-on-close check. It didn't,
        originally, and the result was that quitting during a heartbeat closed
        silently — the dialog claimed nothing was happening while a kin was
        part-way through deciding whether to say something. Released in a
        `finally` so a worker that throws can't leave a phantom that nags on
        every future quit.

        Two more guards, both against the same failure: a heartbeat holding
        the model hostage from something that actually matters. One kin's
        heartbeat starts, and another kin's redistill sits waiting for the
        same model for several minutes, with no way to interrupt it short of
        quitting Hearthkin.

        1. Re-checks `_work_in_flight()` here, not just at the once-a-minute
           scan that decided this kin was due. The scan and this worker
           actually running are seconds apart on a busy machine — real work
           (most often a distillation) can start in that gap, and a
           heartbeat is the one thing in this app that should always lose
           that race. Checked BEFORE registering in `_heartbeat_workers`, so
           a heartbeat that bails here never sees its own entry and refuses
           to run against itself.
        2. Hands `should_stop` down into `run_heartbeat` (which forwards it
           to `run_tool_loop`, same mechanism chat streaming already uses).
           Can't kill a single model call already generating — nothing in
           this app can, that's the same limit every should_stop caller
           has — but it stops a multi-iteration heartbeat between
           iterations, and `_signal_heartbeats_to_stop` (called before any
           distillation kicks off) is what actually sets it."""
        try:
            busy = self._work_in_flight()
        except Exception:
            busy = []
        if busy:
            self._log_heartbeat_deferred(busy)
            return
        stop_event = threading.Event()
        try:
            stops = getattr(self, "_heartbeat_stop_events", None)
            if stops is None:
                stops = self._heartbeat_stop_events = {}
            stops[kin] = stop_event
            self._heartbeat_workers.add(kin)
        except Exception:
            pass
        try:
            import hearthkin_cron
            hearthkin_cron.run_heartbeat(kin, cfg, should_stop=stop_event.is_set)
        except Exception as e:
            try:
                cron_helpers.log_cron_error(
                    kin, "heartbeat_" + type(e).__name__, str(e))
            except Exception:
                pass
        finally:
            try:
                self._heartbeat_workers.discard(kin)
            except Exception:
                pass
            try:
                (getattr(self, "_heartbeat_stop_events", None) or {}).pop(kin, None)
            except Exception:
                pass

    def _signal_heartbeats_to_stop(self):
        """Tell every currently-running heartbeat to stand down at its next
        checkpoint. Heartbeats are the least urgent thing this app does —
        call this before starting anything that actually matters (today:
        any distillation) so one that's already running doesn't keep
        contending for the model.

        Can't interrupt a single model call already generating — same limit
        as every should_stop caller in this codebase (see CLAUDE.md, "The
        stop button"). What it DOES do: stops a multi-iteration heartbeat
        (kin replies, calls reach_out, goes back for a follow-up) from
        starting that next iteration, and — combined with the busy
        re-check in _heartbeat_worker — stops a NEW heartbeat from ever
        starting while real work is in progress. Best-effort and swallowed;
        failing to signal a stop must never break the real work waiting on
        it."""
        try:
            for ev in (getattr(self, "_heartbeat_stop_events", None) or {}).values():
                ev.set()
        except Exception:
            pass

    def _cron_isolated_worker(self, kin, entry_index, prompt, req_time_label=""):
        """Run a cron wake-up for a kin that isn't currently the active
        one in the GUI (or where injection isn't possible — streaming,
        room mode, park-keeper mode, an addressed destination, etc).
        Persists to that kin's conversation.json, journals, posts to
        Telegram. No UI side effects; we don't touch self.conversation
        or repaint anything.

        `req_time_label` is the HH:MM off the request file — the label
        the firing task was scheduled with. It wins over _resolve_entry's
        derivation, which reads the legacy single "time" key and so
        returns "" for a multi-time entry (there's no way to tell WHICH
        of its times fired from config alone). Empty falls back to the
        old behavior for legacy request files."""
        time_label = (req_time_label or "").strip() or "(no time)"
        # Register as in-flight for the confirm-on-close check. A cron wake-up
        # is the one kind of work with NO visible signal at all — it produces
        # no UI, so quitting used to abandon it with nobody any the wiser.
        # Registered here rather than at the call site so every spawn path is
        # covered, and released in the finally so a crashing worker can't leave
        # a phantom "still working" entry that blocks quitting forever.
        try:
            self._cron_workers.add((kin, time_label))
        except Exception:
            pass
        try:
            import hearthkin_cron
            cfg = load_agent_config(kin)
            entry, resolved_time_label, _resolved_prompt = hearthkin_cron._resolve_entry(
                cfg, entry_index
            )
            tend_retry = 0
            destinations = None
            if isinstance(entry, dict):
                if not (req_time_label or "").strip():
                    time_label = resolved_time_label or "(no time)"
                try:
                    tend_retry = max(0, int(entry.get("tend_retry", 0) or 0))
                except (TypeError, ValueError):
                    tend_retry = 0
                _dests = entry.get("destinations")
                destinations = (
                    _dests if isinstance(_dests, list) and _dests else None)
            hearthkin_cron._run_isolated(
                kin, cfg, time_label, prompt, tend_retry=tend_retry,
                destinations=destinations)
        except Exception as e:
            cron_helpers.log_cron_error(kin, type(e).__name__, str(e))
            try:
                cron_helpers.append_journal_error_marker(
                    kin, "(deferred)", prompt, f"{type(e).__name__}: {e}"
                )
            except Exception:
                pass
            # Synthesize a marker that _notify_cron_failure can
            # extract the time label from, so the operator gets a
            # Telegram heads-up that this scheduled wake-up died on
            # the isolated path. Same surface as the active-kin
            # watchdog/error notify.
            try:
                synthetic = f"{_CRON_USER_TEXT_MARKER} — fired at {time_label}]"
                self._notify_cron_failure(
                    kin, synthetic, f"{type(e).__name__}: {e}",
                )
            except Exception:
                pass
        finally:
            # Always release, including on the exception path above: a
            # phantom entry here would tell you a cron wake-up is running
            # forever and nag on every quit.
            try:
                self._cron_workers.discard((kin, time_label))
            except Exception:
                pass

    def _request_approval_dialog(self, make_dialog, deny_decision,
                                 unavailable_decision=None):
        """Shared worker-thread approval plumbing for the exec and
        webcam approval dialogs (they were ~40-line copy-paste twins —
        audit L-B19). BLOCKS the calling worker until the operator
        chooses (or shutdown wakes it).

        `make_dialog` is a zero-arg callable, run on the main thread,
        that constructs and returns the approval dialog (which must
        expose a `.decision` attribute after ShowModal). `deny_decision`
        is the default returned on shutdown / dialog failure.

        Threading model: marshals the dialog show onto the wxPython main
        thread via wx.CallAfter; blocks the worker thread on a
        threading.Event until the main thread sets it. The event is
        also held in self._pending_approvals so _on_close can wake every
        pending approval with a "deny" decision instead of leaving
        worker threads hung when the user closes Hearthkin mid-dialog.
        The try/finally guarantees the event is set even when the
        dialog callback raises — otherwise the worker hangs forever.

        `unavailable_decision` (default: same as `deny_decision`) is what
        comes back when we could NOT ask — shutdown in progress, or the
        dialog failed to build. That is not a refusal, and callers that can
        tell the difference should pass a distinct value so the kin isn't
        told a human said no when no human was ever shown anything. Callers
        that can't (webcam) keep the old behavior by omitting it."""
        if unavailable_decision is None:
            unavailable_decision = deny_decision
        if self._closing:
            return unavailable_decision

        decision_event = threading.Event()
        # Starts as "never got an answer" and is overwritten ONLY by a real
        # ShowModal result — so a dialog that fails to construct reports
        # unavailable rather than impersonating a denial.
        decision_holder = {"decision": unavailable_decision}
        self._pending_approvals.append(decision_event)
        # Re-check after appending: shutdown's wakeup pass could have
        # run between the pre-check and this append, leaving the event
        # uninvolved in wakeup and the worker blocked forever (audit
        # H7).
        if self._closing:
            try:
                self._pending_approvals.remove(decision_event)
            except ValueError:
                pass
            return unavailable_decision

        def _show_on_main():
            try:
                if self._closing:
                    return
                # Audible cue as the modal appears — covers every wx-dialog
                # approval (desktop exec, Discord exec, Telegram webcam). The
                # Telegram-exec path has no dialog and plays its own alert in
                # _notify_remote_approval, so there's no double-fire.
                try:
                    self._play_approval_alert()
                except Exception:
                    pass
                # COME AND GET THE PERSON. An approval blocks a kin mid-turn
                # and expires on a timer, so it is the one thing in Hearthkin
                # that must not wait politely behind whatever else is on
                # screen. It used to: the dialog opened inside the app, which
                # might be behind a browser or hidden in the tray, and the
                # only other signals were a toast lost among every other
                # app's and NVDA speech that never gets a gap to land in when
                # character echo is on. A kin sat blocked for HOURS and timed
                # out because nobody knew it had asked.
                #
                # Taking focus is deliberately rude, and correct here: a
                # window arriving in front is a focus event, which a screen
                # reader announces immediately rather than queueing. It is
                # also the only signal that survives being in another app.
                try:
                    self.bring_to_front()
                except Exception:
                    pass
                dlg = make_dialog()
                # Foreground the DIALOG too, not just the frame: restoring the
                # main window is no use if the thing needing an answer is
                # behind it. Guarded — a failure here must never stop the
                # dialog being shown, or the kin waits forever for an answer
                # nobody was asked for.
                try:
                    _force_foreground(dlg.GetHandle())
                except Exception:
                    pass
                dlg.ShowModal()
                decision_holder["decision"] = dlg.decision
                dlg.Destroy()
            finally:
                decision_event.set()

        wx.CallAfter(_show_on_main)
        decision_event.wait()

        try:
            self._pending_approvals.remove(decision_event)
        except ValueError:
            pass
        return decision_holder["decision"]

    def _request_webcam_approval(self, kin_name, requester_label, requester_id):
        """Pop the webcam-approval dialog on the main thread and block
        the calling worker until the operator chooses. Returns "allow",
        "deny", or "unavailable" (no "remember" — webcam permission is
        configured once per user in Settings, not per-call).

        "unavailable" means we could NOT ask — shutdown in progress, or the
        dialog failed to build. Formerly this returned "deny" for those
        cases and the Telegram wrap told the user the operator had refused
        the capture, which was untrue: nobody was ever shown the request."""
        return self._request_approval_dialog(
            lambda: WebcamApprovalDialog(
                self, kin_name, requester_label, requester_id,
            ),
            WebcamApprovalDialog.DECISION_DENY,
            unavailable_decision="unavailable",
        )

    def _request_exec_approval(self, kin_name, command, reason):
        """Show the exec-approval dialog from a worker thread and return
        the user's decision. BLOCKS the worker until the user picks.

        Returns one of: "allow", "remember", "deny", "unavailable". If the
        frame is already closing (or the dialog can't be built) the answer
        is "unavailable", NOT "deny" — nobody was asked, so nobody
        refused, and the kin is told exactly that."""
        return self._request_approval_dialog(
            lambda: ExecApprovalDialog(self, kin_name, command, reason),
            ExecApprovalDialog.DECISION_DENY,
            unavailable_decision="unavailable",
        )
