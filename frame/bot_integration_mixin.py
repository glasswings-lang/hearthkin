"""BotIntegrationMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    DiscordBot, TelegramBot, load_agent_config, load_memory,
    load_memory_for_prompt, load_soul,
    nvda_speak, play_alert, strip_model_annotation, wx,
)


class BotIntegrationMixin:

    # --- Telegram --- #

    def _start_bot_for(self, agent_name):
        if not agent_name:
            return
        bot = self.bots.get(agent_name)
        if bot is None:
            bot = TelegramBot(
                agent_name=agent_name,
                get_config=lambda an=agent_name: load_agent_config(an).get("telegram", {}),
                get_soul=lambda an=agent_name: load_soul(an),
                # for_prompt: the bot reaches a kin without ever going through
                # the desktop send path, which was the only caller that
                # rebuilt the depth-log index. A kin talked to mostly here had
                # its own logs quietly drop out of its memory.
                get_memory=lambda an=agent_name: load_memory_for_prompt(an),
                get_model_options=lambda an=agent_name: self._model_options_for(an),
                on_status=lambda label, an=agent_name: wx.CallAfter(self._on_bot_status, an, label),
                # "What has the model right now?" — the same question the
                # desktop Activity line answers, asked from the bot's POLL
                # thread so it can be answered while the inference thread is
                # inside a model call. Called directly rather than through
                # wx.CallAfter because the poll thread needs the answer NOW to
                # decide whether to speak; it is only dict reads, and it
                # returns "" on anything unexpected.
                get_busy_label=lambda skip_bot=None: (
                    self._own_background_on_the_model(
                        include_foreground=True, skip_bot=skip_bot)),
                # Telegram-surface activity hook: the bot fires this
                # after persisting each user/group turn so the frame
                # can tick the right per-(kin, scope) distillation
                # counter. Shared surfaces tick "desktop"; non-shared
                # surfaces tick their own scope. Routed through
                # wx.CallAfter to keep the maybe-distill decision on
                # the UI thread (and its access to load_agent_config
                # / self.bots / etc.). Without this, Telegram-side
                # activity never triggered distillation — group
                # conversations could run for days without anything
                # reaching memory.md.
                on_activity=lambda kind, ident, an=agent_name: wx.CallAfter(
                    self._on_telegram_activity, an, kind, ident
                ),
                # Webcam approval callback. The bot's executor wrap
                # consults the user's per-user webcam_permission
                # ("ask" / "auto" / "deny") and, when it's "ask",
                # calls this synchronously from the worker thread.
                # The frame's helper marshals the dialog onto the UI
                # thread, blocks the worker on a threading.Event,
                # returns "allow" / "deny". Worker dispatches the
                # capture or refuses based on the result.
                request_webcam_approval=lambda label, uid, an=agent_name:
                    self._request_webcam_approval(an, label, uid),
                # Remote approval is about to block on the operator. The
                # Telegram-side prompt is invisible if they're looking at
                # another kin, so raise it on the desktop too.
                on_approval_needed=lambda kin, cmd, secs: wx.CallAfter(
                    self._notify_remote_approval, kin, cmd, secs
                ),
            )
            self.bots[agent_name] = bot
        bot.start()

    def _play_approval_alert(self):
        """The audible half of an approval alert. Distinct two-tone cue,
        gated on the app-level `approval_alert` toggle (default on), loudness
        from `chime_volume`. Safe to call from any thread. Deliberately
        independent of `reply_chime`: this is a safety signal, and an
        operator who keeps reply chimes off still needs to hear that a kin is
        waiting on them — NVDA speech can be swallowed by their own typing.
        """
        try:
            if not self.config.get("approval_alert", True):
                return
            try:
                vol = float(self.config.get("chime_volume", 0.8) or 0.0)
            except (TypeError, ValueError):
                vol = 0.8
            if vol > 0:
                play_alert(volume=vol)
        except Exception:
            pass

    def _notify_remote_approval(self, kin_name, command, timeout_secs):
        """A kin on a remote surface is blocked waiting for the operator to
        approve a command. Say so on the desktop.

        Runs on the UI thread (wx.CallAfter from the bot's worker). The
        remote surface still posts its own prompt in-chat and still owns the
        decision — this only makes the wait VISIBLE. Before it, an operator
        working in another kin's window had no signal at all that anything
        was waiting on them, so requests aged out and the kin reported the
        timeout to them as a refusal they'd never made.

        Speech first, toast second: the operator uses NVDA, and a Windows
        toast alone is easy to miss entirely.
        """
        try:
            mins = max(1, int(timeout_secs) // 60)
        except Exception:
            mins = 10
        short = str(command or "").strip().replace("\n", " ")
        if len(short) > 90:
            short = short[:90] + "…"
        spoken = (f"{kin_name} is asking to run a command on Telegram, "
                  f"and is waiting for you. {mins} minutes to answer.")
        # Sound FIRST — it's the signal most likely to survive the operator
        # typing over NVDA or not looking at the screen.
        self._play_approval_alert()
        try:
            self._set_status(f"{kin_name} is waiting for approval: {short}")
        except Exception:
            pass
        try:
            nvda_speak(spoken)
        except Exception:
            pass
        try:
            notif = wx.adv.NotificationMessage(
                title=f"{kin_name} needs approval",
                message=(f"{kin_name} wants to run:\n{short}\n\n"
                         f"Answer in the Telegram chat (yes / no / remember). "
                         f"It gives up after {mins} min — that is not a refusal."),
                parent=None,
            )
            notif.Show(timeout=15)
        except Exception:
            pass

    def _on_telegram_activity(self, agent_name, kind, identifier):
        """Telegram bot persisted a turn — figure out which
        distillation scope it belongs to, tick that counter, and
        maybe trigger distillation. `kind` is "user" (DM) or
        "group"; `identifier` is the user_id or chat_id."""
        # Skip mid-load — _maybe_auto_distill reads cfg / conversation
        # the load is still rewriting, and the load path will fold any
        # pending tick into its own counters on completion (audit H18).
        if getattr(self, "_loading_agent", False):
            return
        # Guard against malformed identifiers (None, dict) that
        # would stringify into a phantom scope key like "None"
        # or "{...}" (audit H24). int and str are the only shapes
        # _distill_scope_for_telegram_* know how to handle.
        if not isinstance(identifier, (int, str)):
            return
        if kind == "user":
            scope_key = self._distill_scope_for_telegram_user(agent_name, identifier)
        elif kind == "group":
            scope_key = self._distill_scope_for_telegram_group(agent_name, identifier)
        else:
            return
        key = (agent_name, scope_key)
        self._messages_since_distill[key] = (
            self._messages_since_distill.get(key, 0) + 1
        )
        # Refresh the Chat tab's per-surface counter display if the
        # dialog is open — gives visible feedback that cross-surface
        # ticks are landing.
        dlg = self._dialog_for(agent_name)
        if dlg is not None:
            try:
                dlg._refresh_chat_counters_display()
            except Exception:
                pass
        try:
            self._maybe_auto_distill(agent_name, scope_key=scope_key)
        except Exception:
            pass

    def _stop_bot_for(self, agent_name):
        bot = self.bots.get(agent_name)
        if bot is not None:
            bot.stop()

    # --- Discord --- #

    def _start_discord_bot_for(self, agent_name):
        if not agent_name:
            return
        bot = self.discord_bots.get(agent_name)
        if bot is None:
            # get_config returns the FULL kin config (Discord needs model,
            # num_ctx, ollama_host_name AND its own discord sub-dict) —
            # unlike the Telegram bot, which is handed only its sub-dict.
            bot = DiscordBot(
                agent_name=agent_name,
                get_config=lambda an=agent_name: load_agent_config(an),
                get_soul=lambda an=agent_name: load_soul(an),
                # Same reason as the Telegram bot above.
                get_memory=lambda an=agent_name: load_memory_for_prompt(an),
                get_model_options=lambda an=agent_name: self._model_options_for(an),
                on_status=lambda label, an=agent_name: wx.CallAfter(
                    self._on_discord_bot_status, an, label),
                # Fires after each handled mention so Discord activity
                # ticks the right per-(kin, scope) distillation counter —
                # so Discord conversations become long-term memory too,
                # exactly like Telegram. ident is the channel_id.
                on_activity=lambda kind, ident, an=agent_name: wx.CallAfter(
                    self._on_discord_activity, an, ident),
                # Routes exec approval to THIS desktop (wx dialog), never
                # to the Discord server — a member can trigger a command,
                # but only the operator can approve it running. The REMOTE
                # wrapper (not the desktop one) is used deliberately: it
                # denylist-hard-denies, uses a Discord-scoped remembered-
                # approval list, and never lets tool_trust=trusted/full
                # auto-run a command that came in over Discord.
                wrap_exec=lambda ex, an: self._wrap_exec_for_remote(
                    ex, an, "discord"),
                # Webcam approval, same shape as Telegram's: the dialog pops
                # on THIS desktop. Discord has no per-user ask/auto/deny
                # radio, so the bot always asks — a missing setting must read
                # as "ask", never as "auto".
                request_webcam_approval=lambda label, uid, an=agent_name:
                    self._request_webcam_approval(an, label, uid),
            )
            self.discord_bots[agent_name] = bot
        bot.start()

    def _stop_discord_bot_for(self, agent_name):
        bot = self.discord_bots.get(agent_name)
        if bot is not None:
            bot.stop()

    def _on_discord_bot_status(self, agent_name, label):
        dlg = self._dialog_for(agent_name)
        if dlg is not None and hasattr(dlg, "dc_status_label"):
            try:
                dlg.dc_status_label.SetLabel(f"Status: {label}")
            except Exception:
                pass

    def _distill_scope_for_discord(self, agent_name, channel_id):
        """Shared Discord history rolls into the unified "desktop" scope;
        non-shared channels each get their own "discord:<channel_id>"
        scope. Mirrors the Telegram group variant."""
        cfg = load_agent_config(agent_name) or {}
        share = bool((cfg.get("discord") or {}).get("share_desktop", False))
        return "desktop" if share else f"discord:{channel_id}"

    def _on_discord_activity(self, agent_name, channel_id):
        """A Discord mention was handled — tick that scope's distillation
        counter and maybe fire distillation, same as _on_telegram_activity."""
        if getattr(self, "_loading_agent", False):
            return
        if not isinstance(channel_id, (int, str)):
            return
        scope_key = self._distill_scope_for_discord(agent_name, channel_id)
        key = (agent_name, scope_key)
        self._messages_since_distill[key] = (
            self._messages_since_distill.get(key, 0) + 1
        )
        dlg = self._dialog_for(agent_name)
        if dlg is not None:
            try:
                dlg._refresh_chat_counters_display()
            except Exception:
                pass
        try:
            self._maybe_auto_distill(agent_name, scope_key=scope_key)
        except Exception:
            pass

    def _model_options_for(self, agent_name):
        cfg = load_agent_config(agent_name)
        model = strip_model_annotation(cfg.get("model", "qwen2.5:7b-instruct"))
        options = {
            "temperature": cfg.get("temperature", 0.8),
            "top_p": cfg.get("top_p", 0.9),
            "top_k": cfg.get("top_k", 40),
            "min_p": cfg.get("min_p", 0.0),
            "repeat_penalty": cfg.get("repeat_penalty", 1.1),
            "presence_penalty": cfg.get("presence_penalty", 0.0),
            "frequency_penalty": cfg.get("frequency_penalty", 0.0),
            "num_ctx": cfg.get("num_ctx", 2048),
            # Circuit breaker on reply length. Without this, a cascade in the
            # Telegram surface had no upper bound — it'd generate until it hit
            # the context ceiling, which for big-ctx models (1M on MiMo) was
            # effectively never. See DEFAULT_AGENT_CONFIG's telegram_token_cap
            # comment for the full diagnosis. 900 is the safe fallback for
            # configs predating this field.
            "num_predict": cfg.get("telegram_token_cap", 900),
        }
        return model, options

    def _on_bot_status(self, agent_name, label):
        dlg = self._dialog_for(agent_name)
        if dlg is not None:
            dlg.tg_status_label.SetLabel(f"Status: {label}")
