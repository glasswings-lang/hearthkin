# SPDX-License-Identifier: CC0-1.0

"""dialogs.cron_entry - extracted from the former monolithic dialogs.py."""

import re

import wx
from ._shared import TickList


class CronEntryDialog(wx.Dialog):
    """Edit one cron entry's time, prompt, and enabled state. Invoked by
    EditKinDialog's cron section when the user clicks Add entry or Edit
    entry. Returns the new {time, prompt, enabled} dict via get_entry()
    if the user clicks OK.

    Time format is validated as HH:MM 24-hour on save (with one-digit
    hours like '8:00' normalized to '08:00'). Empty prompts are
    rejected. Both checks show a wx.MessageBox and keep the dialog open
    on failure so the user can correct without retyping the rest."""

    def __init__(self, parent, kin, entry=None):
        super().__init__(
            parent,
            title=f"Cron entry — {kin}",
            size=(560, 580),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.kin = kin
        defaults = entry or {}
        self._orig_entry = defaults
        default_prompt = (
            defaults.get("prompt")
            or f"I'm {kin}. What do I want to do today?"
        )

        # Fire-times. One HH:MM, or several comma-separated
        # ("09:00, 15:00, 21:00") so a routine that happens several times a
        # day is ONE entry, not one-per-time. Pre-fill from whatever shape the
        # entry already has (a times list, an interval, or a legacy single
        # time) by expanding it to explicit times.
        from cron_helpers import cron_entry_fire_times
        _fire = cron_entry_fire_times(defaults) if defaults else []
        _times_value = ", ".join(_fire) if _fire else "08:00"
        time_label = wx.StaticText(
            self, label="&Times (HH:MM; comma-separated for several):")
        self.times_field = wx.TextCtrl(self, value=_times_value)

        # Inline overlap warning, hidden until an overlap is found on OK. A
        # read-only multi-line TextCtrl (not StaticText) so it's tab-reachable
        # and NVDA reads it; created right after the time field so it lands
        # next in tab order — right where someone correcting the time is.
        self.overlap_warning = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
        )
        self.overlap_warning.SetMinSize((-1, 52))
        # Nothing precedes it that can act as a buddy label, so without a
        # name NVDA announces the warning as an unnamed edit field.
        self.overlap_warning.SetName("Time overlap warning")
        self.overlap_warning.Hide()
        # The time the user has already been warned about and chosen to keep,
        # so a second OK on the same clashing time goes through — warn once,
        # then it's their call.
        self._overlap_ack_time = None

        prompt_label = wx.StaticText(self, label="&Prompt:")
        self.prompt_field = wx.TextCtrl(
            self, value=default_prompt, style=wx.TE_MULTILINE,
        )
        self.prompt_field.SetMinSize((-1, 100))

        self.enabled_check = wx.CheckBox(
            self, label="&Enabled — fire this entry on schedule",
        )
        self.enabled_check.SetValue(bool(defaults.get("enabled", True)))

        # Own history (see hearthkin_cron.own_history_path). Off by default:
        # most wake-ups keep their continuity by sharing the chat. A job that
        # writes something apart from the chat -- fiction, a separate journal
        # -- is better off seeing only its own past runs, and keeping them out
        # of the conversation.
        self.own_history_check = wx.CheckBox(
            self, label="&Keep this job's history separate from the chat",
        )
        self.own_history_check.SetValue(bool(defaults.get("own_history")))
        own_history_hint = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
            value="(When ticked, this job sees only its own past runs, not "
                  "the chat, and its runs aren't saved into the chat. Good "
                  "for fiction or writing jobs. Unticked, it shares the chat "
                  "as usual.)",
        )
        own_history_hint.SetMinSize((-1, 56))
        own_history_hint.SetName("What a separate history does")

        # Outcome-based tend retry. Only meaningful for tending entries (ones
        # that hand the kin staging to process). When > 0, if this wake-up has
        # pending staging but the kin's reply calls no tools, it's re-prompted
        # to issue the real call. 0 = off. (A choice, not a SpinCtrl — NVDA.)
        retry_label = wx.StaticText(
            self, label="&Retry tending if no tool call fired:",
        )
        self.retry_choice = wx.Choice(self, choices=["0 (off)", "1", "2", "3"])
        try:
            _init_retry = max(0, min(3, int(defaults.get("tend_retry", 0) or 0)))
        except (TypeError, ValueError):
            _init_retry = 0
        self.retry_choice.SetSelection(_init_retry)
        # A StaticText here would buddy-label nothing — the next control
        # already has its own label — so this guidance would reach sighted
        # users only. Read-only TextCtrl puts it in tab order.
        retry_hint = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
            value="(Only matters for tending wake-ups. Leave at 0 for "
                  "check-in prompts that don't use tools.)",
        )
        retry_hint.SetMinSize((-1, 40))
        retry_hint.SetName("When tend retry applies")

        # One more try, for ANY job (see hearthkin_cron._maybe_gesture_retry).
        # The tend retry above only covers tending; a journal job that says
        # "let me write today's entry" and calls nothing had no second chance.
        # Judged by whether a tool ran, not by the wording. Off by default: a
        # check-in job that just talks has nothing to retry.
        self.gesture_retry_check = wx.CheckBox(
            self,
            label="&Give it one more try if no tool runs",
        )
        self.gesture_retry_check.SetValue(bool(defaults.get("gesture_retry")))
        gesture_retry_hint = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
            value="(For jobs meant to use tools, like writing a journal "
                  "entry. If a run ends without any tool actually running, "
                  "say it only described the writing, it gets one reminder "
                  "and one more turn. If there was really nothing to do, it "
                  "can just say so. Only once, so it can't loop.)",
        )
        gesture_retry_hint.SetMinSize((-1, 56))
        gesture_retry_hint.SetName("What one more try does")

        # --- Where does this wake-up's message go? (destination addressing) ---
        # A cron reply is always recorded in the kin's own chat history +
        # journal. This picks where it's ALSO sent outward. The kin's Telegram
        # DMs and groups are listed so the operator can address the output
        # somewhere specific (the live pain: cron output never reached groups).
        # Leaving everything unticked keeps the historic behavior. A read-only
        # multi-line TextCtrl for the explainer so NVDA reads it in tab order.
        # dest_label is created further down, immediately before the checklist
        # it names. It reads as though it belongs up here beside the explainer,
        # and on screen it still sits there -- but the accessible name comes
        # from creation order, not sizer order, and only a StaticText that is
        # the IMMEDIATELY preceding sibling counts. Created here it named the
        # explainer instead, and the checklist was left announcing as a bare
        # "check list box".
        dest_help = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
            value=(
                "Tick specific places to send this scheduled message exactly "
                "there — a Telegram group, a Telegram or Discord DM, a "
                "Discord channel. Leave everything unticked to "
                "keep the old behavior (it goes to whoever has 'mirror to "
                "Telegram' turned on). 'Desktop only' records it in the kin's "
                "chat and journal and sends it nowhere outward."
            ),
        )
        dest_help.SetMinSize((-1, 62))
        # Parallel to the checklist rows: (surface, id, label).
        self._dest_rows = [("desktop", "", "Desktop only")]
        choices = ["Desktop only — record it, send nowhere outward"]
        try:
            # Telegram and Discord, from the one list reach_out and the
            # heartbeat use too (reach_places).
            from kin_persistence import load_agent_config
            import reach_places
            for p in reach_places.place_rows(load_agent_config(kin) or {}):
                choices.append(p["display"])
                self._dest_rows.append((p["surface"], p["id"], p["label"]))
        except Exception:
            pass
        # Created here, immediately before the list, because that is the only
        # position wxMSW will take a label from. Still added to the sizer above
        # the explainer, so nothing moves on screen.
        dest_label = wx.StaticText(self, label="&Send this message to:")
        self.dest_list = TickList(self, "Send to", choices)
        self.dest_list.SetMinSize((-1, 110))
        _existing = {
            (d.get("surface"), str(d.get("id", "")))
            for d in (defaults.get("destinations") or [])
            if isinstance(d, dict)
        }
        for _i, (_s, _rid, _lbl) in enumerate(self._dest_rows):
            if (_s, str(_rid)) in _existing:
                self.dest_list.Check(_i, True)

        ok_btn = wx.Button(self, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="&Cancel")
        ok_btn.SetDefault()
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(time_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(
            self.times_field,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=8,
        )
        sizer.Add(
            self.overlap_warning,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8,
        )
        sizer.Add(prompt_label, flag=wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(
            self.prompt_field,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=8,
        )
        sizer.Add(
            self.enabled_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8,
        )
        sizer.Add(
            self.own_history_check, flag=wx.LEFT | wx.RIGHT, border=8,
        )
        sizer.Add(
            own_history_hint,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8,
        )
        sizer.Add(retry_label, flag=wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(
            self.retry_choice,
            flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8,
        )
        sizer.Add(retry_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        sizer.Add(
            self.gesture_retry_check, flag=wx.LEFT | wx.RIGHT, border=8,
        )
        sizer.Add(
            gesture_retry_hint,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8,
        )
        sizer.Add(dest_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(
            dest_help, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        sizer.Add(
            self.dest_list,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=4)
        btn_row.Add(cancel_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizer(sizer)

    def _on_ok(self, event):
        raw = self.times_field.GetValue().strip()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        normed, bad = [], []
        for p in parts:
            m = re.match(r"^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$", p)
            if m:
                normed.append(f"{int(m.group(1)):02d}:{m.group(2)}")
            else:
                bad.append(p)
        if not normed or bad:
            problem = (f" Couldn't read: {', '.join(repr(b) for b in bad)}."
                       if bad else "")
            wx.MessageBox(
                "Give one or more times as HH:MM 24-hour (e.g. 08:00, 14:30, "
                "23:59). Separate several with commas, like "
                "'09:00, 15:00, 21:00'." + problem,
                "Invalid time",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return  # don't Skip — dialog stays open
        # De-dup, sort, and re-render canonical so the field is tidy.
        normed = sorted(dict.fromkeys(normed))
        self.times_field.SetValue(", ".join(normed))
        prompt = self.prompt_field.GetValue().strip()
        if not prompt:
            wx.MessageBox(
                "Prompt can't be empty. The model needs something to "
                "respond to when the cron fires.",
                "Missing prompt",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        # Overlap warning. An enabled entry firing at the same minute as
        # another kin's enabled cron will queue behind it on the shared GPU
        # and can time out. Warn once (naming who), then let the user keep it
        # if they mean to. Disabled entries never fire, so skip the check.
        if self.enabled_check.GetValue():
            # Check every fire-time; collect which times clash with whom.
            clashes_by_time = {}
            for t in normed:
                try:
                    from kin_persistence import cron_time_collisions
                    cl = cron_time_collisions(self.kin, t)
                except Exception:
                    cl = []
                if cl:
                    clashes_by_time[t] = sorted({k for k, _ in cl})
            ack_key = ",".join(normed)
            if clashes_by_time and self._overlap_ack_time != ack_key:
                self._overlap_ack_time = ack_key
                bits = [
                    f"{t} overlaps {', '.join(ks)}'s cron"
                    for t, ks in sorted(clashes_by_time.items())
                ]
                msg = (
                    "Heads up — " + "; ".join(bits) + ". On this machine the "
                    "kin share one GPU, so they take turns and the later one "
                    "can time out. Change the times above, or press OK again "
                    "to keep them anyway."
                )
                self.overlap_warning.SetValue(msg)
                if not self.overlap_warning.IsShown():
                    self.overlap_warning.Show()
                    self.Layout()
                try:
                    from audio import nvda_speak
                    nvda_speak(msg)
                except Exception:
                    pass
                self.times_field.SetFocus()
                self.times_field.SelectAll()
                return  # keep the dialog open so they can adjust
        event.Skip()  # accept — normal OK behavior closes the dialog

    def get_entry(self):
        # Preserve any other keys the entry already had (forward-compatible),
        # then overwrite the fields this dialog owns.
        out = dict(getattr(self, "_orig_entry", {}) or {})
        times = [t.strip() for t in self.times_field.GetValue().split(",") if t.strip()]
        out.update({
            "times": times,
            "prompt": self.prompt_field.GetValue().strip(),
            "enabled": bool(self.enabled_check.GetValue()),
            "tend_retry": int(self.retry_choice.GetSelection()),
            "gesture_retry": bool(self.gesture_retry_check.GetValue()),
        })
        # Own history keeps ONE name for the job's life, so unticking and
        # ticking again picks the same history back up instead of starting
        # an empty one. An entry set up by hand with a name keeps that name.
        prev = out.get("own_history")
        name = (prev if isinstance(prev, str) and prev.strip()
                else out.get("own_history_name"))
        if self.own_history_check.GetValue():
            if not (isinstance(name, str) and name.strip()):
                import datetime as _dt
                name = "job-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            out["own_history"] = name
            out["own_history_name"] = name
        else:
            out["own_history"] = False
            if isinstance(name, str) and name.strip():
                out["own_history_name"] = name
        # Migrate off the shapes this dialog no longer emits — it always
        # produces an explicit `times` list now, so a stale single `time` or
        # interval spec left behind would confuse cron_entry_fire_times.
        for _k in ("time", "every_minutes", "active_start", "active_end"):
            out.pop(_k, None)
        # Destinations from the checklist. Outward picks (DM/group) win; else
        # "Desktop only" -> explicit send-nowhere; else omit the key entirely
        # so the entry falls back to the legacy mirror-to-DM behavior.
        checked = [
            self._dest_rows[i]
            for i in range(len(self._dest_rows))
            if self.dest_list.IsChecked(i)
        ]
        outward = [(s, rid, lbl) for (s, rid, lbl) in checked if s != "desktop"]
        if outward:
            out["destinations"] = [
                {"surface": s, "id": rid, "label": lbl}
                for (s, rid, lbl) in outward
            ]
        elif any(s == "desktop" for (s, _r, _l) in checked):
            out["destinations"] = [{"surface": "desktop"}]
        else:
            out.pop("destinations", None)
        return out


