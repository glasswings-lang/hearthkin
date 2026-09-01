# SPDX-License-Identifier: CC0-1.0

"""dialogs.park_play — ParkPlayDialog: tend a kin's Time for Family park
from inside Hearthkin, taking turns in the kin's OWN save.

A kin plays its park through the `tff` tool; this dialog gives the
operator hands in the *same* save file, so the two share one world and
take turns in it — what you do here the kin sees next time it looks, and
vice versa. Every turn (the kin's tool calls, a cron wake-up in its own
subprocess, and this dialog) routes through `GameHost.run`, which
serializes the load-act-save with a cross-process file lock, so a turn
taken here and a turn the kin fires can't overwrite each other.

The dialog never talks to the game directly — it calls the same `tff`
tool the kin uses, with the chosen kin's name, so there is exactly one
code path (and one lock) into the park.
"""

import threading

import wx

from audio import nvda_speak
from kin_persistence import list_agents


class ParkPlayDialog(wx.Dialog):
    def __init__(self, parent, active_kin=""):
        super().__init__(parent, title="Tend a kin's park",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(700, 660))
        self._busy = False
        self._kin_timer = None  # debounce for the whose-park picker
        kin_names = list_agents()

        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.TextCtrl(
            self,
            value=(
                "You're taking turns in a kin's own Time for Family park — "
                "the same world the kin plays through its tff tool, not a "
                "copy. Pick whose park below, type a command like 'look', "
                "'dig 50', 'adopt cat', or 'go to <room>', and press "
                "Send (or Enter). Whatever you do here the kin sees next "
                "time it looks; whatever the kin does, you'll see when you "
                "look. Turns are serialized behind the scenes, so you and a "
                "scheduled wake-up can't overwrite each other's turn."
            ),
            style=(wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
                   | wx.TE_WORDWRAP),
        )
        header.SetName("Park play explainer")
        header.SetMinSize((-1, 110))
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        # ─── Whose park ──────────────────────────────────────────
        pick_row = wx.BoxSizer(wx.HORIZONTAL)
        pick_row.Add(wx.StaticText(self, label="&Whose park:"), 0,
                     wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.kin_choice = wx.Choice(self, choices=kin_names)
        self.kin_choice.SetName("Whose park")
        if active_kin and active_kin in kin_names:
            self.kin_choice.SetStringSelection(active_kin)
        elif kin_names:
            self.kin_choice.SetSelection(0)
        self.kin_choice.Bind(wx.EVT_CHOICE, self._on_kin_changed)
        pick_row.Add(self.kin_choice, 1, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(pick_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # ─── Park narration (read-only, arrow-through) ───────────
        outer.Add(wx.StaticText(self, label="Park:"), 0, wx.LEFT | wx.RIGHT, 8)
        self.output = wx.TextCtrl(
            self, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.output.SetName("Park narration")
        outer.Add(self.output, 1, wx.EXPAND | wx.ALL, 8)

        # ─── Your own words (optional) ───────────────────────────
        # A kin narrates before its `> command` and that prose reaches you as
        # its chat reply — so kin could be characters in the park and the
        # operator could not: this dialog only ever accepted a bare command.
        # Whatever goes here rides into the shared feed alongside the move, the
        # same way tff_server carries a carer's `say`, so anyone else tending
        # this park reads your words and not just the mechanics.
        outer.Add(wx.StaticText(self, label="&Your words (optional):"),
                  0, wx.LEFT | wx.RIGHT, 8)
        self.say = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.say.SetName("Your words")
        self.say.SetMinSize((-1, 56))
        outer.Add(self.say, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # ─── Command entry ───────────────────────────────────────
        cmd_row = wx.BoxSizer(wx.HORIZONTAL)
        cmd_row.Add(wx.StaticText(self, label="&Command:"), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.cmd = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.cmd.SetName("Park command")
        self.cmd.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        cmd_row.Add(self.cmd, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.send_btn = wx.Button(self, label="&Send")
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)
        cmd_row.Add(self.send_btn, 0)
        outer.Add(cmd_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # ─── Bottom row ──────────────────────────────────────────
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        look_btn = wx.Button(self, label="&Look (refresh)")
        look_btn.Bind(wx.EVT_BUTTON, self._on_look)
        btn_row.Add(look_btn, 0, wx.RIGHT, 6)
        btn_row.AddStretchSpacer()
        close_btn = wx.Button(self, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.Add(close_btn, 0)
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(outer)
        # End the modal loop on Escape / the window's X / the Close button.
        # A plain self.Close() leaves ShowModal's loop running (the dialog
        # won't actually dismiss), so route every close through EndModal.
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.cmd.SetFocus()

        # Show the park as it stands the moment the dialog opens — but don't
        # move focus off the command field where we just put it.
        if kin_names:
            self._run_command("look", move_focus=False)

    # ----- helpers ----------------------------------------------------- #

    def _current_kin(self):
        return self.kin_choice.GetStringSelection()

    def _append(self, text):
        self.output.AppendText(text)

    def _set_busy(self, busy):
        # NOTE: deliberately does NOT disable the whose-park picker. Disabling
        # the control that currently has focus yanks focus elsewhere — the
        # exact accessibility jump we're avoiding. The self._busy flag alone
        # guards against overlapping commands.
        self._busy = busy
        self.send_btn.Enable(not busy)
        self.cmd.Enable(not busy)

    # ----- events ------------------------------------------------------ #

    def _on_kin_changed(self, event):
        # Arrowing through the dropdown fires EVT_CHOICE once per step. Debounce
        # so we only look up the park once the selection settles — and never
        # steal focus off the picker while the user is still choosing (mirrors
        # how the main window debounces its kin/mode switch).
        if self._kin_timer is not None and self._kin_timer.IsRunning():
            self._kin_timer.Stop()
        self._kin_timer = wx.CallLater(250, self._do_kin_switch)

    def _do_kin_switch(self):
        kin = self._current_kin()
        if not kin:
            return
        self._append(f"\n— now tending {kin}'s park —\n")
        # Browsing the picker must NOT read the whole park aloud — that dumped a
        # wall of narration over the user mid-arrow. Refresh the view quietly and
        # speak only a short confirmation; the full narration waits for an
        # explicit Look/Send.
        self._run_command("look", move_focus=False, speak=False)
        nvda_speak(f"{kin}'s park")

    def _on_send(self, event):
        text = self.cmd.GetValue().strip()
        if not text:
            return
        self.cmd.SetValue("")
        self._run_command(text)

    def _on_look(self, event):
        self._run_command("look")

    def _on_close(self, event):
        # Handles the Close button (CommandEvent), Escape (via SetEscapeId),
        # and the window's X (EVT_CLOSE) — all end the modal loop.
        if self._kin_timer is not None and self._kin_timer.IsRunning():
            self._kin_timer.Stop()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()

    # ----- the one call into the park --------------------------------- #

    def _run_command(self, command, move_focus=True, speak=True):
        kin = self._current_kin()
        if not kin or self._busy:
            return
        self._set_busy(True)
        say = ""
        try:
            say = (self.say.GetValue() or "").strip()
        except Exception:
            say = ""
        if say:
            self._append("\n" + say + "\n")
        self._append(f"\n> {command}\n")

        def worker():
            try:
                # Same host the `tff` tool uses — the lock lives in
                # GameHost.run, so this shares the kin's world safely. Called
                # directly rather than through tff() only because the tool's
                # signature is model-facing and `say` is the operator's.
                from tools import get_game
                host = get_game("tff")
                # decorate(), not bare run() — so this window gets what every
                # kin surface already gets: what the OTHER tenants have done
                # since you last looked, and (on a look) one thing worth doing.
                #
                # This was the exact inverse of the bug that made GameHost
                # announce a kin's moves to the feed. That one was "a human
                # sitting in a kin's park watched it change around them in
                # silence"; the fix gave the kin a mouth and ears, and left
                # this window with neither. So every kin in a shared park
                # could see each other tend and see you tend, and you were the
                # only player who couldn't see anyone — births, adoptions, a
                # whole afternoon of someone else's care, all invisible from
                # here while they read yours.
                # reader="desktop": this window keeps its OWN place in the
                # feed. Without it, looking here marks the news read on the
                # kin's behalf and the kin silently stops being told what the
                # other tenants did — you'd be reading its mail.
                result = host.decorate(kin, command,
                                       host.run(kin, command, say=say),
                                       reader="desktop")
            except Exception as e:  # noqa: BLE001 — surface, never crash the UI
                result = f"[couldn't play that: {e}]"
            wx.CallAfter(self._on_result, result, move_focus, speak, bool(say))

        threading.Thread(target=worker, daemon=True).start()

    def _on_result(self, result, move_focus=True, speak=True, said=False):
        self._append(result + "\n")
        self._set_busy(False)
        # Clear the narration only once it has actually gone out, so a failed
        # turn doesn't silently eat what you wrote.
        if said:
            try:
                self.say.SetValue("")
            except Exception:
                pass
        # Only return focus to the command field after a command the user
        # actively issued (Send / Look / Enter). An auto-look from opening the
        # dialog or switching parks must NOT pull focus off wherever the user
        # is (e.g. mid-arrow in the picker).
        if move_focus:
            self.cmd.SetFocus()
        # Speak the narration so an NVDA user hears the outcome without having to
        # arrow back through the read-only field — but only for a command the
        # user actively issued, never for an auto-look while browsing the picker.
        if speak:
            nvda_speak(result)
