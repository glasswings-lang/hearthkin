# SPDX-License-Identifier: CC0-1.0

"""dialogs._shared — widgets and helpers used across multiple dialogs.

Currently:
  - `_IntField`: NVDA-friendly integer input widget that replaces
    wx.SpinCtrl across the dialogs package.
  - `rebuild_listbox`: rebuilds a wx.ListBox / wx.CheckListBox /
    wx.Choice in place, preserving the previous selection by stable
    key (preferred) or by previous index (fallback). Without this,
    every list refresh sends NVDA's focus back to the top — painful
    when adding or removing items in a long list of Telegram users,
    cron entries, etc.

  - `_SliderValueAccessible`: makes NVDA read a generation-parameter
    slider's value as its formatted decimal ("0.45") instead of the raw
    integer position ("45"). Used by any dialog with scaled float
    sliders (the per-kin sampling settings).

Kept in a leaf module so every dialog can `from ._shared import ...`
without circular pain.
"""

import wx


class _SliderValueAccessible(wx.Accessible):
    """Make NVDA read a generation-parameter slider's value as its
    formatted decimal ("0.45") instead of the raw integer position
    ("45"). wxMSW exposes a slider's value to NVDA as the underlying
    int, which doesn't match the value the user is setting — the old
    workaround was an extra nvda_speak of the decimal, but that produced
    a DOUBLED announcement (NVDA's native "45" plus the spoken "0.45").

    Overriding only accValue lets the native announcement carry the right
    text, so the redundant speech is dropped and the user hears exactly
    one thing per step. Every other property returns NOT_IMPLEMENTED, so
    role / name / state still come from the real control — we change only
    what the value reads as. Reads the slider live each time, so it's
    always current. If wx.Accessible isn't honored on a given setup, the
    worst case is NVDA falling back to the raw int ("45") — still a single
    reading, just less friendly — never silence."""

    def __init__(self, slider):
        super().__init__(slider)
        self._slider = slider

    def GetValue(self, childId):
        s = self._slider
        try:
            scale = getattr(s, "_scale", 1)
            fmt = getattr(s, "_fmt", ".2f")
            raw = s.GetValue()
            val = raw / scale if scale > 1 else raw
            txt = f"{val:{fmt}}" if scale > 1 else str(val)
            return (wx.ACC_OK, txt)
        except Exception:
            return (wx.ACC_NOT_IMPLEMENTED, "")


def rebuild_listbox(listbox, labels, *, keys=None, saved_key=None, saved_index=None):
    """Repaint a wx.ListBox-like control and re-select the previously
    selected entry by key when possible.

    Args:
        listbox: a control with a .Set(items) method that takes a list
            of strings (wx.ListBox, wx.CheckListBox, wx.Choice).
        labels: the new display strings to show.
        keys: parallel to `labels` — stable identifiers used to match
            `saved_key`. Optional; when None, key-based restoration is
            skipped and the helper falls back to `saved_index`.
        saved_key: the identifier that was selected before the rebuild.
            The caller is responsible for capturing this BEFORE this
            function is called (because the caller usually has to
            reset its own parallel key list during the rebuild
            anyway).
        saved_index: the previously selected index, used as a fallback
            when `saved_key` isn't found in the new `keys`. Clamped to
            the new length so a deletion at the end still lands you on
            the last item rather than nothing.

    Behavior:
        - Empty `labels`: just clears, no selection.
        - `saved_key` in `keys`: select that index.
        - Otherwise, clamp `saved_index` to the new range.
        - Otherwise (e.g. fresh dialog open with -1 selection), default
          to index 0 so something is highlighted for screen readers.

    For wx.CheckListBox the helper preserves the SELECTION cursor but
    NOT the per-item checked state — caller is responsible for
    re-checking entries from their source-of-truth membership list
    after this call returns.
    """
    listbox.Set(labels)
    if not labels:
        return

    new_idx = -1
    if saved_key is not None and keys is not None:
        try:
            new_idx = keys.index(saved_key)
        except ValueError:
            pass

    if new_idx < 0 and saved_index is not None and saved_index >= 0:
        new_idx = min(saved_index, len(labels) - 1)

    if new_idx < 0:
        new_idx = 0

    listbox.SetSelection(new_idx)


