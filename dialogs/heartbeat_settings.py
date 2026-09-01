# SPDX-License-Identifier: CC0-1.0

"""dialogs.heartbeat_settings — per-kin proactive-heartbeat settings.

A focused sub-dialog, opened from the Cron tab's "Heartbeat…" button, so the
low-frequency heartbeat knobs stay out of the tab (the suite convention:
everyday controls on the tab, per-concern knobs behind a '… settings…'
button). Edits cfg["heartbeat"]: enabled, every_minutes, the active-hours
window, and where the kin's reach-outs go. Returns the dict via
get_heartbeat(); the caller persists it with the parent's _save_param."""

import re

import wx


class HeartbeatSettingsDialog(wx.Dialog):
    def __init__(self, parent, kin, heartbeat=None):
        super().__init__(
            parent, title=f"Heartbeat — {kin}", size=(580, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.kin = kin
        hb = dict(heartbeat or {})
        self._orig = hb

        # Named because it's the first thing you tab to and nothing precedes
        # it to lend it a name — it announced as a bare "edit, read only",
        # which tells you nothing about what dialog you just opened.
        info = wx.TextCtrl(
            self,
            name="What a heartbeat is",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
            value=(
                "A heartbeat gives this kin a quiet moment, on a timer, to "
                "reach out to you on its own — but only if it genuinely has "
                "something to say. Most of the time it will stay silent, and "
                "silence sends nothing and leaves no trace. It only runs while "
                "Hearthkin is open.\n\n"
                "Turning it on here is all it takes — the kin's ability to "
                "reach out is part of this feature; there's no separate tool "
                "to enable.\n\n"
                "About cost: each heartbeat runs the model once even when the "
                "kin decides to stay quiet. On a kin that runs on your Mac "
                "that's free; on a paid (OpenRouter) kin, keep the interval "
                "long so quiet check-ins don't add up."
            ),
        )
        info.SetMinSize((-1, 128))

        self.enable_check = wx.CheckBox(
            self, label="&Enable proactive heartbeat for this kin")
        self.enable_check.SetValue(bool(hb.get("enabled", False)))

        try:
            _every = int(hb.get("every_minutes", 120) or 120)
        except (TypeError, ValueError):
            _every = 120
        every_label = wx.StaticText(self, label="Give it a moment every (&minutes):")
        self.every_field = wx.TextCtrl(self, value=str(_every))

        start_label = wx.StaticText(self, label="Only between — &start (HH:MM):")
        self.start_field = wx.TextCtrl(self, value=str(hb.get("active_start", "09:00")))
        end_label = wx.StaticText(self, label="and e&nd (HH:MM):")
        self.end_field = wx.TextCtrl(self, value=str(hb.get("active_end", "22:00")))

        dest_label = wx.StaticText(self, label="Send its reach-outs t&o:")
        # Parallel to the Choice: (surface, id).
        self._dest_rows = [("desktop", "")]
        # The name the kin is shown and must say back. Taken from the SOURCE
        # config, never parsed back out of the display string — a label like
        # "Book club (Tuesdays)" loses its tail to that, and two groups whose names
        # differ only inside brackets would collide into one unaddressable name.
        self._dest_labels = ["Desktop"]
        choices = ["Desktop — the kin's own chat (you see it next time you open it)"]
        try:
            from kin_persistence import load_agent_config
            cfg = load_agent_config(kin) or {}
            tg = cfg.get("telegram") or {}
            labels = tg.get("user_labels") or {}
            for uid in (tg.get("allow_from") or []):
                s = str(uid).strip()
                if not s or s == "*" or not s.lstrip("-").isdigit():
                    continue
                nm = str(labels.get(s) or "").strip()
                choices.append(f"Telegram DM: {nm} ({s})" if nm else f"Telegram DM: {s}")
                self._dest_rows.append(("telegram_dm", s))
                self._dest_labels.append(nm or s)
            for cid, g in (tg.get("groups") or {}).items():
                sc = str(cid).strip()
                if not sc:
                    continue
                nm = str(g.get("label") or "").strip() if isinstance(g, dict) else ""
                choices.append(
                    f"Telegram group: {nm} ({sc})" if nm else f"Telegram group: {sc}")
                self._dest_rows.append(("telegram_group", sc))
                self._dest_labels.append(nm or sc)
        except Exception:
            pass
        self.dest_choice = wx.Choice(self, choices=choices)
        cur = hb.get("destination") or {"surface": "desktop"}
        cur_key = (cur.get("surface", "desktop"), str(cur.get("id", "")))
        sel = 0
        for i, (s, rid) in enumerate(self._dest_rows):
            if (s, str(rid)) == cur_key:
                sel = i
                break
        self.dest_choice.SetSelection(sel)

        # ─── Other places this kin may write to, unprompted ───────────
        # The default above is where a reach-out goes when the kin doesn't say
        # where. THIS is the list it may name instead — and it is the entire
        # security model: a kin can reach nowhere that isn't ticked here.
        # Off for every kin until an operator ticks something.
        allow_info = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
            value=(
                "Below: anywhere else this kin may write to on its own. "
                "Nothing is ticked by default, and a kin can never reach a "
                "place that isn't ticked here — this list is the whole of what "
                "it can do.\n"
                "Tick a group and the kin can start a conversation there "
                "without being spoken to first. Everyone in that group will "
                "hear it, so tick the ones you're in."
            ),
        )
        allow_info.SetName("What ticking a place does")
        allow_info.SetMinSize((-1, 78))
        allow_label = wx.StaticText(
            self, label="Other places it may &write to (Space toggles):")
        # Skip row 0 — "desktop" is the kin's own chat, which is the default
        # target, not somewhere it addresses.
        self._allow_rows = self._dest_rows[1:]
        # "(Space toggles)" has to be in the NAME. The StaticText above is
        # never announced (this list has its own SetName, which wins), so the
        # one instruction telling you HOW to tick a place reached sighted
        # users only — on a list whose entire purpose is ticking.
        self.allow_list = wx.CheckListBox(self, choices=choices[1:])
        self.allow_list.SetName("Other places it may write to (Space toggles)")
        self.allow_list.SetMinSize((-1, 100))
        cur_allowed = {
            (str(d.get("surface", "")), str(d.get("id", "")))
            for d in (hb.get("allowed_destinations") or [])
            if isinstance(d, dict)
        }
        for i, (s, rid) in enumerate(self._allow_rows):
            if (s, str(rid)) in cur_allowed:
                self.allow_list.Check(i, True)
        # The labels the kin will actually be shown and must say back.
        self._allow_labels = self._dest_labels[1:]

        ok_btn = wx.Button(self, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="&Cancel")
        ok_btn.SetDefault()
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(info, flag=wx.EXPAND | wx.ALL, border=8)
        sizer.Add(self.enable_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        for lab, fld in ((every_label, self.every_field),
                         (start_label, self.start_field),
                         (end_label, self.end_field)):
            sizer.Add(lab, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
            sizer.Add(fld, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(dest_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(
            self.dest_choice,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        sizer.Add(allow_info, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        sizer.Add(allow_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(self.allow_list, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=4)
        btn_row.Add(cancel_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)
        self.SetSizer(sizer)

    def _on_ok(self, event):
        for fld, name in ((self.start_field, "start"), (self.end_field, "end")):
            v = fld.GetValue().strip()
            if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", v):
                wx.MessageBox(
                    f"The {name} time must be HH:MM 24-hour (e.g. 09:00, "
                    f"22:30). Got: {v!r}",
                    "Invalid time", wx.OK | wx.ICON_ERROR, self)
                fld.SetFocus()
                return  # keep dialog open
        event.Skip()

    def get_heartbeat(self):
        out = dict(self._orig)
        try:
            every = max(5, min(1440, int(self.every_field.GetValue().strip() or "120")))
        except (TypeError, ValueError):
            every = 120

        def _norm(v, fb):
            m = re.match(r"^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$", (v or "").strip())
            return f"{int(m.group(1)):02d}:{m.group(2)}" if m else fb

        surface, rid = self._dest_rows[max(0, self.dest_choice.GetSelection())]
        dest = {"surface": surface}
        if surface != "desktop" and rid:
            dest["id"] = rid
        allowed = []
        for i, (s, rid) in enumerate(self._allow_rows):
            if not self.allow_list.IsChecked(i):
                continue
            allowed.append({
                # The label is the contract: it's what the kin is shown in its
                # tool schema and what it must say back to send there.
                "label": self._allow_labels[i],
                "surface": s,
                "id": str(rid),
            })
        out.update({
            "enabled": bool(self.enable_check.GetValue()),
            "every_minutes": every,
            "active_start": _norm(self.start_field.GetValue(), "09:00"),
            "active_end": _norm(self.end_field.GetValue(), "22:00"),
            "destination": dest,
            "allowed_destinations": allowed,
        })
        return out
