"""LifecycleMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    APP_NAME, CONFIG_FILE, ConfirmCloseDialog, ParkPlayDialog, SearchDialog, __version__,
    _force_foreground,
    _num_ctx_of, append_agent_conversation_turn, append_failure_log, atomic_write_json,
    build_system_prompt, cron_helpers, extract_inline_thinking, list_agents, list_rooms,
    llm_backend, load_memory, load_soul, now_iso, nvda_speak, resolve_kin_ollama_host,
    save_room_conversation, strip_model_annotation, strip_self_timestamp, threading, tray,
    urllib, wx,
)


class LifecycleMixin:

    def _on_whats_busy(self, event=None):
        """Say what the app is doing right now — Chat → What's it busy with?

        `_work_in_flight` has always known, and phrases it in sentences. It was
        only ever ASKED by the quit prompt, so the way to find out whether a
        kin was waiting behind a long distillation was to begin quitting and
        read the warning. That is a bad way to check something worth checking
        often, and a worse one when a 16-minute memory write and a reply are
        competing for the same model and neither says so.

        Spoken as well as shown: the status bar is a place you have to go and
        look, and the question "what is it doing?" is asked precisely when
        nothing appears to be happening.

        Never raises. A fault here must not cost anyone the keystroke — the
        honest answer to a broken probe is "I can't tell", not a traceback.
        """
        try:
            busy = self._work_in_flight() or []
        except Exception:
            busy = None

        if busy is None:
            message = "I can't tell what's running just now."
        elif not busy:
            message = "Nothing is running. It's idle."
        elif len(busy) == 1:
            message = busy[0] + "."
        else:
            # "A, B, and C." — read aloud, so joined as speech rather than as
            # a list someone is expected to scan.
            message = ", ".join(busy[:-1]) + f", and {busy[-1]}."

        try:
            self.SetStatusText(message)
        except Exception:
            pass
        try:
            nvda_speak(message)
        except Exception:
            pass

    def _machine_busy(self):
        """Is a model call actually running right now? Narrower than
        `_work_in_flight`, which also counts things WAITING ON A HUMAN.

        The two questions differ. For "may I quit?", an approval you haven't
        answered is absolutely something to warn about. For "should I play the
        still-working sound?", it isn't work at all — the machine is idle and
        ticking at someone once a minute about their own unanswered prompt is
        nagging, not information.
        """
        return [line for line in self._work_in_flight()
                if "waiting on your answer" not in line]

    def _work_in_flight(self):
        """Human-readable lines for everything a quit would abandon.

        Empty list means quitting costs nothing, which is the common case and
        the one that must stay silent — a confirmation on every close trains
        people to dismiss it unread, and then it isn't a safeguard.

        Every probe is individually guarded and the whole thing is wrapped by
        its caller. A fault here must never be able to prevent quitting: the
        worst outcome of a bug in this method is a missing warning, never an
        app that won't close.

        Dictation is deliberately NOT listed, and this is the note saying so
        rather than an omission. A transcription is a second or two of work
        the person started by pressing a button and is sitting in front of;
        warning about it would put a dialog in front of someone during a
        window they can already see the whole of, which is the "trains people
        to dismiss it unread" failure above. The microphone itself IS closed
        on the way out, in VoiceEngine.shutdown, so quitting mid-sentence
        cannot leave an input stream holding the device.
        """
        busy = []
        try:
            if self._streaming and self.current_agent:
                busy.append(
                    f"{self.current_agent} is part-way through a reply "
                    f"in the main window")
        except Exception:
            pass
        try:
            if self._room_active:
                room = self.current_room or "a room"
                busy.append(f'the room "{room}" is part-way through a round')
        except Exception:
            pass
        try:
            for kin in sorted(self._distilling or {}):
                busy.append(f"{kin} is saving notes to its memory")
        except Exception:
            pass
        try:
            for kin, when in sorted(self._cron_workers or set()):
                busy.append(f"{kin} is answering its scheduled wake-up from {when}")
        except Exception:
            pass
        # A cron wake-up can also run in the standalone hearthkin_cron process,
        # which shares no state with us — it reports itself through a marker
        # file instead. Without this, quitting during a scheduled wake-up closed
        # silently and abandoned it, because from in here nothing was happening.
        try:
            for kin, when in sorted(cron_helpers.cron_running_kin()):
                label = f" from {when}" if when else ""
                busy.append(f"{kin} is answering its scheduled wake-up{label}")
        except Exception:
            pass
        # Proactive heartbeats run on their own threads and registered nothing
        # at all, so a kin part-way through deciding whether to reach out was
        # invisible too. Same omission, different mechanism.
        try:
            for kin in sorted(self._heartbeat_workers or set()):
                busy.append(f"{kin} is deciding whether to reach out")
        except Exception:
            pass
        # A park turn is several moves in a SHARED save, on its own thread.
        # Quitting through one abandons a kin mid-visit — and unlike a reply,
        # what it was part-way through changes a world other tenants read.
        try:
            for kin in sorted(self._park_workers or set()):
                busy.append(f"{kin} is part-way through its park turn")
        except Exception:
            pass
        # Remote surfaces: the whole reason this dialog exists. There is no
        # local signal for a kin mid-reply to someone on Telegram or Discord.
        # Discord was missing here for as long as the surface existed, so
        # quitting through a reply on it abandoned somebody's conversation in
        # silence — the exact failure this whole check was built to stop.
        # One try PER REGISTRY, deliberately. Sharing a single one means a
        # fault reading either registry silently costs the report for BOTH —
        # which is how a missing warning becomes a missing warning about work
        # that had nothing to do with the fault.
        for _registry_name in ("bots", "discord_bots"):
            try:
                registry = getattr(self, _registry_name, None) or {}
                for bot in list(registry.values()):
                    try:
                        line = bot.active_turn_label()
                    except Exception:
                        line = None
                    if line:
                        busy.append(line)
            except Exception:
                pass
        try:
            waiting = len(self._pending_approvals or [])
            if waiting:
                busy.append(
                    f"{waiting} tool approval"
                    f"{'s' if waiting != 1 else ''} waiting on your answer")
        except Exception:
            pass
        return busy

    def _confirm_close_while_busy(self, event):
        """True when the close should proceed. False means the user chose to
        wait and the event has been vetoed.

        Fails OPEN in every direction: no work in flight, an unvetoable close
        (system shutdown, installer Restart Manager, anything that has already
        decided), or any exception at all, and the close proceeds. A blocked
        quit is a worse bug than a missing prompt — see the "Ctrl+Q does
        nothing" hazard the teardown comments below are all shaped around.
        """
        try:
            if not event.CanVeto():
                return True
            busy = self._work_in_flight()
            if not busy:
                return True
            dlg = ConfirmCloseDialog(self, busy)
            try:
                answer = dlg.ShowModal()
            finally:
                dlg.Destroy()
            if answer == wx.ID_OK:
                return True
            event.Veto()
            return False
        except Exception as e:
            try:
                append_failure_log(
                    "dialog_failures.log", self.current_agent or "?",
                    "confirm_close_while_busy", e,
                )
            except Exception:
                pass
            return True

    def _on_close(self, event):
        # Close-to-tray fast path. When the setting is on AND this
        # isn't a user-requested full exit (exit_from_tray sets
        # _quitting), the close goes into background-mode instead of
        # tearing the app down. Workers, bots, cron timer, distillation
        # cadence — all stay running.
        #
        # Two background-mode shapes, depending on whether the tray
        # icon is actually visible:
        #   - tray icon visible → Hide() the window; user pulls it
        #     back via tray left-click or tray menu.
        #   - tray icon NOT visible (init failed, platform without
        #     a notification area, etc.) → Iconize() to the taskbar
        #     so the user can recover via Alt-Tab. Hiding into nothing
        #     would orphan the process.
        #
        # Either way: announce via NVDA + Windows toast that the app
        # is still alive but no longer in the foreground. Without the
        # announcement, screen-reader users can't tell "minimized"
        # apart from "exited" — the tray icon is silent visual signal
        # only.
        close_to_tray = bool(self.config.get("close_to_tray", True))
        # The IsShown() check is the fix for a real installer bug: when
        # Hearthkin is minimized to tray (window hidden) and the Inno
        # Setup installer's Restart Manager asks Hearthkin.exe to close
        # so it can be replaced, RM sends WM_CLOSE to the hidden window.
        # wxPython surfaces that as a vetoable EVT_CLOSE — and without
        # the IsShown() guard, the handler below would re-hide the
        # (already-hidden) window and veto, leaving the process alive
        # forever. Installer times out, user sees "Hearthkin is running,
        # cannot continue" even though the window has been gone for
        # ages. Heuristic: if the window isn't visible, nobody just
        # clicked X on it — the close request is coming from outside
        # (RM, system shutdown, taskkill that politely asked first),
        # and we should honor it as a real exit.
        if (close_to_tray
                and not self._quitting
                and event.CanVeto()
                and self.IsShown()):
            tray_alive = (
                self._tray_icon is not None
                and getattr(self._tray_icon, "icon_visible", False)
            )
            try:
                if tray_alive:
                    self.Hide()
                    self._announce_minimized_to_tray()
                else:
                    self.Iconize(True)
                    self._announce_minimized_to_taskbar()
            except Exception:
                pass
            event.Veto()
            return

        # Ask before tearing anything down, if something is still working.
        # This MUST come before the teardown below — the point is that
        # answering "wait" leaves Hearthkin exactly as it was, so it has to
        # happen while that is still true. One line later and the bots are
        # already stopped and the model call already abandoned, and "wait"
        # would be a lie.
        if not self._confirm_close_while_busy(event):
            return

        # Mark closing FIRST and wake any worker thread blocked on an
        # exec-approval dialog. Without this, an in-flight exec call
        # whose approval dialog hasn't been answered yet would leave its
        # worker thread waiting on the Event forever, and Hearthkin's
        # process would refuse to exit until the OS killed it.
        self._closing = True
        for ev in list(self._pending_approvals):
            try:
                ev.set()
            except Exception:
                pass
        if self._streaming:
            self._stream_id += 1
            self._streaming = False
        if self.current_room is not None:
            try:
                save_room_conversation(self.current_room, self.room_conversation)
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_room or "?",
                    "save_room_conversation (app close)",
                    e,
                )
        # Distillation on close (current kin only — we're shutting down so no time to wait
        # for background workers; trigger but let the daemon thread do best-effort).
        # MUST be guarded: this is the close path, and an unhandled exception here
        # escapes _on_close before event.Skip() below, so the frame never gets
        # destroyed and the app refuses to quit (a "Ctrl+Q does nothing" hang). Every
        # other teardown step in this handler is wrapped for the same reason; this one
        # was the lone gap. Logging it also surfaces a failure that pythonw otherwise
        # swallows to the void.
        if self.current_agent:
            try:
                self._maybe_distill_on_close(self.current_agent)
            except Exception as e:
                append_failure_log(
                    "save_failures.log", self.current_agent or "?",
                    "distill_on_close (app close)", e,
                )
        if self._auto_timer is not None:
            try:
                self._auto_timer.Stop()
            except Exception:
                pass
        # Stop the status-bar revert timer so a pending CallLater can't
        # fire against destroyed widgets after shutdown (audit H19).
        pending_status = getattr(self, "_status_revert_timer", None)
        if pending_status is not None:
            try:
                pending_status.Stop()
            except Exception:
                pass
        for bot in list(self.bots.values()):
            try:
                bot.stop()
            except Exception:
                pass
        for bot in list(self.discord_bots.values()):
            try:
                bot.stop()
            except Exception:
                pass
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        # Stop the cron poll timer + drop the running-lock so the next
        # cron fire knows we're gone and goes isolated-mode. Both are
        # best-effort; a left-behind lock would just get reaped on the
        # next Hearthkin start via recover_stale_lock().
        if self._cron_timer is not None:
            try:
                self._cron_timer.Stop()
            except Exception:
                pass
        try:
            cron_helpers.delete_lock()
        except Exception:
            pass
        # Tear down the tray icon, mini-chat window, and any open
        # menu-triggered dialogs (Preferences / Usage). Doing this
        # before event.Skip() means the wxPython main loop sees no
        # leftover top-level windows and can exit cleanly.
        for attr in ("_prefs_dialog", "_usage_dialog"):
            dlg = getattr(self, attr, None)
            if dlg is not None:
                try:
                    dlg.Destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._mini_chat is not None:
            try:
                self._mini_chat.destroy_for_real()
            except Exception:
                pass
            self._mini_chat = None
        if getattr(self, "_voice_engine", None) is not None:
            try:
                self._voice_engine.shutdown()
            except Exception:
                pass
            self._voice_engine = None
        if self._tray_icon is not None:
            try:
                self._tray_icon.RemoveIcon()
                self._tray_icon.Destroy()
            except Exception:
                pass
            self._tray_icon = None
        # Guarantee the main loop actually ends. Reaching here is always a
        # real exit — the close-to-tray path returned early above — so the
        # process MUST go down now. A stray top-level window (a mini-chat
        # that only Hides on its own close, a leftover dialog) or the
        # TaskBarIcon's hidden helper window can otherwise keep wx's main
        # loop alive after this frame is destroyed: the window vanishes but
        # the process lingers (the intermittent "Ctrl+Q didn't quit"
        # zombie). That zombie keeps holding the single-instance lock, so
        # the NEXT launch silently bails thinking an instance is still up —
        # which is why a fresh install "doesn't show up." Destroy every
        # other top-level window, then force the loop to exit rather than
        # relying on wx's exit-when-last-window-closes heuristic.
        try:
            for _tlw in list(wx.GetTopLevelWindows()):
                if _tlw is not self:
                    try:
                        _tlw.Destroy()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            _app = wx.GetApp()
            if _app is not None:
                _app.ExitMainLoop()
        except Exception:
            pass
        event.Skip()

    def _on_exit(self, event):
        # File → Exit menu item. Force a real exit even if close-to-
        # tray is enabled — selecting Exit explicitly should not put
        # the app into the tray.
        self._quitting = True
        self.Close()

    def bring_to_front(self):
        """Show, un-minimize, and reliably foreground the main window.
        Called from the tray icon's restore path. Uses _force_foreground
        for the Win32 foreground handshake so the window actually comes
        forward even when Windows' foreground-lock would silently reject a
        bare Raise() — the difference between a screen-reader user landing
        in the window vs. hearing nothing and assuming it didn't open."""
        try:
            if not self.IsShown():
                self.Show()
            if self.IsIconized():
                self.Iconize(False)
            self.Raise()
        except Exception:
            pass
        try:
            _force_foreground(self.GetHandle())
        except Exception:
            pass
        try:
            if getattr(self, "input_box", None):
                self.input_box.SetFocus()
        except Exception:
            pass

    def exit_from_tray(self):
        """Called by the tray icon's Exit menu entry. Sets the
        'real exit' flag so _on_close doesn't bounce us back into
        the tray, then triggers a normal close."""
        self._quitting = True
        try:
            if not self.IsShown():
                # Briefly show + hide so wx's main loop has a top-
                # level window to dispatch the close through.
                self.Show()
        except Exception:
            pass
        self.Close()

    def _announce_minimized_to_tray(self):
        """Speak via NVDA + show a Windows toast when close-to-tray
        hides the main window. Without this, a screen-reader user
        gets zero feedback that the app is still running — the tray
        icon is silent. NVDA and the toast are layered (toasts
        respect Focus-Assist / silent-hours; speech bypasses).

        Note on Win+B: it still opens the tray on Windows 11, but
        Win 11 hides most icons under a "show hidden icons" flyout
        (the chevron arrow on the taskbar) by default — so Win+B
        focuses the chevron, not the icons. Telling users to "Press
        Win+B then Tab to Hearthkin" is wrong on stock Win 11.
        Wording below is version-agnostic: just "find it in the
        system tray" without prescribing a keystroke that may not
        do what we say."""
        msg_short = (
            "Hearthkin is in the system tray. "
            "Open it from there to bring the window back."
        )
        try:
            nvda_speak(msg_short)
        except Exception:
            pass
        try:
            notif = wx.adv.NotificationMessage(
                title=APP_NAME,
                message=(
                    "Minimized to the system tray. Find the Hearthkin "
                    "icon in the taskbar's notification area "
                    "(on Windows 11, click the chevron / 'show hidden "
                    "icons' arrow first). Click it to bring the window "
                    "back, or right-click for the menu including Exit."
                ),
                parent=None,
            )
            notif.Show(timeout=6)
        except Exception:
            pass

    def _check_ollama_on_startup(self):
        """Background-thread probe of the configured Ollama host's
        /api/tags endpoint. If it responds, Ollama is up and we say
        nothing. If it doesn't, we post the advisory dialog to the UI
        thread via wx.CallAfter.

        Short timeout (5s — matches the Test Ollama host button) so a
        sleepy network stack doesn't keep this thread alive past the
        user's normal startup window, while still tolerating the Wi-Fi
        round-trip to a remote daemon on another machine. We do not
        retry — startup is exactly the moment to mention it; later
        prompts would feel intrusive."""
        ok = False
        try:
            from llm_backend import _resolve_ollama_host
            host = _resolve_ollama_host()
        except Exception:
            host = "http://localhost:11434"
        try:
            req = urllib.request.Request(host + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                ok = (200 <= resp.status < 300)
        except Exception:
            ok = False
        if not ok:
            wx.CallAfter(self._show_ollama_missing_dialog, host)

    def _show_ollama_missing_dialog(self, host="http://localhost:11434"):
        """Friendly first-run advisory when Ollama isn't responding.
        Two action paths: open the Ollama download page, or dismiss.
        Optional 'don't show again' checkbox sets a config flag so
        the probe stops nagging users who only use OpenRouter or who
        intentionally launch Hearthkin without Ollama.

        `host` is the URL we actually probed (default localhost) so
        the message can name the real address — important when the
        user has Hearthkin pointed at a remote daemon and the failure
        might be 'remote unreachable' rather than 'no Ollama
        installed.'"""
        if self._closing:
            return
        # Belt-and-suspenders: if the user dismissed between probe-
        # fire and CallAfter delivery (unlikely but possible), skip.
        if self.config.get("ollama_warning_dismissed", False):
            return
        title = "Ollama not detected"
        is_remote = "localhost" not in host and "127.0.0.1" not in host
        if is_remote:
            msg = (
                f"Hearthkin couldn't reach Ollama at {host}.\n\n"
                "This kin is pointed at a remote Ollama machine. That "
                "machine may be off, asleep, or unreachable on the "
                "network; the Ollama service there may not be running; "
                "or the saved address may be wrong.\n\n"
                "You can still use kin routed through OpenRouter if you "
                "have an API key set up (File → Preferences → "
                "Connections).\n\n"
                "To check or change the machine: click \"Kin settings…\" "
                "next to the kin dropdown, go to the Model tab, click "
                "\"Change model…\", then \"Manage machines…\" — that "
                "dialog has a \"Test connection\" button."
            )
        else:
            msg = (
                f"Hearthkin couldn't reach Ollama at {host}.\n\n"
                "Ollama runs the local language models Hearthkin talks to "
                "by default. Without it, kin using local models won't be "
                "able to reply — though you can still use kin routed "
                "through OpenRouter if you have an API key set up under "
                "File → Preferences → Connections.\n\n"
                "Open the download page to grab Ollama from ollama.ai. "
                "After installing, open a Command Prompt and pull at "
                "least one model — for example:\n\n"
                "    ollama pull qwen2.5:7b-instruct"
            )
        try:
            dlg = wx.RichMessageDialog(
                self, msg, title,
                style=wx.OK | wx.CANCEL | wx.ICON_INFORMATION,
            )
            # The action button differs by cause. For a local miss,
            # "download Ollama" is the fix. For an unreachable remote
            # machine it is not — installing Ollama here does nothing
            # for a Mac that's asleep — so the button goes where the
            # machine is actually configured: the kin's Settings.
            dlg.SetOKLabel(
                "&Open kin settings" if is_remote else "&Open download page")
            dlg.SetCancelLabel("&Not now")
            dlg.ShowCheckBox(
                "&Don't show this again "
                "(I'm using OpenRouter only or know what I'm doing)"
            )
            result = dlg.ShowModal()
            if dlg.IsCheckBoxChecked():
                self.config["ollama_warning_dismissed"] = True
                try:
                    atomic_write_json(CONFIG_FILE, self.config)
                except Exception:
                    pass
            if result == wx.ID_OK:
                if is_remote:
                    # CallAfter so this modal is fully gone before the
                    # Settings dialog opens on top of it.
                    try:
                        wx.CallAfter(self._on_edit_kin, None)
                    except Exception:
                        pass
                else:
                    try:
                        import webbrowser
                        webbrowser.open("https://ollama.ai/download")
                    except Exception:
                        pass
            dlg.Destroy()
        except Exception:
            # Last-resort fallback — any wx.RichMessageDialog quirk
            # shouldn't cost the user the warning entirely.
            try:
                wx.MessageBox(
                    msg, title, wx.OK | wx.ICON_INFORMATION, parent=self,
                )
            except Exception:
                pass

    def _announce_minimized_to_taskbar(self):
        """Same idea as the tray version, but for the case where the
        tray icon never came up (init failed / platform unsupported).
        We minimized to the taskbar instead of hiding entirely; tell
        the user so they don't think the app exited."""
        msg = (
            "Hearthkin is minimized to the taskbar. Use Alt-Tab to find it."
        )
        try:
            nvda_speak(msg)
        except Exception:
            pass
        try:
            notif = wx.adv.NotificationMessage(
                title=APP_NAME,
                message=(
                    "Minimized to the taskbar (the system tray icon "
                    "isn't available). Use Alt+Tab to bring Hearthkin back."
                ),
                parent=None,
            )
            notif.Show(timeout=6)
        except Exception:
            pass

    def open_mini_chat(self):
        """Lazily build (or re-show) the always-on-top quick-chat
        window and refresh it from the current kin's recent turns.
        Called by the tray menu's 'Mini chat…' entry."""
        if self._mini_chat is None:
            self._mini_chat = tray.MiniChatFrame(self)
        try:
            self._mini_chat.populate()
            self._mini_chat.Show()
            self._mini_chat.Raise()
            self._mini_chat.SetFocus()
        except Exception:
            pass

    def open_preferences_dialog(self):
        """Open the Preferences dialog. Used to be a notebook tab; now
        a menu-triggered dialog (Tools → Preferences, Ctrl+,, or the
        tray icon's Preferences entry). Lazily built the first time
        it's opened, then re-shown — preserves all the widget
        references stored on `self` from `_build_prefs_tab`.

        Exceptions during build or show used to silently disappear
        into stderr under pythonw.exe — leaving the user with a
        Preferences menu item that does nothing and no diagnostic.
        Logged to dialog_failures.log + surfaced via status bar +
        wx.MessageBox so the failure is visible. (Don't remove the
        logging — the silent-fail mode is too easy to regress into.)
        """
        try:
            if self._prefs_dialog is None:
                self._prefs_dialog = self._build_dialog_from_tab_builder(
                    "Preferences", self._build_prefs_tab, (700, 640),
                )
            self._show_dialog(self._prefs_dialog)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            # Use the existing failure-log path. The {exc!r} formatting
            # in append_failure_log gives a short one-liner; we also
            # write the full traceback to the same file on the next
            # line so a grep on dialog_failures.log shows context.
            append_failure_log("dialog_failures.log",
                               "open_preferences_dialog", "build_or_show", e)
            try:
                from kin_persistence import LOGS_DIR
                with open(LOGS_DIR / "dialog_failures.log", "a", encoding="utf-8") as f:
                    f.write(tb + "\n")
            except Exception:
                pass
            try:
                self._set_status(f"Preferences failed to open: {e}")
            except Exception:
                pass
            wx.MessageBox(
                f"Couldn't open Preferences: {e}\n\n"
                f"Full traceback logged to ~/.hearthkin/logs/dialog_failures.log",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )
            # Don't cache a half-built dialog — next open should retry.
            self._prefs_dialog = None

    def open_usage_dialog(self):
        """Open the Usage stats dialog. Same lazy-build pattern as
        Preferences. Refreshes the display each time it's shown so
        the breakdown reflects the current conversation state, not
        the state at first open.

        Exceptions during build / show are surfaced the same way as
        open_preferences_dialog (logged + status + MessageBox) so a
        silent-fail can't hide the menu item from doing nothing.
        """
        try:
            if self._usage_dialog is None:
                self._usage_dialog = self._build_dialog_from_tab_builder(
                    "Usage stats", self._build_usage_tab, (640, 500),
                )
            try:
                self._refresh_usage_display()
            except Exception:
                pass
            self._show_dialog(self._usage_dialog)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            append_failure_log("dialog_failures.log",
                               "open_usage_dialog", "build_or_show", e)
            try:
                from kin_persistence import LOGS_DIR
                with open(LOGS_DIR / "dialog_failures.log", "a", encoding="utf-8") as f:
                    f.write(tb + "\n")
            except Exception:
                pass
            try:
                self._set_status(f"Usage stats failed to open: {e}")
            except Exception:
                pass
            wx.MessageBox(
                f"Couldn't open Usage stats: {e}\n\n"
                f"Full traceback logged to ~/.hearthkin/logs/dialog_failures.log",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )
            self._usage_dialog = None

    def _build_dialog_from_tab_builder(self, title, builder, default_size):
        """Wrap a panel-builder (originally a notebook-tab builder) as
        a resizable dialog. The builder calls panel.SetSizer() inside,
        and stores widget references on `self` (the frame) — those
        references stay valid as long as the dialog persists, which
        it does for the lifetime of the frame.

        Hide-on-close, not destroy-on-close, so the next open is
        instant and the widget references don't go stale."""
        dlg = wx.Dialog(
            self, title=title, size=default_size,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(dlg)
        builder(panel)  # builder calls panel.SetSizer internally
        outer_sizer.Add(panel, proportion=1, flag=wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        close_btn = wx.Button(dlg, wx.ID_CLOSE, label="&Close")
        close_btn.Bind(wx.EVT_BUTTON, lambda _e: dlg.Hide())
        close_btn.SetDefault()
        btn_row.Add(close_btn)
        outer_sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)

        dlg.SetSizer(outer_sizer)
        # Escape closes the dialog. wxPython auto-maps Escape to
        # wx.ID_CANCEL by default; we use wx.ID_CLOSE so we tell it
        # explicitly. Same pattern as EditKinDialog.
        dlg.SetEscapeId(wx.ID_CLOSE)
        # Hide instead of destroy on the window's X. Re-shown on
        # next open with state intact.
        #
        # Exception: if the app is shutting down (Inno installer's
        # Restart Manager, Windows logoff/reboot), the wxApp's default
        # OnQueryEndSession walks every TLW and Close()s each — if any
        # of them Veto here, the whole shutdown is vetoed and the
        # installer hangs. Skip the Hide+Veto when _quitting is set.
        def _on_dlg_close(event):
            if self._quitting:
                event.Skip()
                return
            dlg.Hide()
            if event.CanVeto():
                event.Veto()
        dlg.Bind(wx.EVT_CLOSE, _on_dlg_close)
        return dlg

    def _show_dialog(self, dlg):
        """Show + raise + focus a dialog. Centralized so the show
        sequence stays consistent across Preferences and Usage and
        any future menu-triggered dialogs."""
        try:
            dlg.Show()
            dlg.Raise()
            dlg.SetFocus()
        except Exception:
            pass

    def show_about_dialog(self):
        """Called by the tray menu's About entry. Plain wx.AboutBox
        with the project metadata."""
        info = wx.adv.AboutDialogInfo()
        info.SetName(APP_NAME)
        info.SetVersion(__version__)
        info.SetDescription(
            "Multi-kin local-LLM chat for Windows. Accessibility-first.\n\n"
            "Each kin is a configured persona with a soul prompt, a "
            "kin-curated memory index plus depth logs, and a chosen "
            "model. Default backend is Ollama (local); per-kin "
            "OpenRouter routing is supported.\n\n"
            "Bundled third-party software:\n"
            "  • NVDA Controller Client (LGPL 2.1, unmodified) — © NV Access\n"
            "    Source: https://github.com/nvaccess/nvda\n"
            "    See licenses\\NVDA-ControllerClient-LGPL-2.1.txt and\n"
            "    licenses\\NVDA-ControllerClient-NOTICE.md."
        )
        info.SetCopyright("Hearthkin: CC0 1.0 Universal — public domain.")
        info.SetWebSite("https://github.com/glasswings-lang/hearthkin")
        try:
            wx.adv.AboutBox(info, parent=self)
        except Exception:
            wx.MessageBox(
                f"{APP_NAME} {__version__}\n\n"
                "Multi-kin local-LLM chat for Windows.\n"
                "https://github.com/glasswings-lang/hearthkin\n\n"
                "Includes the NVDA Controller Client (LGPL 2.1, unmodified)\n"
                "by NV Access — https://github.com/nvaccess/nvda",
                f"About {APP_NAME}",
                wx.OK | wx.ICON_INFORMATION,
            )

    def send_from_mini_chat(self, text, mini_chat_frame):
        """Quick-chat send path. Bypasses the streaming/chunked main
        chat plumbing — appends a user turn, fires a blocking chat
        call on a worker thread, and routes the reply back to the
        mini chat (and into the kin's persistent conversation) when
        it lands. The main chat tab repaints from the canonical
        conversation when the user next looks at it."""
        text = (text or "").strip()
        if not text:
            mini_chat_frame._enable_input()
            return
        if not self.current_agent:
            mini_chat_frame._append_line("(error)", "No kin loaded.")
            mini_chat_frame._enable_input()
            return

        # Echo into the canonical conversation immediately so the main
        # window (if reopened mid-call) shows the user's message. `ts`
        # matches every other surface's user-turn shape — without it
        # the timestamp-grounding prefix never applied to mini-chat
        # turns (audit M-F8).
        self.conversation.append({"role": "user", "content": text, "ts": now_iso()})
        try:
            self._persist_current_conversation()
        except Exception:
            pass
        try:
            self._render_conversation()
        except Exception:
            pass

        # Snapshot everything the worker needs ON THE MAIN THREAD —
        # the kin, its cfg, and a copy of the messages list. The worker
        # used to read self.current_agent / self.agent_cfg /
        # self.conversation live from its own thread, racing a kin
        # switch mid-flight (audit M-F8).
        kin = self.current_agent
        cfg_snapshot = dict(self.agent_cfg or {})
        convo_snapshot = list(self.conversation)

        threading.Thread(
            target=self._mini_chat_worker,
            args=(text, mini_chat_frame, kin, cfg_snapshot, convo_snapshot),
            daemon=True,
        ).start()

    def _mini_chat_worker(self, user_text, mini_chat_frame, kin, cfg, convo_snapshot):
        """Background thread for the mini-chat send. Builds messages
        the same way _send_message does (system prompt + compacted
        tool-history + normalized turns) from the main-thread snapshots
        passed in, calls llm_backend.chat blocking, and posts the reply
        back via wx.CallAfter. The finish handler re-checks that `kin`
        is still the active kin — on a mid-flight switch the reply is
        persisted straight to the captured kin's conversation.jsonl
        instead of being appended under the wrong kin (audit M-F8)."""
        reply = ""
        thinking = ""
        try:
            soul = (load_soul(kin) or "").strip()
            try:
                memory = load_memory(kin) or ""
            except Exception:
                memory = ""
            # Mini-chat is a tool-less send path (no run_tool_loop, no
            # tools= on the chat call below), so fence the base prompt to
            # the empty set — the popup gets soul + remembered context,
            # not tending instructions it can't act on here.
            sys_content = build_system_prompt(soul, memory, enabled_tools=[],
                                              kin_name=kin)

            keep_window = int(cfg.get("tool_history_keep", 5) or 0)
            compacted = self._compact_tool_history(convo_snapshot, keep_window)
            history = []
            for _m in compacted:
                _entry = self._history_entry_for_model(_m)
                if _entry is not None:
                    history.append(_entry)
            messages = []
            if sys_content:
                messages.append({"role": "system", "content": sys_content})
            messages.extend(history)
            # The user's turn is already in the snapshot (appended by
            # send_from_mini_chat before this worker fired), so the
            # history list already includes it — don't double-append.

            options = {
                "temperature": cfg.get("temperature", 0.8),
                "top_p": cfg.get("top_p", 0.9),
                "top_k": cfg.get("top_k", 40),
                "min_p": cfg.get("min_p", 0.0),
                "repeat_penalty": cfg.get("repeat_penalty", 1.1),
                "presence_penalty": cfg.get("presence_penalty", 0.0),
                "frequency_penalty": cfg.get("frequency_penalty", 0.0),
                "num_ctx": _num_ctx_of(cfg),
            }
            model = strip_model_annotation(str(cfg.get("model", "") or "")).strip()
            cache = bool(cfg.get("cache", True))
            cache_ttl = str(cfg.get("cache_ttl", "auto"))
            openrouter_provider = llm_backend.build_openrouter_provider_routing(
                cfg.get("openrouter_provider_order"),
                bool(cfg.get("openrouter_allow_fallbacks", True)),
            )
            # Same input-budget contract as every other conversational
            # surface: num_ctx minus ~2K reply headroom. Omitting this
            # meant the mini chat sent the entire archive every message
            # regardless of num_ctx — the exact bug class behind the
            # historical Telegram overspend (audit DH2).
            max_ctx_tokens = max(512, _num_ctx_of(cfg) - 2000)
            result = llm_backend.chat(
                model, messages, options=options,
                stream=False, cache=cache, cache_ttl=cache_ttl,
                openrouter_provider=openrouter_provider,
                max_context_tokens=max_ctx_tokens,
                kin_name=kin, surface="mini-chat",
                ollama_host=resolve_kin_ollama_host(
                    cfg.get("ollama_host_name", "")),
            )
            # Pull inline <thinking> markup out of content and merge it
            # into the structured field — the v0.4.8 "every send
            # surface" normalization missed this surface (audit M-F9).
            reply, thinking = extract_inline_thinking(
                result.content or "", result.thinking or "")
            reply = strip_self_timestamp(reply).strip()
            if not reply:
                self._log_empty_reply(
                    kin or "?", model, result.content or "")
                reply = "[no reply produced]"
        except Exception as e:
            reply = f"[error: {e}]"

        ts = now_iso()
        assistant_msg = {"role": "assistant", "content": reply, "ts": ts}
        if thinking:
            cap = int(cfg.get("think_max_chars", 1200) or 0)
            if cap > 0 and len(thinking) > cap:
                thinking = thinking[:cap] + "\n... [reasoning truncated]"
            assistant_msg["thinking"] = thinking

        def finish():
            if self._closing:
                return
            if self.current_agent != kin or self.current_room is not None:
                # Kin switched (or a room took over) while the call was
                # in flight — self.conversation and the chat display
                # belong to the new context now. Persist the reply
                # straight to the captured kin's conversation.jsonl
                # (the user turn was already persisted at send time)
                # and mirror under the captured kin (audit M-F8).
                try:
                    append_agent_conversation_turn(kin, assistant_msg)
                except Exception as e:
                    append_failure_log(
                        "save_failures.log", kin,
                        "mini-chat reply (kin switched mid-call)", e,
                    )
                try:
                    if mini_chat_frame is not None:
                        mini_chat_frame.append_assistant_reply(reply)
                except Exception:
                    pass
                try:
                    self._maybe_mirror_to_telegram(kin, user_text, reply)
                except Exception:
                    pass
                return
            self.conversation.append(assistant_msg)
            try:
                self._persist_current_conversation()
            except Exception:
                pass
            try:
                self._render_conversation()
            except Exception:
                pass
            try:
                if mini_chat_frame is not None:
                    mini_chat_frame.append_assistant_reply(reply)
            except Exception:
                pass
            # Speak the reply if voice is on for the active kin. The
            # mini-chat path is non-streaming so we can't sentence-
            # stream from intermediate chunks; just queue the whole
            # reply at once. The engine still chunks audio internally,
            # so playback starts as the first PCM bytes arrive.
            try:
                self._maybe_speak_sentence(reply)
            except Exception:
                pass
            # Mirror to Telegram for opted-in users — same as the
            # desktop send-and-reply path.
            try:
                self._maybe_mirror_to_telegram(kin, user_text, reply)
            except Exception:
                pass

        wx.CallAfter(finish)

    def _on_search(self, event):
        dlg = SearchDialog(self, on_open_target=self._open_search_target)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_play_park(self, event):
        """Open the shared-park play dialog, defaulting to the active kin's
        park so it's usually a single keystroke to co-tend."""
        active = self.current_agent or ""
        dlg = ParkPlayDialog(self, active_kin=active)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_edit_park_words(self, event):
        """Open the editor for the park's hand-editable word lists (actions,
        per-species nicknames, 'everyone' words) — install-wide, no kin needed."""
        from dialogs import ParkVocabDialog
        dlg = ParkVocabDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _open_search_target(self, kind, name):
        """Callback from SearchDialog when the user picks a result."""
        if kind == "room":
            if name in list_rooms():
                self._load_room(name)
        else:
            if name in list_agents():
                self._load_agent(name)
                # If the search hit the kin's soul or memory, open the Settings
                # dialog so the user lands where the match lives.
                self._on_edit_kin(None)
