"""Per-kin voice-tuning sliders, in their own dialog.

Split off the Voice tab to keep it to the everyday bits (enable + the
voice picker). These are the ElevenLabs tuning sliders — stability,
similarity boost, style, speed — that you set once and rarely revisit.

Button-opens-dialog (NVDA-discoverable), flat, like the other settings
dialogs. Saves into the voice sub-dict through the parent's
`_save_voice_param`; slider writes are debounced ~400ms so holding an
arrow key doesn't fire a config write per step. Sliders read 0-100 (speed
70-130) directly — unchanged from the old inline behaviour, so NVDA reads
the same integer it always did.
"""

import wx


class VoiceTuningDialog(wx.Dialog):
    def __init__(self, parent, cfg, save_voice_param, kin_name=""):
        title = "Voice tuning"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_voice_param = save_voice_param
        self._timers = {}
        self._pending = {}  # key -> latest float value awaiting debounced save

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        v = cfg.get("voice") or {}

        def slider_row(label_text, name, lo, hi, init_int, key, to_float):
            row = wx.BoxSizer(wx.HORIZONTAL)
            lbl = wx.StaticText(panel, label=label_text)
            lbl.SetMinSize((150, -1))
            slider = wx.Slider(panel, value=init_int, minValue=lo, maxValue=hi)
            slider.SetName(name)
            value_lbl = wx.StaticText(panel, label=str(init_int))
            value_lbl.SetMinSize((50, -1))

            def on_evt(_e, s=slider, vl=value_lbl, k=key, f=to_float):
                raw = s.GetValue()
                vl.SetLabel(str(raw))
                val = f(raw)
                self._pending[k] = val

                def _commit(kk=k, vv=val):
                    self._pending.pop(kk, None)
                    self._save_voice_param(kk, vv)

                t = self._timers.get(k)
                if t is not None:
                    t.Stop()
                self._timers[k] = wx.CallLater(400, _commit)

            slider.Bind(wx.EVT_SLIDER, on_evt)
            row.Add(lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
            row.Add(slider, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
            row.Add(value_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=6)
            sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        pct = lambda r: r / 100.0
        slider_row("&Stability (0-100):", "Voice stability", 0, 100,
                   int(round(float(v.get("stability", 0.5)) * 100)), "stability", pct)
        slider_row("S&imilarity boost (0-100):", "Voice similarity boost", 0, 100,
                   int(round(float(v.get("similarity_boost", 0.75)) * 100)),
                   "similarity_boost", pct)
        slider_row("St&yle (0-100):", "Voice style", 0, 100,
                   int(round(float(v.get("style", 0.0)) * 100)), "style", pct)
        slider_row("Spee&d (70 = slow, 100 = natural, 130 = fast):", "Voice speed",
                   70, 130, int(round(float(v.get("speed", 1.0)) * 100)), "speed", pct)

        # Read-only TextCtrl: the next control is a button, which names
        # itself from its label, so as a StaticText this is announced to
        # nobody -- and it is the only explanation of what these four
        # sliders actually do. A slider that reads "Voice style, 0" tells
        # a keyboard user nothing about which way to move it.
        explainer = wx.TextCtrl(
            panel,
            value=("Stability: lower = more variation / emotional range; higher "
                   "= consistent.\nSimilarity boost: higher = closer to the "
                   "source voice; lower = more freedom.\nStyle: emotional "
                   "expression (only some models honor it). 0 = neutral.\n"
                   "Speed: 100 is natural; below is slower, above faster."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL)
        explainer.SetMinSize((-1, 110))
        explainer.SetName("What these sliders do")
        sizer.Add(explainer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)
        sizer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((520, 380))
        self.Layout()

    def _on_close(self, _event):
        # Flush pending debounced writes (wx.CallLater has no force-fire,
        # so save the stashed latest values directly) — a quick adjust-
        # then-close can't drop the last change.
        for t in self._timers.values():
            if t is not None:
                t.Stop()
        for key, val in list(self._pending.items()):
            self._save_voice_param(key, val)
        self._pending.clear()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
