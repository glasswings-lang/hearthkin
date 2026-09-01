"""Per-kin sampling / generation-parameter settings, in their own dialog.

Split out of the Model && generation tab to declutter it: the sampling
knobs (temperature, top-p, min-p, the penalties, top-k) are power-user
controls that most kin never need touched, so they live behind a
"Sampling settings…" button — the app's standard, screen-reader-
discoverable pattern (same shape as the model browser and the recall
settings dialog). All sampling lives here, in one place.

The dialog edits the same generation config keys and persists through the
parent EditKinDialog's ``_save_param`` callback, so every save is
byte-identical to the old inline path (load-modify-save per key on disk).
Slider writes are debounced ~400ms so holding an arrow key doesn't fire a
full config read+write per step; the inline value label and NVDA value
announcement stay per-step.
"""

import wx

from kin_persistence import DEFAULT_AGENT_CONFIG
from ._shared import _IntField, _SliderValueAccessible


class SamplingSettingsDialog(wx.Dialog):
    """Edit one kin's sampling / generation parameters.

    `cfg` is the parent dialog's current kin config (read once, at open).
    `save_param(key, value)` is the parent's `_save_param` bound method —
    it reloads config from disk, applies the one key, and saves, so saves
    here stay coherent even though the parent may refresh its own cfg.
    """

    def __init__(self, parent, cfg, save_param, kin_name=""):
        title = "Sampling settings"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cfg = cfg
        self._save_param = save_param
        self._slider_save_timers = {}
        self._pending_slider = {}  # key -> latest value awaiting debounced save

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Read-only TextCtrl so it's tab-reachable. As a StaticText it reached
        # nobody: it's the only thing saying these defaults are already fine
        # and when tuning is even warranted — which, on a screen of unlabelled-
        # by-feel sliders, is the difference between "leave this alone" and
        # dragging a kin's voice around blind. "All values save as you change
        # them" is also the only answer to "is there a Save button?".
        blurb = wx.TextCtrl(
            panel,
            value=("How this kin's model picks each word. Defaults suit most "
                   "models; tune only if a kin is repeating itself, drifting "
                   "incoherent, or you're chasing a specific model's sweet "
                   "spot. All values save as you change them."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        blurb.SetName("About these sampling settings")
        blurb.SetMinSize((520, 60))
        sizer.Add(blurb, flag=wx.ALL, border=10)

        # All float sliders step by 0.01 (scale=100): some finetunes are
        # coherent at temp 0.40 but not 0.50, and a 0.1 step can't land on
        # 0.43/0.45/0.47 to find that edge.
        for label_text, key, lo, hi in (
            ("Temperature:", "temperature", 0.0, 2.0),
            ("Top-p:", "top_p", 0.0, 1.0),
            ("Min-p:", "min_p", 0.0, 1.0),
            ("Repeat penalty:", "repeat_penalty", 1.0, 2.0),
            ("Presence penalty:", "presence_penalty", 0.0, 2.0),
            ("Frequency penalty:", "frequency_penalty", 0.0, 2.0),
        ):
            row = self._make_slider_row(panel, label_text, key, lo, hi)
            sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        # Top-k is an integer, not a float — plain _IntField.
        topk_row = wx.BoxSizer(wx.HORIZONTAL)
        topk_lbl = wx.StaticText(panel, label="Top-&k:", size=(150, -1))
        self.topk_field = _IntField(
            panel, value=self.cfg.get("top_k", 40),
            min_val=1, max_val=200, size=(100, -1), name="Top-k",
            on_commit=lambda v: self._save_param("top_k", v))
        topk_row.Add(topk_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        topk_row.Add(self.topk_field)
        sizer.Add(topk_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

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
        self.SetInitialSize((560, 520))
        self.Layout()

    def _make_slider_row(self, panel, label_text, key, min_val, max_val,
                         scale=100, decimals=2):
        """A labelled float slider that reads its decimal value to NVDA via
        _SliderValueAccessible and saves (debounced) through save_param.
        Mirrors the builder the Model tab used before sampling moved
        here."""
        row = wx.BoxSizer(wx.HORIZONTAL)
        lbl = wx.StaticText(panel, label=label_text, size=(150, -1))
        val = self.cfg.get(key, DEFAULT_AGENT_CONFIG[key])
        fmt = f".{decimals}f"
        slider = wx.Slider(
            panel, value=int(round(val * scale)),
            minValue=int(round(min_val * scale)),
            maxValue=int(round(max_val * scale)),
            style=wx.SL_HORIZONTAL)
        # Arrows = one 0.01 step; PageUp/Down jumps ~0.1 so crossing the
        # range isn't hundreds of presses.
        try:
            slider.SetPageSize(max(1, scale // 10))
        except Exception:
            pass
        display = wx.StaticText(panel, label=f"{val:{fmt}}", size=(50, -1))
        slider._key = key
        slider._scale = scale
        slider._fmt = fmt
        slider._display = display
        # Name is just the label; the decimal value is supplied by the
        # custom accessible so NVDA reads "0.45", not the raw int, and not
        # twice.
        slider.SetName(label_text.rstrip(":").strip())
        try:
            slider.SetAccessible(_SliderValueAccessible(slider))
        except Exception:
            pass
        slider.Bind(wx.EVT_SLIDER, self._on_slider)
        row.Add(lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        row.Add(slider, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        row.Add(display, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=4)
        return row

    def _on_slider(self, event):
        slider = event.GetEventObject()
        key = slider._key
        scale = slider._scale
        fmt = getattr(slider, "_fmt", ".2f")
        val = slider.GetValue() / scale
        slider._display.SetLabel(f"{val:{fmt}}")
        # NVDA announcement comes from _SliderValueAccessible (reads the
        # decimal natively); rewriting the name or nvda_speak here would
        # double it. Display repaint stays per-step; the disk write is
        # debounced ~400ms (latest value wins). Stash the latest value so
        # close can flush it if the timer hasn't fired yet.
        self._pending_slider[key] = val

        def _commit(k=key):
            self._pending_slider.pop(k, None)
            self._save_param(k, val)

        timer = self._slider_save_timers.get(key)
        if timer is not None:
            timer.Stop()
        self._slider_save_timers[key] = wx.CallLater(400, _commit)

    def _on_close(self, _event):
        # Flush any pending debounced slider writes so a quick adjust-then-
        # close doesn't drop the last value (wx.CallLater has no force-fire,
        # so we save the stashed latest values directly).
        for timer in self._slider_save_timers.values():
            if timer is not None:
                timer.Stop()
        for key, val in list(self._pending_slider.items()):
            self._save_param(key, val)
        self._pending_slider.clear()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
