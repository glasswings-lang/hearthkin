"""MenusMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    HealthCheckDialog,
    EditKinDialog, LOGS_DIR, Path, UsageHistoryDialog, __version__, _tool_cap_cache,
    agent_dir, clear_models_cache, datetime, json, list_agents, list_rooms,
    load_agent_config, nvda_speak, os, strip_model_annotation, sys, threading, urllib, wx,
)


class MenusMixin:

    # --- UI construction --- #

    def _build_ui(self):
        self._build_menu()

        # Notebook removed (was Chat / Usage / Preferences). Reasons:
        #   - Tab navigation widget added a layer of overhead the chat-
        #     focused workflow didn't justify.
        #   - For NVDA users the notebook was never quite right —
        #     wxMSW's notebook traps focus cycles inside each page in
        #     ways that fight standard Tab navigation.
        #   - Most desktop apps put settings/usage in menu-triggered
        #     dialogs, not tabs. We were the odd one out.
        # Current shape: chat content fills the main window directly;
        # Preferences and Usage are dialogs opened from the Tools menu
        # (and the tray icon's right-click menu). The dialogs are
        # built lazily on first open and re-shown thereafter so widget
        # references stored on `self` remain valid across hide/show.
        outer = wx.Panel(self)
        self._build_chat_tab(outer)

        # Lazy-init holders for the menu-triggered dialogs.
        self._prefs_dialog = None
        self._usage_dialog = None

        self.Centre()

    def _build_menu(self):
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(wx.ID_NEW, "&New conversation\tCtrl+N")
        file_menu.Append(wx.ID_OPEN, "&Open conversation...\tCtrl+O")
        file_menu.Append(wx.ID_SAVE, "Save snapshot...\tCtrl+S")
        self.mnu_export_md = file_menu.Append(wx.ID_ANY, "Export as &markdown...")
        self.mnu_import_history = file_menu.Append(
            wx.ID_ANY, "&Import history...",
        )
        # Separate entry, not a mode of Import, on purpose: import labels
        # what it writes as carried-in history and stamps every row
        # `source: import:<label>`. A kin's own archive must not be
        # relabelled that way. See dialogs/restore_history.py.
        self.mnu_restore_history = file_menu.Append(
            wx.ID_ANY, "Restore a kin's histor&y...",
        )
        file_menu.AppendSeparator()
        # Preferences moved out of Tools and up to File. wx.ID_PREFERENCES
        # is the standard ID; on macOS it auto-routes to the app menu's
        # standard entry. On Windows the placement is just before Exit
        # in the File menu — common pattern for desktop apps.
        self.mnu_preferences = file_menu.Append(
            wx.ID_PREFERENCES, "&Preferences...\tCtrl+,",
        )
        file_menu.AppendSeparator()
        # Note on Exit's accelerator: we deliberately do NOT bind
        # Alt+F4 here. wxPython treats menu accelerators as real
        # bindings, which would steal Alt+F4 from the OS's window-
        # close path — meaning _on_exit would fire (with _quitting=True)
        # and close-to-tray would be silently bypassed. Alt+F4 should
        # go through the natural EVT_CLOSE → _on_close chain so the
        # close_to_tray preference actually controls what happens.
        # Ctrl+Q is the modern Windows convention for explicit exit.
        file_menu.Append(wx.ID_EXIT, "E&xit\tCtrl+Q")
        menubar.Append(file_menu, "&File")

        agent_menu = wx.Menu()
        self.mnu_new_agent = agent_menu.Append(wx.ID_ANY, "&New kin...\tCtrl+Shift+N")
        self.mnu_clone_agent = agent_menu.Append(wx.ID_ANY, "&Clone kin...")
        # Rename moved into the per-kin Settings dialog (Identity tab)
        # so it lives next to the kin's name field instead of in a
        # top-level menu that felt like operating ON the kin from
        # outside.
        self.mnu_delete_agent = agent_menu.Append(wx.ID_ANY, "&Delete kin...")
        agent_menu.AppendSeparator()
        self.mnu_save_soul = agent_menu.Append(wx.ID_ANY, "&Save soul file\tCtrl+Shift+S")
        menubar.Append(agent_menu, "&Kin")

        room_menu = wx.Menu()
        self.mnu_new_room = room_menu.Append(wx.ID_ANY, "&New room...")
        self.mnu_edit_room = room_menu.Append(wx.ID_ANY, "Current room s&ettings...")
        self.mnu_delete_room = room_menu.Append(wx.ID_ANY, "&Delete current room...")
        menubar.Append(room_menu, "&Room")

        chat_menu = wx.Menu()
        self.mnu_regen = chat_menu.Append(wx.ID_ANY, "&Regenerate last reply\tCtrl+R")
        self.mnu_stop = chat_menu.Append(wx.ID_ANY, "Sto&p generating\tEsc")
        self.mnu_continue = chat_menu.Append(wx.ID_ANY, "Continue room round\tCtrl+Enter")
        # Rewrite any past turn in place — the surgical alternative to
        # Clear chat when one bad reply is poisoning the room's context.
        # Alt+E is safe inside the Chat menu (each menu has its own
        # mnemonic scope; the frame-level Alt+E on "Kin s&ettings..." is
        # unaffected).
        self.mnu_edit_message = chat_menu.Append(
            wx.ID_ANY, "&Edit a message...\tCtrl+E")
        # "What is it doing right now?" — the app already knows, and already
        # says it in sentences: _work_in_flight composes them for the quit
        # prompt, and the heartbeat writes them to a log every minute. It
        # simply had no way to be ASKED. The only way to find out a kin was
        # waiting behind a 16-minute distillation was to start quitting and
        # read the warning, which is a miserable way to check something you
        # want to check often.
        self.mnu_whats_busy = chat_menu.Append(
            wx.ID_ANY, "What's it &busy with?\tCtrl+B")
        menubar.Append(chat_menu, "&Chat")

        tools_menu = wx.Menu()
        self.mnu_search = tools_menu.Append(wx.ID_ANY, "&Search across kin...\tCtrl+F")
        # Take a turn in a kin's own creature-park (the same save the kin
        # plays through its tff tool). Reachable here, not buried in kin
        # settings, so co-tending a shared park is a couple of keystrokes.
        self.mnu_play_park = tools_menu.Append(
            wx.ID_ANY, "Tend a kin's &park…\tCtrl+Shift+P")
        # Edit the park's hand-editable word lists (verbs, per-species
        # nicknames, 'everyone' words) without hunting for the files.
        self.mnu_park_words = tools_menu.Append(
            wx.ID_ANY, "Edit park &words…")
        tools_menu.AppendSeparator()
        # Usage cluster — all three entry points to the same data,
        # grouped together. "Usage stats..." is the live per-kin
        # breakdown; "Usage history…" is the NVDA-friendly aggregated
        # dialog (date filters, by-kin/model/surface breakdowns); the
        # raw log is the power-user grep view. This is the answer to
        # "where did my OpenRouter credits go" — pick the one whose
        # shape fits the question.
        self.mnu_usage = tools_menu.Append(wx.ID_ANY, "&Usage stats...")
        self.mnu_usage_history = tools_menu.Append(
            wx.ID_ANY, "Usage &history…",
        )
        self.mnu_open_usage_log = tools_menu.Append(
            wx.ID_ANY, "Open raw usage lo&g",
        )
        # Sits with the usage items because it reads the same log — but it
        # answers a different question. Usage stats say what was spent; this
        # says why you're waiting, in words, with the likely cause named.
        self.mnu_speed_check = tools_menu.Append(
            wx.ID_ANY, "Speed chec&k…",
        )
        tools_menu.AppendSeparator()
        self.mnu_open_logs = tools_menu.Append(
            wx.ID_ANY, "Open &logs folder",
        )
        self.mnu_open_kin_folder = tools_menu.Append(
            wx.ID_ANY, "Open active &kin folder",
        )
        # Edit the shared base prompt that applies to every kin. This
        # is app-wide, not per-kin — its content prepends every kin's
        # soul.md on every send, so the token cost is per-kin × per-turn.
        # Worth a look every now and then to see what's in there.
        self.mnu_edit_base_prompt = tools_menu.Append(
            wx.ID_ANY, "Edit &base prompt…",
        )
        # Install-wide editor for every registered harness prompt. The per-kin
        # Prompts tab edits one kin's override; this edits the shared layer all
        # kin fall back to. Before it existed the detector word-lists
        # (gesture_messages, reach_messages) had no UI at all despite existing
        # specifically to be operator-edited.
        self.mnu_edit_prompts = tools_menu.Append(
            wx.ID_ANY, "Edit &prompts…",
        )
        # Opt-in adoption of improved built-in prompt defaults. Never changes
        # the operator's files on its own — this is where they choose to.
        self.mnu_prompt_updates = tools_menu.Append(
            wx.ID_ANY, "Prompt &updates…",
        )
        tools_menu.AppendSeparator()
        self.mnu_clear_caches = tools_menu.Append(
            wx.ID_ANY, "&Clear cached model lists",
        )
        menubar.Append(tools_menu, "&Tools")

        # Help menu — standard Windows position (rightmost). Holds
        # User guide, Check for updates, About. Same items also live
        # on the system-tray context menu for non-keyboard reach.
        help_menu = wx.Menu()
        self.mnu_user_guide = help_menu.Append(wx.ID_ANY, "&User guide")
        self.mnu_check_updates = help_menu.Append(
            wx.ID_ANY, "&Check for updates...",
        )
        help_menu.AppendSeparator()
        self.mnu_about = help_menu.Append(wx.ID_ABOUT, "&About Hearthkin")
        menubar.Append(help_menu, "&Help")

        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self._on_new, id=wx.ID_NEW)
        self.Bind(wx.EVT_MENU, self._on_open, id=wx.ID_OPEN)
        self.Bind(wx.EVT_MENU, self._on_save, id=wx.ID_SAVE)
        self.Bind(wx.EVT_MENU, self._on_export_md, self.mnu_export_md)
        self.Bind(wx.EVT_MENU, self._on_import_history, self.mnu_import_history)
        self.Bind(wx.EVT_MENU, self._on_restore_history, self.mnu_restore_history)
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_new_agent, self.mnu_new_agent)
        self.Bind(wx.EVT_MENU, self._on_clone_agent, self.mnu_clone_agent)
        # _on_rename_agent is still bound — it's now invoked from
        # EditKinDialog's "Rename..." button rather than a menu item.
        self.Bind(wx.EVT_MENU, self._on_delete_agent, self.mnu_delete_agent)
        self.Bind(wx.EVT_MENU, self._on_save_soul, self.mnu_save_soul)
        self.Bind(wx.EVT_MENU, self._on_new_room, self.mnu_new_room)
        self.Bind(wx.EVT_MENU, self._on_edit_room, self.mnu_edit_room)
        self.Bind(wx.EVT_MENU, self._on_delete_room, self.mnu_delete_room)
        self.Bind(wx.EVT_MENU, self._on_regen, self.mnu_regen)
        self.Bind(wx.EVT_MENU, self._on_stop, self.mnu_stop)
        self.Bind(wx.EVT_MENU, self._on_whats_busy, self.mnu_whats_busy)
        self.Bind(wx.EVT_MENU, self._on_continue, self.mnu_continue)
        self.Bind(wx.EVT_MENU, self._on_edit_message, self.mnu_edit_message)
        self.Bind(wx.EVT_MENU, self._on_search, self.mnu_search)
        self.Bind(wx.EVT_MENU, self._on_play_park, self.mnu_play_park)
        self.Bind(wx.EVT_MENU, self._on_edit_park_words, self.mnu_park_words)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self.open_preferences_dialog(),
                  self.mnu_preferences)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self.open_usage_dialog(),
                  self.mnu_usage)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._open_user_guide(),
                  self.mnu_user_guide)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_check_for_updates(),
                  self.mnu_check_updates)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self.show_about_dialog(),
                  self.mnu_about)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_open_logs_folder(),
                  self.mnu_open_logs)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_usage_history(),
                  self.mnu_usage_history)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_speed_check(),
                  self.mnu_speed_check)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_open_usage_log(),
                  self.mnu_open_usage_log)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_open_kin_folder(),
                  self.mnu_open_kin_folder)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_edit_base_prompt(),
                  self.mnu_edit_base_prompt)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_edit_all_prompts(),
                  self.mnu_edit_prompts)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_prompt_updates(),
                  self.mnu_prompt_updates)
        self.Bind(wx.EVT_MENU,
                  lambda _e: self._on_clear_model_caches(),
                  self.mnu_clear_caches)

    def _open_user_guide(self):
        """Help → User guide. Open the bundled user-guide.html in the
        default browser, falling back to the GitHub copy if not found.
        Uses the shared `_find_bundled_doc` resolver (in kin_persistence,
        a module that's reliably under _internal/ when frozen) which
        prefers the current _internal/docs/ copy over any stale {app}/docs/
        orphan left by an old install layout — the bug that had this menu
        opening months-old docs."""
        p = None
        try:
            from kin_persistence import _find_bundled_doc
            p = _find_bundled_doc("user-guide.html")
        except Exception:
            p = None
        if p is not None:
            try:
                import webbrowser
                webbrowser.open(Path(p).resolve().as_uri())
                return
            except Exception:
                pass
        try:
            import webbrowser
            webbrowser.open(
                "https://github.com/glasswings-lang/hearthkin/blob/master/docs/user-guide.html"
            )
        except Exception:
            pass

    def _on_open_logs_folder(self):
        """Open ~/.hearthkin/logs/ in Explorer (or the platform's
        equivalent). Useful when troubleshooting empty replies,
        telegram failures, save failures, cron errors — all of which
        write to that directory."""
        try:
            path = LOGS_DIR
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)])
            self._set_status(f"Opened logs folder: {path}")
        except Exception as e:
            self._set_status(f"Couldn't open logs folder: {e}")
            try:
                nvda_speak(f"Couldn't open logs folder: {e}")
            except Exception:
                pass

    def _on_speed_check(self):
        """Open the Speed check — reads logs/usage.log and reports, in plain
        language, why replies are taking as long as they are. Exists because a
        silent config regression on the model machine cost weeks of six-minute
        replies with no signal anywhere; every number needed to spot it was
        already being logged."""
        try:
            from kin_persistence import LOGS_DIR
            dlg = HealthCheckDialog(self, str(LOGS_DIR / "usage.log"))
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            self._set_status(f"Couldn't open the speed check: {e}")

    def _on_usage_history(self):
        """Open the structured Usage history dialog — reads
        ~/.hearthkin/logs/usage.log, parses it into rows, presents
        filterable summary + per-kin / per-model / per-surface
        breakdowns + a recent-calls list. NVDA-friendly alternative
        to opening the raw text file. The raw-file path is still
        available via the next menu item below this one and via the
        "Open raw log file" button inside this dialog."""
        try:
            dlg = UsageHistoryDialog(self)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            self._set_status(f"Couldn't open usage history: {e}")

    def _on_open_usage_log(self):
        """Open ~/.hearthkin/logs/usage.log in the platform's default
        text viewer. One line per llm_backend.chat() call, with
        timestamp, kin, model, tokens in/out, USD estimate, and
        surface label (desktop / room / telegram-dm / telegram-group /
        distill / cron / etc.). The answer to "where did my OpenRouter
        credits go today?" — tail or grep this file."""
        try:
            from kin_persistence import USAGE_LOG_PATH
            path = USAGE_LOG_PATH
            if not path.exists():
                # Touch an empty file so the open succeeds with an
                # informative empty doc rather than failing with a
                # "no such file" error before any calls have been
                # logged.
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "# Hearthkin usage log — one line per chat() call.\n"
                    "# Format: <iso-timestamp> kin=<name> model=<id> "
                    "in=<prompt_tokens> out=<completion_tokens> "
                    "est_cost=$X.XXXX surface=<label>\n",
                    encoding="utf-8",
                )
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)])
            self._set_status(f"Opened usage log: {path}")
        except Exception as e:
            self._set_status(f"Couldn't open usage log: {e}")
            try:
                nvda_speak(f"Couldn't open usage log: {e}")
            except Exception:
                pass

    def _on_open_kin_folder(self):
        """Open the active kin's folder (~/.hearthkin/kin/<name>/)
        in Explorer. Useful for hand-editing soul / memory / journal
        files outside the dialogs, or for spot-checking what got
        persisted."""
        if not self.current_agent:
            self._set_status("No kin loaded — nothing to open.")
            try:
                nvda_speak("No kin loaded.")
            except Exception:
                pass
            return
        try:
            path = agent_dir(self.current_agent)
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)])
            self._set_status(f"Opened kin folder: {self.current_agent}")
        except Exception as e:
            self._set_status(f"Couldn't open kin folder: {e}")
            try:
                nvda_speak(f"Couldn't open kin folder: {e}")
            except Exception:
                pass

    def _on_edit_base_prompt(self):
        """Tools → Edit base prompt… — opens BasePromptDialog on the
        shared ~/.hearthkin/base_prompt.md file. The base prompt
        prepends every kin's soul.md on every send, so changes take
        effect immediately on the next send for every kin (the file
        is re-read each turn via build_system_prompt)."""
        from dialogs.base_prompt import BasePromptDialog
        dlg = BasePromptDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_edit_all_prompts(self):
        """Tools → Edit prompts… — browse and edit the install-wide copy of
        every registered harness prompt (the text Hearthkin wraps around a kin:
        injected notes, nudges, cron framing, the detector word-lists).

        The per-kin Prompts tab edits one kin's override; this edits the shared
        layer every kin falls back to. Until this existed there was no
        install-wide editor at all, which left gesture_messages and
        reach_messages with no UI despite being there to be operator-edited."""
        from dialogs import AllAppPromptsDialog
        dlg = AllAppPromptsDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()
        self._set_status("Prompts closed")

    def _on_prompt_updates(self):
        """Tools → Prompt updates… — opt-in adoption of improved built-in
        prompt defaults. Lists prompts whose shipped default version is newer
        than what the operator's install-wide file was seeded at, and lets them
        adopt / stash / preview each. Nothing changes unless they choose it;
        per-kin overrides are never touched."""
        from dialogs.prompt_updates import PromptUpdatesDialog
        dlg = PromptUpdatesDialog(self, on_status=self._set_status)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _maybe_nudge_prompt_updates(self):
        """Startup nudge: if a newer built-in default shipped for a prompt the
        operator already seeded, mention it once (Activity field + NVDA). Never
        opens anything or changes a file — just points at Tools → Prompt
        updates so the operator can decide. Silent when nothing's stale."""
        try:
            from kin_persistence import app_prompts_needing_update
            stale = app_prompts_needing_update()
        except Exception:
            stale = []
        # base_prompt.md and distill_prompt.md predate the registry, so the
        # Prompt updates dialog can't adopt them — but an operator who
        # overrode one would otherwise never hear that the default moved on.
        # Named separately here because the advice differs: compare by hand.
        try:
            from kin_persistence import legacy_prompt_overrides_needing_review
            legacy = legacy_prompt_overrides_needing_review()
        except Exception:
            legacy = []
        if not stale and not legacy:
            return
        parts = []
        if stale:
            n = len(stale)
            parts.append(
                f"{n} prompt update{'s' if n != 1 else ''} available — "
                "open the Tools menu, Prompt updates, to review or adopt."
            )
        if legacy:
            names = ", ".join(title for (_k, _h, _s, title) in legacy)
            parts.append(
                "A newer default also shipped for: " + names + ". Those keep "
                "your wording and can't be adopted automatically — compare "
                "them by hand if you want the new rules."
            )
        msg = " ".join(parts)
        self._set_status(msg)
        try:
            nvda_speak(msg)
        except Exception:
            pass

    def _on_clear_model_caches(self):
        """Clear Hearthkin's in-memory model-list caches: Ollama
        models (model_utils._models_cache), per-model capability
        cache (_tool_cap_cache), and the voice catalog cache. Next
        access to any of those re-fetches from source.

        Doesn't pre-fetch — the rename from "Refresh" to "Clear" is
        deliberate. Pre-fetching three providers on demand would
        block the UI; clearing is instant and the next call (e.g.
        opening the model browser) does the work it would have done
        anyway."""
        cleared = []
        try:
            clear_models_cache()
            cleared.append("Ollama")
        except Exception:
            pass
        try:
            _tool_cap_cache.clear()
            cleared.append("model capabilities")
        except Exception:
            pass
        # Voice catalog cache is on the engine instance.
        try:
            engine = getattr(self, "_voice_engine", None)
            if engine is not None:
                with engine._voices_cache_lock:
                    engine._voices_cache = None
                cleared.append("voices")
        except Exception:
            pass
        msg = (
            f"Cleared cached lists: {', '.join(cleared)}. "
            "Next browse will re-fetch."
            if cleared else
            "Nothing to clear."
        )
        self._set_status(msg)
        try:
            nvda_speak(msg)
        except Exception:
            pass

    def _on_check_for_updates(self):
        """Help → Check for updates. Hits GitHub Releases API for
        the latest tag, compares against __version__, surfaces the
        result in the Activity field + force-spoken via NVDA so a
        screen-reader user gets immediate feedback. If a newer
        version exists, offers to open the Releases page in the
        browser. No background polling — explicit user action only."""
        self._set_status("Checking for updates…")
        try:
            nvda_speak("Checking for updates")
        except Exception:
            pass
        threading.Thread(
            target=self._check_for_updates_worker, daemon=True,
        ).start()

    def _check_for_updates_worker(self):
        latest = ""
        body = ""
        err = None
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/glasswings-lang/hearthkin/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"Hearthkin/{__version__}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                latest = (data.get("tag_name") or "").lstrip("v")
                body = data.get("body") or ""
        except Exception as e:
            err = str(e)

        def finish():
            if err:
                self._log_update_check(f"manual: FAILED — {err}")
                msg = f"Couldn't reach the update server: {err}"
                self._set_status(msg)
                try:
                    nvda_speak(msg)
                except Exception:
                    pass
                return
            if not latest:
                self._log_update_check("manual: no release found on server")
                msg = "No release found on the update server."
                self._set_status(msg)
                try:
                    nvda_speak(msg)
                except Exception:
                    pass
                return
            if self._is_newer_version(latest, __version__):
                self._log_update_check(
                    f"manual: NEWER available — {latest} "
                    f"(running {__version__})"
                )
                self._update_available_version = latest
                msg = (
                    f"Hearthkin {latest} is available "
                    f"(you have {__version__})."
                )
                self._set_status(msg + " Opening the Releases page.")
                try:
                    nvda_speak(msg)
                except Exception:
                    pass
                # Pop a confirmation so the user can decline opening
                # the browser if they're mid-task. wx.CANCEL is included
                # so ESCAPE dismisses the box: on wxMSW a native message
                # box only wires Escape to a Cancel button, so a bare
                # YES_NO box traps the user (no keyboard way out — an
                # accessibility snag for NVDA users). Cancel and No both
                # decline; only YES opens the browser.
                resp = wx.MessageBox(
                    f"{msg}\n\nOpen the Releases page in your browser?",
                    "Hearthkin update available",
                    wx.YES_NO | wx.CANCEL | wx.ICON_INFORMATION,
                    parent=self,
                )
                if resp == wx.YES:
                    try:
                        import webbrowser
                        webbrowser.open(
                            "https://github.com/glasswings-lang/hearthkin/releases/latest"
                        )
                    except Exception:
                        pass
            else:
                self._log_update_check(
                    f"manual: up to date (running {__version__}, "
                    f"latest {latest})"
                )
                # Clear any stale "update available" flag — the user
                # has reached this state either because they updated
                # or because the prior check was wrong.
                self._update_available_version = None
                msg = (
                    f"You're on the latest release ({__version__})."
                )
                self._set_status(msg)
                try:
                    nvda_speak(msg)
                except Exception:
                    pass

        wx.CallAfter(finish)

    def _check_for_updates_worker_quiet(self):
        """Background variant of the update check, fired on startup
        when auto_check_updates_on_startup is on. Surfaces a newer
        version via the Activity field + NVDA speech, AND persists
        the version in self._update_available_version so the
        default-status summary line keeps showing it after the
        Activity field reverts. Silent on no-news case.

        Every outcome (newer / up to date / error) is logged to
        ~/.hearthkin/logs/update_check.log so silent failures
        become visible after the fact — previously this worker
        had `except Exception: return` with no record, making
        "the auto-check never fired" impossible to diagnose.
        """
        latest = ""
        err = None
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/glasswings-lang/hearthkin/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"Hearthkin/{__version__}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                latest = (data.get("tag_name") or "").lstrip("v")
        except Exception as e:
            err = str(e)

        if err:
            self._log_update_check(f"auto: FAILED — {err}")
            return
        if not latest:
            self._log_update_check("auto: no release found on server")
            return
        if not self._is_newer_version(latest, __version__):
            self._log_update_check(
                f"auto: up to date (running {__version__}, "
                f"latest {latest})"
            )
            return

        self._log_update_check(
            f"auto: NEWER available — {latest} "
            f"(running {__version__})"
        )

        def announce():
            self._update_available_version = latest
            msg = (
                f"Hearthkin {latest} is available "
                f"(you have {__version__}). "
                f"Use Help, Check for updates to download."
            )
            self._set_status(msg)
            try:
                nvda_speak(msg)
            except Exception:
                pass

        wx.CallAfter(announce)

    def _log_update_check(self, message):
        """Append one line to ~/.hearthkin/logs/update_check.log.

        Always-on log (like nvda_status.log and empty_replies.log)
        so users running under pythonw.exe can diagnose silent
        update-check failures without a console. Best-effort —
        never raises."""
        try:
            from kin_persistence import LOGS_DIR
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "update_check.log"
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts} {message}\n")
        except Exception:
            pass

    def _is_newer_version(self, candidate, current):
        """Compare two semver-ish strings (e.g. "0.2.4" vs "0.2.3").
        Returns True if candidate is strictly newer than current.
        Handles missing parts and non-numeric suffixes by best-effort
        casting; falls back to lexicographic compare on parse failure."""
        def parse(s):
            parts = []
            for chunk in (s or "").split("."):
                # Strip non-digit suffixes like "0.2.3-rc1" → 3
                num = ""
                for ch in chunk:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                parts.append(int(num) if num else 0)
            return tuple(parts)
        try:
            return parse(candidate) > parse(current)
        except Exception:
            return candidate > current

    def _build_header(self, parent):
        """Header: mode radio + a swappable sub-panel that holds either the
        kin selector or the room selector.

        Tab order in kin mode: Mode radio → Kin combo → New kin → Kin settings
        → Model combo → Refresh models. In room mode: Mode radio → Room combo
        → New room → Room settings.
        """
        outer = wx.BoxSizer(wx.VERTICAL)

        # Two individual RadioButtons (not a RadioBox) for accessibility.
        # A wx.RadioBox is one composite control: when NVDA lands on it,
        # only the currently-checked option is announced — the other
        # option seems "gone" unless the user knows to press arrow keys
        # within the box. With two separate RadioButtons, Tab walks
        # across them and NVDA announces each one by name, so both
        # options are always discoverable.
        # The first button gets wx.RB_GROUP to start a new radio group;
        # the second joins it automatically (mutually exclusive without
        # any extra wiring).
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        mode_lbl = wx.StaticText(parent, label="Mode:")
        # No mnemonics here, deliberately. These were "Talk with a &kin"
        # (Alt+K) and "Talk in a &room" (Alt+R) — both dead on arrival,
        # because the menubar's "&Kin" and "&Room" claim those letters and
        # the menu always wins. The rule is even written down on the Take
        # photo button below ("NOT Alt+T ... menu wins, the button mnemonic
        # would be dead"); it just wasn't applied here.
        #
        # A dead mnemonic is worse than none: the label teaches Alt+R means
        # "switch to room mode", and every press opens a menu instead, which
        # for an NVDA user is a derail that needs an Escape to get out of.
        # Alt+K / Alt+R now have exactly one meaning each — the menus — which
        # is the Windows convention and is where the kin/room operations
        # (including the settings dialogs) actually live.
        #
        # Not re-homed onto free letters because there aren't any: every
        # letter in "Talk with a kin" / "Talk in a room" is already claimed
        # somewhere in this frame. That's a signal the header is over-
        # subscribed, not a puzzle to solve with a worse letter. The radio
        # group is one Tab away and arrows switch it, which is the standard
        # path for a two-option radio anyway.
        self.mode_kin_radio = wx.RadioButton(
            parent, label="Talk with a kin", style=wx.RB_GROUP,
        )
        self.mode_room_radio = wx.RadioButton(
            parent, label="Talk in a room",
        )
        self.mode_kin_radio.Bind(wx.EVT_RADIOBUTTON, self._on_mode_radio)
        self.mode_room_radio.Bind(wx.EVT_RADIOBUTTON, self._on_mode_radio)
        mode_row.Add(mode_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        mode_row.Add(self.mode_kin_radio, flag=wx.RIGHT, border=12)
        mode_row.Add(self.mode_room_radio)
        outer.Add(mode_row, flag=wx.EXPAND | wx.BOTTOM, border=4)

        # --- Kin sub-panel ---
        self.kin_header_panel = wx.Panel(parent)
        kp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        kin_lbl = wx.StaticText(self.kin_header_panel, label="Kin:")
        self.agent_choice = wx.ComboBox(self.kin_header_panel, style=wx.CB_READONLY)
        self.agent_choice.Bind(wx.EVT_COMBOBOX, self._on_kin_selected_event)
        new_kin_btn = wx.Button(self.kin_header_panel, label="&New kin...")
        new_kin_btn.Bind(wx.EVT_BUTTON, self._on_new_agent)
        # Settings opens the per-kin Settings dialog (formerly "Edit
        # kin"). Renamed because that framing felt like operating on
        # the kin from outside; "settings" frames it as the kin's
        # configuration the operator can adjust.
        # Mnemonic note: Alt+S was a conflict with the Send button.
        # Moved to Alt+E for symmetry with "Room s&ettings..." in the
        # other mode panel — same letter, parallel role, mutually
        # exclusive visibility means no actual collision.
        edit_kin_btn = wx.Button(self.kin_header_panel, label="Kin s&ettings...")
        edit_kin_btn.Bind(wx.EVT_BUTTON, self._on_edit_kin)
        self.edit_kin_btn = edit_kin_btn
        # NOTE: the kin's chat model + Refresh Models button + Browse
        # Models button used to live in this header row. They moved into
        # the Settings dialog — model selection is
        # a per-kin configuration, not a frequent quick-swap, and the
        # header was crowded. At chat-send time the model now comes from
        # self.agent_cfg["model"] (via _current_chat_model_clean), not
        # from any widget — cfg is the source of truth on disk.
        kp_sizer.Add(kin_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        kp_sizer.Add(self.agent_choice, proportion=2, flag=wx.RIGHT, border=6)
        kp_sizer.Add(new_kin_btn, flag=wx.RIGHT, border=4)
        kp_sizer.Add(edit_kin_btn)
        self.kin_header_panel.SetSizer(kp_sizer)

        # --- Room sub-panel ---
        self.room_header_panel = wx.Panel(parent)
        rp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        room_lbl = wx.StaticText(self.room_header_panel, label="Room:")
        self.room_choice = wx.ComboBox(self.room_header_panel, style=wx.CB_READONLY)
        self.room_choice.Bind(wx.EVT_COMBOBOX, self._on_room_selected_event)
        new_room_btn = wx.Button(self.room_header_panel, label="&New room...")
        new_room_btn.Bind(wx.EVT_BUTTON, self._on_new_room)
        # "Room settings...", not "Edit room..." — same rename, and for the
        # same reason, as the kin button above. The two were given the same
        # mnemonic (Alt+E) for symmetry and then left saying different
        # words, so the room's button read as "change this room's name and
        # members" while holding its settings. An operator looking for the
        # room's memory toggle opened this, saw "Edit room", and escaped
        # out of the exact dialog it lives in.
        self.edit_room_btn = wx.Button(self.room_header_panel, label="Room s&ettings...")
        self.edit_room_btn.Bind(wx.EVT_BUTTON, self._on_edit_room)
        rp_sizer.Add(room_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        rp_sizer.Add(self.room_choice, proportion=1, flag=wx.RIGHT, border=6)
        rp_sizer.Add(new_room_btn, flag=wx.RIGHT, border=4)
        rp_sizer.Add(self.edit_room_btn)
        self.room_header_panel.SetSizer(rp_sizer)

        outer.Add(self.kin_header_panel, flag=wx.EXPAND | wx.BOTTOM, border=4)
        outer.Add(self.room_header_panel, flag=wx.EXPAND | wx.BOTTOM, border=4)

        # Populate now that combos exist
        self._refresh_kin_list(select=None)
        self._refresh_room_list(select=None)
        # NOTE: _refresh_model_list used to be called here too, but the
        # model dropdown moved to the Settings dialog. The dialog
        # populates its own dropdown on open.

        # Initial visibility — set to whatever the saved last_target_kind is.
        # _apply_mode_visibility runs on the panels we just made.
        last_kind = self.config.get("last_target_kind", "kin")
        self._mode_set_kin(last_kind != "room")
        self._apply_mode_visibility()
        return outer

    def _mode_is_kin(self):
        """True when the 'Talk with a kin' radio is selected."""
        return self.mode_kin_radio.GetValue()

    def _mode_set_kin(self, is_kin):
        """Programmatically pick the kin or room radio. Doesn't fire
        EVT_RADIOBUTTON (wxPython convention — SetValue is silent),
        so callers that need _on_mode_radio side effects must invoke
        them explicitly. Matches the old mode_radio.SetSelection
        behavior, just split across the two new buttons."""
        if is_kin:
            self.mode_kin_radio.SetValue(True)
        else:
            self.mode_room_radio.SetValue(True)

    def _apply_mode_visibility(self):
        """Show/hide the kin or room header panel based on the mode radio.

        Both Show/Hide and Enable/Disable are needed: on Windows wxPython,
        Hide() alone doesn't reliably remove widgets from the keyboard tab-
        navigation chain, especially for nested combos.
        """
        if not hasattr(self, "kin_header_panel"):
            return
        is_kin = self._mode_is_kin()
        self.kin_header_panel.Show(is_kin)
        self.kin_header_panel.Enable(is_kin)
        self.room_header_panel.Show(not is_kin)
        self.room_header_panel.Enable(not is_kin)
        parent = self.kin_header_panel.GetParent()
        parent.Layout()

    def _on_mode_radio(self, event):
        """User flipped the mode. Fast path: swap visible panels and set
        a brief status message immediately — that's all that should be
        synchronous with the keypress. Slow path: actually load the
        last-active kin or room. The load reads soul/memory/conversation
        from disk and re-renders the chat display (~140KB and 194 lines
        for a long-running kin), which is real wall-clock time.

        For NVDA users navigating the radio group with left/right arrow
        keys, doing the load synchronously means each arrow press blocks
        the UI thread for half a second or more — feels broken. We
        defer the load via wx.CallLater(200, ...) and cancel any pending
        timer when a new radio event arrives. End result: arrow-key
        chatter through the group is instant, and the load only fires
        once the user settles on a choice for 200 ms.
        """
        is_kin = self._mode_is_kin()
        target_kind = "kin" if is_kin else "room"
        # Already in this mode — nothing to load.
        already = (target_kind == "kin" and self.current_room is None) or \
                  (target_kind == "room" and self.current_room is not None)
        if already:
            self._apply_mode_visibility()
            return

        # Fast path: paint mode swap NOW so the user gets immediate
        # feedback / NVDA can announce the new mode-radio state.
        self._apply_mode_visibility()
        self._set_status(
            "Switching to kin mode..." if target_kind == "kin"
            else "Switching to room mode..."
        )

        # Slow path: schedule the load. Cancel any pending one — if the
        # user keeps arrow-keying, only the last position fires.
        pending = getattr(self, "_mode_switch_timer", None)
        if pending is not None and pending.IsRunning():
            pending.Stop()
        self._mode_switch_timer = wx.CallLater(
            200, self._do_mode_switch_load, target_kind,
        )

    def _do_mode_switch_load(self, target_kind):
        """The deferred slow-path load for a mode switch. Runs ~200 ms
        after the user stops arrow-keying between mode radios. Re-checks
        the current radio state in case the user toggled again during
        the delay."""
        # Re-derive the target from the current radio state — in case
        # the user flipped during the delay window (the timer chain
        # would have cancelled, but defensively re-check).
        is_kin_now = self._mode_is_kin()
        if (target_kind == "kin") != is_kin_now:
            return  # state moved on; the newer timer will handle it
        if target_kind == "kin":
            agents = list_agents()
            last = self.config.get("last_agent", "")
            target = last if last in agents else (agents[0] if agents else None)
            if target:
                self._load_agent(target)
            else:
                self._exit_room_mode()
                self.current_agent = None
                self.chat_display.Clear()
                self.conversation = []
                self._set_status("No kin yet. Hit 'New kin...' in the header to create one.")
        else:
            rooms = list_rooms()
            last = self.config.get("last_room", "")
            target = last if last in rooms else (rooms[0] if rooms else None)
            if target:
                self._load_room(target)
            else:
                self.current_agent = None
                self.chat_display.Clear()
                self.conversation = []
                self._set_status("No rooms yet. Hit 'New room...' in the header to create one.")

    def _on_edit_kin(self, event):
        if not self.current_agent:
            self._set_status("No kin loaded.")
            return
        dlg = EditKinDialog(self, self.current_agent)
        dlg.ShowModal()
        dlg.Destroy()
        # The dialog auto-persists everything to disk as it edits; just
        # re-load the cfg into memory so subsequent reads see the new
        # model / params / etc.
        if self.current_agent:
            self.agent_cfg = load_agent_config(self.current_agent)
            self._active_model = strip_model_annotation(
                self.agent_cfg.get("model", "") or ""
            )
        self._update_token_display()

    def _current_chat_model_clean(self):
        """Return the kin's current chat model (cleaned of the
        '(no tools)' annotation). Reads from agent_cfg — the source of
        truth on disk. Used at chat send time, export, and logging.

        Previously this came from self.model_choice.GetValue(), but the
        widget moved to the Settings dialog (2026-05-13) so the frame
        no longer has a persistent model widget to read from."""
        if not self.current_agent or not self.agent_cfg:
            return ""
        return strip_model_annotation(self.agent_cfg.get("model", "") or "").strip()

    def _build_chat_tab(self, parent):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Header (mode radio + kin/room selector) now lives inside the
        # Chat tab rather than above the notebook. Keeps "talking
        # options" out of the Preferences and Usage tabs where they have
        # no business showing up. _build_header parents its widgets to
        # whatever panel we pass in, so this is a one-line move.
        header_sizer = self._build_header(parent)
        sizer.Add(header_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=6)

        # Activity line — what the model/system is doing right now.
        # Multi-line read-only because single-line read-only TextCtrls
        # on wxMSW sometimes get skipped by NVDA Tab navigation; the
        # multi-line variant is reliably treated as a focusable text
        # area. NVDA reads "Activity, read only edit, <value>" on
        # focus; phase transitions (Thinking, Typing, Still loading)
        # also speak via nvda_speak so the user doesn't have to tab
        # to find out — see _speak_status_phase.
        #
        # IMPORTANT: this block is created FIRST, before the
        # conversation widgets, even though it's "above" them in the
        # eventual sizer too. wxPython's tab order follows widget
        # CREATION order (Z-order), not sizer order, so reordering
        # via the sizer alone left tab traversal still going
        # transcript → Activity → input. Creation order is the source
        # of truth for keyboard navigation; the sizer is just visual.
        # See `git log -- hearthkin.pyw` for the regression that
        # taught us this.
        activity_label = wx.StaticText(parent, label="Acti&vity:")
        self.status_label = wx.TextCtrl(
            parent,
            value="Loading…",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.status_label.SetName("Activity")
        self.status_label.SetMinSize((-1, 48))

        conv_label = wx.StaticText(parent, label="Conversation:")
        # chat_display is created BEFORE load_older_btn so conv_label above
        # is the nearest preceding StaticText and wxMSW picks it up as the
        # accessible name. SetName() on a wx.TextCtrl is unreliable on
        # wxMSW — an earlier version of this file interleaved load_older_btn
        # between conv_label and chat_display and relied on SetName; NVDA
        # then announced the transcript (the main thing on screen) as a
        # bare unnamed "edit, read only". The buddy-label pattern is the
        # only one that works reliably here.
        self.chat_display = wx.TextCtrl(
            parent,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.HSCROLL | wx.TE_RICH2,
        )
        self.chat_display.SetName("Conversation")   # kept as belt-and-suspenders
        # "Load older messages" button — only visible when the current render
        # window doesn't cover the full conversation. Sits ABOVE chat_display
        # in the sizer (older messages are conceptually above newer ones), but
        # created AFTER chat_display so conv_label above stays adjacent to it
        # in creation order. Tab order: chat_display → load_older_btn, i.e.
        # you read the transcript first, then reach the button to expand.
        # _refresh_load_older_button toggles visibility based on _render_window
        # vs total turn count. See _on_load_older for the expand handler.
        self.load_older_btn = wx.Button(parent, label="Load &older messages")
        self.load_older_btn.Bind(wx.EVT_BUTTON, self._on_load_older)
        self.load_older_btn.Hide()
        # Reasoning style: gray italic — visually distinct from chat content
        self._reasoning_style = wx.TextAttr(
            colText=wx.Colour(140, 140, 140),
        )
        self.chat_display.SetMinSize((-1, 240))

        input_label = wx.StaticText(parent, label="Your message:")
        self.input_box = wx.TextCtrl(
            parent,
            style=wx.TE_MULTILINE,
            size=(-1, 90),
        )
        self.input_box.Bind(wx.EVT_KEY_DOWN, self._on_input_key)
        self.input_box.Bind(wx.EVT_TEXT, self._on_input_changed)

        # Both of these are read-only TextCtrls, not StaticText, so they
        # are in the Tab cycle — they carry the live send size against the
        # context cap, and which keys send. Each gets a buddy StaticText
        # created immediately before it, so wxMSW picks up an accessible
        # name; SetName() alone was announced as nothing.
        send_size_label = wx.StaticText(parent, label="Send size:")
        self.token_label = wx.TextCtrl(
            parent,
            value="≈ 0 tokens",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.token_label.SetName("Token usage")
        self.token_label.SetMinSize((-1, 24))
        keys_label = wx.StaticText(parent, label="Keys:")
        self.send_hint = wx.TextCtrl(
            parent,
            value=self._send_hint_text(),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.send_hint.SetName("Send and newline keys")
        self.send_hint.SetMinSize((-1, 24))

        token_row = wx.BoxSizer(wx.HORIZONTAL)
        token_row.Add(send_size_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        token_row.Add(self.token_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=12)
        token_row.Add(keys_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        token_row.Add(self.send_hint, flag=wx.ALIGN_CENTER_VERTICAL)

        # Image-attachment row. Sits between the input box and the
        # main button row so tabbing through after typing naturally
        # lands on Attach before Send. Visibility tracks model
        # capability — see _refresh_attach_button_state. The staged
        # label + Clear button are hidden until an image is staged;
        # tab order picks them up only when relevant.
        self.attach_btn = wx.Button(parent, label="Attach &image…")
        self.attach_btn.Bind(wx.EVT_BUTTON, self._on_attach_image)
        self.attach_btn.Disable()
        # "Take photo" — capture from the host webcam without going
        # through the file picker. Counts down 3-2-1 (with NVDA
        # speech), captures on a worker thread, stages the result as
        # if it'd been picked from disk. Enabled state mirrors the
        # Attach button (both need a vision-capable model).
        # Mnemonic: Alt+A. NOT Alt+T (which conflicts with the
        # Tools menu — menu wins, the button mnemonic would be dead).
        # Alt+A is shared with "&Auto-continue rounds" but that
        # checkbox is room-mode-only and this button is 1-on-1-only,
        # so mutually exclusive visibility means no real collision.
        self.take_photo_btn = wx.Button(parent, label="T&ake photo")
        self.take_photo_btn.Bind(wx.EVT_BUTTON, self._on_take_photo)
        self.take_photo_btn.Disable()
        self.attached_label = wx.TextCtrl(
            parent,
            style=wx.TE_READONLY | wx.TE_NO_VSCROLL,
        )
        self.attached_label.SetName("Staged image attachment")
        self.attached_label.Hide()
        # Mnemonic note: was C&lear image (Alt+L), which collided
        # with C&lear chat in the row below. "Re&move image" (Alt+M)
        # is unique in the chat tab.
        self.clear_attach_btn = wx.Button(parent, label="Re&move image")
        self.clear_attach_btn.Bind(wx.EVT_BUTTON, self._on_clear_attachment)
        self.clear_attach_btn.Hide()
        attach_row = wx.BoxSizer(wx.HORIZONTAL)
        attach_row.Add(self.attach_btn,
                       flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        attach_row.Add(self.take_photo_btn,
                       flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        attach_row.Add(self.attached_label, proportion=1,
                       flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        attach_row.Add(self.clear_attach_btn,
                       flag=wx.ALIGN_CENTER_VERTICAL)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(parent, label="&Send")
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)
        self.stop_btn = wx.Button(parent, label="Sto&p")
        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        self.stop_btn.Disable()
        # Mnemonic note: was C&ontinue round (Alt+O), which
        # collided with Load &older messages — both visible in
        # room mode. Alt+N is free in room mode (the &New kin
        # header button is hidden in room mode).
        self.continue_btn = wx.Button(parent, label="Co&ntinue round")
        self.continue_btn.Bind(wx.EVT_BUTTON, self._on_continue)
        self.continue_btn.Hide()
        # Mnemonic is Alt+G. It was moved off Alt+R to protect the
        # Talk-in-a-&room radio — which turned out to be a mnemonic the
        # menubar's "&Room" had been swallowing all along, so it was never
        # reachable to collide with. Alt+G is free and works; leaving it
        # alone. (The radio's ampersand is gone now; see _build_header.)
        self.regen_btn = wx.Button(parent, label="Re&generate")
        self.regen_btn.Bind(wx.EVT_BUTTON, self._on_regen)
        self.clear_btn = wx.Button(parent, label="C&lear chat")
        self.clear_btn.Bind(wx.EVT_BUTTON, self._on_clear)
        # Click-to-toggle dictation. Click once to start recording
        # (label flips to "Stop talking"), click again to stop and
        # transcribe. Click-toggle (rather than hold-to-talk) avoids
        # the focus-loss-stops-recording fragility that mouse-hold
        # patterns have.
        #
        # Shown whenever dictation can actually work — see
        # _refresh_talk_button_visibility. It used to be shown only when
        # the kin had a paid text-to-speech voice picked, which tied
        # speaking TO a kin to that kin speaking BACK: two unrelated
        # things, and it put putting your own words in the box behind a
        # subscription. Speech recognition now runs on this machine by
        # default, free and offline.
        #
        # No mnemonic. This was "&Talk" (Alt+T) — dead, swallowed by the
        # "&Tools" menu, which is precisely what the Take photo comment 50
        # lines above warns against ("NOT Alt+T ... menu wins, the button
        # mnemonic would be dead"). The rule was written down and then
        # broken in the same file.
        #
        # Not re-homed: every letter in "Talk" is taken (T/&Tools menu,
        # A/Take photo, L/C&lear chat, K/&Kin menu), so a working mnemonic
        # would mean renaming the button, and "Talk" is the deliberate
        # word — warmer than "Dictate", and it's what the kin does. If a
        # voice shortcut is wanted later, that's a label decision to make
        # on purpose, not a letter to smuggle in here.
        self.talk_btn = wx.Button(parent, label="Talk")
        self.talk_btn.Bind(wx.EVT_BUTTON, self._on_talk)
        self.talk_btn.Hide()
        self._is_recording = False
        btn_row.Add(self.send_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.stop_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.continue_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.regen_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.clear_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(self.talk_btn)

        room_row = wx.BoxSizer(wx.HORIZONTAL)
        self.auto_check = wx.CheckBox(parent, label="&Auto-continue rounds")
        self.auto_check.Bind(wx.EVT_CHECKBOX, self._on_auto_toggle)
        self.auto_check.Hide()
        # Read-only TextCtrl, not StaticText, so it is in the Tab cycle when
        # visible. Buddy label "Rounds:" created immediately before it so
        # NVDA announces something sensible ("auto: 2/8" etc.) when a room is
        # active. Hidden in kin mode alongside auto_check — otherwise it sat
        # in the tab cycle as a blank, unnamed field with no purpose, which
        # is what an NVDA user's transcript surfaced.
        rounds_label = wx.StaticText(parent, label="Rounds:")
        self.round_label = wx.TextCtrl(
            parent,
            value="",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.round_label.SetName("Room round counter")
        self.round_label.SetMinSize((-1, 24))
        self.round_label.Hide()
        rounds_label.Hide()
        self._rounds_label = rounds_label   # kept so room enter/exit can toggle it
        room_row.Add(self.auto_check, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=12)
        room_row.Add(rounds_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        room_row.Add(self.round_label, flag=wx.ALIGN_CENTER_VERTICAL)

        # Activity sits right after the header — at the top of the
        # chat panel. Then transcript → input → buttons flows as one
        # uninterrupted unit below it.
        sizer.Add(activity_label, flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(self.status_label, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=6)
        sizer.Add(conv_label, flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(self.load_older_btn,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(self.chat_display, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=6)
        sizer.Add(input_label, flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(self.input_box, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=6)
        sizer.Add(attach_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(token_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(btn_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        sizer.Add(room_row, flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)

        parent.SetSizer(sizer)