class _IntField(wx.TextCtrl):
    """A plain wx.TextCtrl that accepts integer input — with optional
    commas and whitespace — and validates / clamps to a range on blur
    or Enter. Replaces wx.SpinCtrl across this module for two reasons:

    1. SpinCtrl on Windows uses the native EDIT control's ES_NUMBER
       style, which blocks paste of any string containing non-digits
       at the OS level. Users naturally copy numbers from the human-
       readable displays elsewhere in the app ("Model max: 131,072
       tokens") and the comma in the pasted string is rejected before
       wx even sees the event. Plain TextCtrl strips the formatting
       and parses cleanly.

    2. SpinCtrl's rapid arrow-key hold floods NVDA's announce queue
       (a project-wide rule documented in CLAUDE.md). The remaining
       SpinCtrls in this file predate that rule — converting them
       here is housekeeping that was already due.

    Behavior:
      - Initial value is rendered as a plain integer (no formatting).
        Display formatting (commas) belongs in read-only labels, not
        in an editable field where it would re-format mid-type.
      - On Enter or focus-loss, the field's text is read, has commas
        and whitespace stripped, parsed as int, clamped to [min,
        max], and rendered back canonically. If parsing fails, the
        last known good value is restored — so mistyping doesn't
        destroy the previous setting.
      - `on_commit(int)` fires once per validated value change.
        Compatible with the auto-save callback shape the old
        SpinCtrl EVT_SPINCTRL handlers used.
      - `GetIntValue()` / `SetIntValue(int)` provide the equivalent
        of SpinCtrl.GetValue() / SetValue() for callers that need
        programmatic access.
    """

    def __init__(self, parent, *, value, min_val, max_val,
                 on_commit=None, size=wx.DefaultSize, name=""):
        clamped = max(int(min_val), min(int(max_val), int(value)))
        super().__init__(
            parent,
            value=str(clamped),
            style=wx.TE_PROCESS_ENTER,
            size=size,
        )
        if name:
            self.SetName(name)
        self._min = int(min_val)
        self._max = int(max_val)
        self._last_committed = clamped
        self._on_commit = on_commit
        self.Bind(wx.EVT_TEXT_ENTER, self._commit)
        self.Bind(wx.EVT_KILL_FOCUS, self._on_kill_focus)

    def _on_kill_focus(self, event):
        self._commit(None)
        event.Skip()

    def _commit(self, _event):
        raw = self.GetValue()
        cleaned = raw.replace(",", "").replace(" ", "").strip()
        try:
            val = int(cleaned)
        except (TypeError, ValueError):
            val = self._last_committed
        val = max(self._min, min(self._max, val))
        # Always rewrite the field to the canonical form so a sloppy
        # entry ("131,072 ") doesn't sit in the visible text.
        if str(val) != raw:
            self.ChangeValue(str(val))
        if val != self._last_committed:
            self._last_committed = val
            if self._on_commit is not None:
                try:
                    self._on_commit(val)
                except Exception:
                    pass

    def GetIntValue(self):
        """Return the last validated int value. Prefer this over
        GetValue() in callers — GetValue() returns the raw textbox
        contents, which may be mid-edit and not yet validated."""
        return self._last_committed

    def SetIntValue(self, val):
        """Programmatically set the value. Clamped to range. Does
        NOT fire on_commit — that callback is for user-initiated
        changes only."""
        val = max(self._min, min(self._max, int(val)))
        self._last_committed = val
        self.ChangeValue(str(val))

    def SetMaxValue(self, new_max):
        """Update the upper bound. If the current value exceeds the
        new bound, clamp it down and fire on_commit (this IS a real
        change to persist — e.g. num_ctx ceiling dropping when the
        operator swaps to a smaller-context model). Returns the
        clamped value, or None if no clamping happened.

        Used by the per-kin num_ctx field to track the active
        model's declared max context — the global 1M ceiling exists
        to support models with that range, but setting num_ctx
        above the active model's declared cap either fails outright
        or bills extended-context premium pricing."""
        self._max = int(new_max)
        if self._last_committed > self._max:
            self._last_committed = self._max
            self.ChangeValue(str(self._max))
            if self._on_commit is not None:
                try:
                    self._on_commit(self._max)
                except Exception:
                    pass
            return self._max
        return None

    def GetMaxValue(self):
        """The current upper bound (may shift via SetMaxValue)."""
        return self._max
