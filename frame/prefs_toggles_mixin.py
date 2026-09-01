"""PrefsTogglesMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    DictationSettingsDialog,
    SoundCuesDialog,
    CONFIG_FILE, _ensure_foreground_lock_disabled, atomic_write_json, nvda_speak,
    nvda_status, play_alert, play_chime, windows_startup, wx,
)


class PrefsTogglesMixin:

    # --- Toggles --- #

    def _on_log_toggle(self, event):
        self.config["logging_enabled"] = self.log_check.GetValue()
        atomic_write_json(CONFIG_FILE, self.config)
        self._setup_logging()
        status = "on" if self.config["logging_enabled"] else "off"
        self._set_status(f"Logging turned {status}.")

    def _on_user_name_changed(self, event):
        """Save the operator's display name to app config. Fires on
        EVT_KILL_FOCUS (Tab away) or EVT_TEXT_ENTER (Enter pressed
        in the field). Empty / whitespace-only values become the
        empty string — the room build path treats that as "no
        attribution prefix" (same shape as the empty-default
        behavior)."""
        new_val = self.user_name_field.GetValue().strip()
        current = (self.config.get("user_name", "") or "").strip()
        # Only write to disk on actual change — avoids redundant
        # config rewrites on every focus traversal.
        if new_val != current:
            self.config["user_name"] = new_val
            try:
                atomic_write_json(CONFIG_FILE, self.config)
            except Exception:
                pass
            if new_val:
                self._set_status(f"Your name set to: {new_val}")
            else:
                self._set_status("Your name cleared — no name tag in rooms.")
        # Skip on EVT_KILL_FOCUS so the focus move proceeds. Harmless
        # for EVT_TEXT_ENTER (no default behavior to suppress).
        if event is not None:
            try:
                event.Skip()
            except Exception:
                pass

    def _on_nvda_mode(self, event):
        idx = self.nvda_choice.GetSelection()
        mode = ["off", "short", "full", "stream"][idx if idx >= 0 else 0]
        self.config["nvda_mode"] = mode
        atomic_write_json(CONFIG_FILE, self.config)
        self._set_status(f"NVDA reading: {mode}.")
        if mode != "off":
            nvda_speak(f"Reading mode: {mode}")

    def _on_test_nvda_speech(self, event):
        """Speak a fixed phrase via the NVDA controller DLL.

        Three outcomes:
          - DLL not loaded: button is disabled at build time, this method
            won't be reachable normally — but guard anyway.
          - DLL loaded, NVDA running: user hears "Hearthkin NVDA speech test."
          - DLL loaded, NVDA NOT running: speech call is a silent no-op
            inside the DLL itself; status field shows the hint to start NVDA.
        """
        loaded, msg, path = nvda_status()
        if not loaded:
            self._set_status("NVDA controller DLL not loaded.")
            return
        nvda_speak("Hearthkin NVDA speech test.")
        # Update status to confirm the call went out — actual audibility
        # depends on whether NVDA is running, which we can't probe from
        # this side of the API.
        self._set_status(
            "Sent test speech to NVDA. If you didn't hear anything, "
            "make sure NVDA is running."
        )

    def _on_sound_cues(self, _event):
        """Open the per-cue sound settings.

        The single "chime on reply" tick and one volume could not express what
        was actually needed: cues that fire on every surface rather than only
        the kin on screen, a repeating still-working signal for waits measured
        in minutes, and separate volumes because "finished" wants to carry
        across a room while a repeating tick must not.
        """
        try:
            dlg = SoundCuesDialog(
                self, self.config,
                lambda: atomic_write_json(CONFIG_FILE, self.config))
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            self._set_status(f"Couldn't open sound cues: {e}")

    def _on_dictation_settings(self, _event):
        """Open the dictation settings.

        Dictation used to be reachable only if the kin you were talking
        to had a paid text-to-speech voice configured — two unrelated
        things tied together, which put speaking to a kin behind a
        subscription. It now runs on this machine by default, for free,
        and these are its settings.
        """
        try:
            dlg = DictationSettingsDialog(
                self, self.config,
                lambda: atomic_write_json(CONFIG_FILE, self.config))
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            self._set_status(f"Couldn't open dictation settings: {e}")

    def _on_chime_toggle(self, event):
        self.config["reply_chime"] = self.chime_check.GetValue()
        atomic_write_json(CONFIG_FILE, self.config)
        if self.config["reply_chime"]:
            # Test tone uses current volume so the user can hear what they'll get.
            vol = float(self.config.get("chime_volume", 0.8) or 0.0)
            play_chime(volume=vol)
        self._set_status(f"Reply chime {'on' if self.config['reply_chime'] else 'off'}.")

    def _on_chime_volume(self, event):
        pct = self.chime_vol_slider.GetValue()
        self.chime_vol_display.SetLabel(f"{pct}%")
        self.config["chime_volume"] = pct / 100.0
        atomic_write_json(CONFIG_FILE, self.config)
        # Play a test tone at the new volume so the user can hear it. Skip the
        # test if they've slid all the way to 0 (silence is silent).
        if pct > 0:
            play_chime(volume=self.config["chime_volume"])

    def _on_approval_alert_toggle(self, event):
        self.config["approval_alert"] = self.approval_alert_check.GetValue()
        atomic_write_json(CONFIG_FILE, self.config)
        if self.config["approval_alert"]:
            # Play it so the operator learns what the cue sounds like.
            try:
                vol = float(self.config.get("chime_volume", 0.8) or 0.0)
            except (TypeError, ValueError):
                vol = 0.8
            if vol > 0:
                play_alert(volume=vol)
        self._set_status(
            f"Approval alert {'on' if self.config['approval_alert'] else 'off'}.")

    def _on_problem_alert_toggle(self, event):
        self.config["problem_alert"] = self.problem_alert_check.GetValue()
        atomic_write_json(CONFIG_FILE, self.config)
        if self.config["problem_alert"]:
            # Play it so they learn what the cue sounds like — and, more to
            # the point, hear that it falls where the approval alert rises.
            try:
                vol = float(self.config.get("chime_volume", 0.8) or 0.0)
            except (TypeError, ValueError):
                vol = 0.8
            if vol > 0:
                play_alert(volume=vol, name="problem")
        self._set_status(
            f"Problem alert {'on' if self.config['problem_alert'] else 'off'}.")

    def _on_open_sounds_folder(self, _event):
        """Open ~/.hearthkin/sounds/ so the operator can drop in custom WAVs.
        Creates it first (empty until they add files — a file always wins
        over the built-in tone)."""
        import os
        from audio import sounds_dir
        d = sounds_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        try:
            os.startfile(d)  # Windows file explorer
            self._set_status("Opened your sounds folder.")
        except Exception:
            # No GUI file manager (or non-Windows) — just tell them the path.
            self._set_status(f"Sounds folder: {d}")
            try:
                nvda_speak(f"Your sounds folder is at {d}")
            except Exception:
                pass

    def _on_enter_toggle(self, event):
        self.config["enter_sends"] = self.enter_check.GetValue()
        atomic_write_json(CONFIG_FILE, self.config)
        self.send_hint.SetValue(self._send_hint_text())
        self._set_status("Send key updated.")

    def _on_warn_model_swap_toggle(self, event):
        self.config["warn_on_model_swap"] = self.warn_model_swap_check.GetValue()
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        if self.config["warn_on_model_swap"]:
            self._set_status("Model-swap warning re-enabled.")
        else:
            self._set_status("Model-swap warning suppressed.")

    def _on_telegram_cmd_menu_toggle(self, _event):
        self.config["telegram_command_menu"] = self.telegram_cmd_menu_check.GetValue()
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        if self.config["telegram_command_menu"]:
            self._set_status(
                "Telegram command menu will be registered on next bot start."
            )
        else:
            self._set_status(
                "Telegram command menu OFF — restart Hearthkin to clear it, then "
                "reopen/restart your Telegram client (Unigram caches the old menu)."
            )

    def _on_auto_update_toggle(self, _event):
        val = bool(self.auto_update_check.GetValue())
        self.config["auto_check_updates_on_startup"] = val
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        msg = (
            "Auto-update check on startup is on. Hearthkin will quietly "
            "check GitHub Releases at launch and announce only if a "
            "newer version is available."
            if val else
            "Auto-update check on startup is off. Use Help → Check for "
            "updates to check manually."
        )
        self._set_status(msg)

    def _on_close_to_tray_toggle(self, _event):
        val = bool(self.tray_close_check.GetValue())
        self.config["close_to_tray"] = val
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        if val:
            self._set_status(
                "Close-to-tray on: Alt+F4 will hide Hearthkin into "
                "the system tray. Use the tray menu's Exit to fully quit."
            )
        else:
            self._set_status(
                "Close-to-tray off: Alt+F4 / closing the window will exit Hearthkin."
            )

    def _on_foreground_lock_toggle(self, _event):
        val = bool(self.foreground_lock_check.GetValue())
        self.config["manage_foreground_lock"] = val
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        if val:
            # Apply immediately so the user doesn't have to wait for the
            # next launch. (Still needs a sign-out/restart to take full
            # effect — Windows reads the value at session start.)
            try:
                _ensure_foreground_lock_disabled()
            except Exception:
                pass
            self._set_status(
                "Window focus management on: Hearthkin will keep its "
                "window reliably focusable. Takes full effect after your "
                "next sign-in or restart."
            )
        else:
            self._set_status(
                "Window focus management off: Hearthkin won't adjust "
                "Windows' foreground setting. (Any change already made "
                "stays until you reset it.)"
            )

    def _on_start_with_windows_toggle(self, _event):
        val = bool(self.start_with_windows_check.GetValue())
        # Sync the registry. Disable the checkbox briefly during the
        # call so a rapid toggle can't race; re-read state from the
        # registry afterward to confirm the change actually took (the
        # registry is the source of truth, our cached config value is
        # only a UI echo).
        self.start_with_windows_check.Disable()
        try:
            if val:
                windows_startup.enable()
                self._set_status(
                    "Hearthkin will launch when Windows starts."
                )
            else:
                windows_startup.disable()
                self._set_status(
                    "Hearthkin will no longer launch when Windows starts."
                )
            self.config["start_with_windows"] = val
            try:
                atomic_write_json(CONFIG_FILE, self.config)
            except Exception:
                pass
        except Exception as e:
            wx.MessageBox(
                f"Couldn't change the Windows startup setting:\n\n{e}",
                "Hearthkin",
                wx.OK | wx.ICON_ERROR,
            )
            # Re-read actual state into the checkbox so it doesn't
            # lie about what's registered.
            try:
                self.start_with_windows_check.SetValue(
                    windows_startup.is_enabled()
                )
            except Exception:
                pass
        finally:
            try:
                if windows_startup.is_supported():
                    self.start_with_windows_check.Enable()
            except Exception:
                pass

    def _on_chat_history_window_changed(self, value):
        """Save the new chat-history-window preference and apply it to
        the currently-loaded kin immediately. Setting 0 means "render
        everything"; otherwise the value is the most-recent-N count.

        We re-render right away so the user sees the change without
        having to switch kins. If they shrunk the window, older
        rendered messages fall off and the Load Older button appears;
        if they grew the window past current total, the button hides.
        """
        self.config["chat_history_window"] = int(value)
        try:
            atomic_write_json(CONFIG_FILE, self.config)
        except Exception:
            pass
        if not self.current_agent or self.current_room is not None:
            self._set_status(f"Chat history window set to {value}.")
            return
        total = len(self.conversation)
        if value <= 0:
            self._render_window = total
        else:
            self._render_window = min(value, total)
        self._render_conversation()
        self._set_status(f"Chat history window set to {value}.")
