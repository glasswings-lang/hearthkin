# SPDX-License-Identifier: CC0-1.0

"""dialogs.room_edit - extracted from the former monolithic dialogs.py."""

import wx

from kin_persistence import (
    DEFAULT_ROOM_CONFIG,
    load_agent_config,
    load_room_conversation,
)
from ._shared import _IntField


def members_without_auto_distill(members):
    """Which of `members` would never distill this room on their own.

    "Remember this room" only makes a room ELIGIBLE for memory. What decides
    whether anything actually fires is a separate, per-KIN setting — the
    distillation triggers (`memory_distill_every_n` / `memory_distill_at_pct`)
    on Settings → Memory — and they govern every surface that kin has, not
    just this room. Both default to 0, i.e. off.

    So ticking the box for a kin with no trigger changes nothing by itself,
    silently: the counter ticks and nothing ever fires. That's the exact
    "I turned it on, did anything happen?" trap the per-surface counter
    overview was built to close, and this checkbox walked straight into it on
    the day it shipped. The dialog says so now instead of promising memory it
    can't deliver.

    A kin whose config can't be read is left out rather than guessed at — a
    false warning is worse than none.
    """
    out = []
    for name in members or []:
        try:
            cfg = load_agent_config(name) or {}
        except Exception:
            continue
        try:
            every_n = int(cfg.get("memory_distill_every_n", 0) or 0)
            at_pct = int(cfg.get("memory_distill_at_pct", 0) or 0)
        except (TypeError, ValueError):
            continue
        if every_n <= 0 and at_pct <= 0:
            out.append(name)
    return out


def distill_help_text(enabled, turn_count, members, no_trigger):
    """The words under the "Remember this room" checkbox.

    Pure so it can be tested without building a dialog: `no_trigger` is the
    result of members_without_auto_distill(members), passed in rather than
    looked up here.
    """
    if not enabled:
        return (
            "Off: nothing said in this room reaches anyone's memory. "
            "It stays in the room transcript and nowhere else. Members "
            "won't recall it in another room, in a DM, or tomorrow.\n"
            "This has been every room's behavior until now, so it stays "
            "the default."
        )
    history = (
        f"This room has {turn_count} turn{'s' if turn_count != 1 else ''} on "
        "disk already, and all of it counts — the first pass works back "
        "through the existing transcript, not just what's said from here on."
        if turn_count else
        "Turns from here on are included."
    )
    lines = [
        "On: each member distills its OWN view of this room into its own "
        "staging notes, and decides during its nightly tending what becomes "
        "lasting memory. Members don't share one summary.",
        f"{history} Other rooms are unaffected.",
    ]
    # The honest part. Without this the box above is a promise the app won't
    # keep for a kin whose triggers are off — which is every kin by default.
    if no_trigger:
        members = list(members or [])
        if len(no_trigger) == len(members) and members:
            who = "None of these kin"
        else:
            who = _and_list(no_trigger)
        lines.append(
            f"⚠ {who} distill anything automatically right now — their "
            "auto-distillation triggers are off (Settings → Memory, per kin), "
            "so ticking this alone won't make the room reach them. It makes "
            "the room eligible; to actually fold it in, use Settings → Memory "
            "→ \"Distill selected surface now\" and pick this room. Turning a "
            "trigger on instead would also start auto-distilling that kin's "
            "other surfaces, which may not be what you want."
        )
    return "\n".join(lines)


def _and_list(names):
    names = list(names)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


class RoomEditDialog(wx.Dialog):
    """Create or edit a Room. Room name field is locked on edit.

    Member selection uses a CheckListBox: tab into it once, arrow keys
    navigate, Space toggles. NVDA's announcement of checked/unchecked
    state on wxMSW's native LISTBOX-with-checkmarks is inconsistent
    (sometimes spoken, often not). Two accessibility safeguards are
    layered on top:
      - A read-only "Currently in this room (N): name1, name2" display
        below the list, tab-reachable, so the user can always read
        the current roster.
      - On every Space-toggle, `nvda_speak(\"X added to room\" /
        \"X removed from room\")` fires explicitly so the action is
        confirmed audibly regardless of native announce behavior."""

    def __init__(self, parent, title="New room", initial_name="", initial_cfg=None,
                 available_kin=None, name_locked=False):
        super().__init__(parent, title=title, size=(580, 640))
        panel = wx.Panel(self)
        cfg = {**DEFAULT_ROOM_CONFIG, **(initial_cfg or {})}
        available_kin = list(available_kin or [])

        # The label carries the "why" when the field is locked. It's the
        # buddy label (name_field has no SetName), so it IS what gets
        # announced -- which makes it the only place a keyboard user can
        # learn why the first field they meet is dead. Before this it just
        # said "Room name:, edit, unavailable" and left them to guess
        # whether renaming was forbidden, deferred, or broken.
        #
        # It really is permanent: there is no room-rename anywhere in the
        # app (kin_persistence.rename_kin_in_rooms renames a KIN across
        # rooms, which is a different thing). And it's load-bearing now --
        # a room's distill scope key is "room:<name>", so a rename would
        # orphan its staging file and bookmark.
        name_lbl = wx.StaticText(
            panel,
            label=("Room name (fixed once the room exists):" if name_locked
                   else "Room name:"),
        )
        self.name_field = wx.TextCtrl(panel, value=initial_name)
        if name_locked:
            self.name_field.Disable()

        members_lbl = wx.StaticText(
            panel,
            label="Members (Tab into the list, arrow keys to navigate, Space to toggle):",
        )

        # Order: previously-checked members first in their saved order, then unchecked
        # alphabetically. Order in the list IS the round order.
        initial_members = [m for m in (cfg.get("members") or []) if m in available_kin]
        non_members = sorted(k for k in available_kin if k not in initial_members)
        ordered_kin = initial_members + non_members

        if ordered_kin:
            self.kin_check = wx.CheckListBox(panel, choices=ordered_kin)
            for i, name in enumerate(ordered_kin):
                if name in initial_members:
                    self.kin_check.Check(i, True)
            self.kin_check.SetMinSize((-1, 180))
            if ordered_kin:
                self.kin_check.SetSelection(0)
            self.kin_check.Bind(wx.EVT_CHECKLISTBOX, self._on_check_toggle)
        else:
            self.kin_check = None

        # Roster display: a read-only mirror of the checklist that NVDA
        # can always read reliably (the CheckListBox itself often won't
        # announce check state on wxMSW). Updated by _refresh_roster
        # whenever the membership changes.
        self.roster_display = wx.TextCtrl(
            panel,
            value=self._format_roster(initial_members),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        # Name says WHY this exists, because landing on a read-only echo of
        # the list you just tabbed through reads as a duplicate control --
        # "I'd wonder if I'd tabbed into the same control twice." It isn't a
        # duplicate: it's the reliable read of check state (wxMSW's native
        # checklist announce is inconsistent), and it's the only place the
        # speaking ORDER is stated in one breath rather than item by item.
        self.roster_display.SetName(
            "Room members in speaking order — read-only summary of the list above")
        self.roster_display.SetMinSize((-1, 50))

        # Action buttons stacked beside the list
        check_all_btn = wx.Button(panel, label="Check &all")
        uncheck_all_btn = wx.Button(panel, label="Uncheck a&ll")
        up_btn = wx.Button(panel, label="Move &up")
        down_btn = wx.Button(panel, label="Move &down")
        check_all_btn.Bind(wx.EVT_BUTTON, self._on_check_all)
        uncheck_all_btn.Bind(wx.EVT_BUTTON, self._on_uncheck_all)
        up_btn.Bind(wx.EVT_BUTTON, self._on_move_up)
        down_btn.Bind(wx.EVT_BUTTON, self._on_move_down)
        if self.kin_check is None:
            for b in (check_all_btn, uncheck_all_btn, up_btn, down_btn):
                b.Disable()

        btns_col = wx.BoxSizer(wx.VERTICAL)
        btns_col.Add(check_all_btn, flag=wx.EXPAND | wx.BOTTOM, border=4)
        btns_col.Add(uncheck_all_btn, flag=wx.EXPAND | wx.BOTTOM, border=12)
        btns_col.Add(up_btn, flag=wx.EXPAND | wx.BOTTOM, border=4)
        btns_col.Add(down_btn, flag=wx.EXPAND)

        members_row = wx.BoxSizer(wx.HORIZONTAL)
        if self.kin_check is not None:
            members_row.Add(self.kin_check, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=8)
        else:
            # Read-only TextCtrl, not StaticText: this is the entire content
            # of the dialog when there are no kin, and as a StaticText it was
            # announced to nobody — a keyboard user met an empty-looking
            # members area with no explanation and no next step, while the
            # red text told everyone else exactly what to do.
            empty = wx.TextCtrl(
                panel,
                value="No kin yet — create some first via the Kin menu.",
                style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP
                | wx.TE_NO_VSCROLL,
            )
            empty.SetName("No kin available")
            empty.SetMinSize((-1, 40))
            members_row.Add(empty, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        members_row.Add(btns_col, flag=wx.EXPAND)

        # Read-only TextCtrl so it's tab-reachable. As a StaticText this was
        # announced to nobody: the next control's buddy slot goes to the
        # nearest preceding StaticText, which is the context-note label below.
        # It's the only statement anywhere of what the Move up / Move down
        # buttons are FOR — that list position IS speaking order — so without
        # it those two buttons operate on something whose purpose is never
        # said out loud. Grey text was the tell: styled as an aside for
        # sighted readers, invisible to everyone else.
        order_hint = wx.TextCtrl(
            panel,
            value="Order in the list = round order. Each round rotates the "
                  "starting member, so no one is always first.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        order_hint.SetName("What member order does")
        order_hint.SetMinSize((-1, 40))

        ctx_lbl = wx.StaticText(panel, label="Room context note (added to each member's system prompt):")
        self.ctx_field = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.ctx_field.SetMinSize((-1, 70))
        self.ctx_field.SetValue(cfg.get("context_note", ""))

        cap_row = wx.BoxSizer(wx.HORIZONTAL)
        cap_lbl = wx.StaticText(panel, label="Max auto-rounds before pause:", size=(220, -1))
        self.cap_spin = _IntField(
            panel,
            value=cfg.get("max_auto_rounds", 10),
            min_val=1, max_val=30,
            size=(80, -1),
            name="Max auto-rounds before pause",
        )
        cap_row.Add(cap_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        cap_row.Add(self.cap_spin)

        idle_row = wx.BoxSizer(wx.HORIZONTAL)
        idle_lbl = wx.StaticText(panel, label="Auto-pause after idle (minutes):", size=(220, -1))
        self.idle_spin = _IntField(
            panel,
            value=cfg.get("auto_inactivity_min", 15),
            min_val=1, max_val=180,
            size=(80, -1),
            name="Auto-pause after idle in minutes",
        )
        idle_row.Add(idle_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        idle_row.Add(self.idle_spin)

        tok_row = wx.BoxSizer(wx.HORIZONTAL)
        tok_lbl = wx.StaticText(panel, label="Per-turn token cap (per kin):", size=(220, -1))
        self.tok_spin = _IntField(
            panel,
            value=cfg.get("per_turn_token_cap", 800),
            min_val=64, max_val=4096,
            size=(80, -1),
            name="Per-turn token cap per kin",
        )
        tok_row.Add(tok_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        tok_row.Add(self.tok_spin)

        # ─── Memory ──────────────────────────────────────────────────
        # Off by default and off for every room that predates this
        # checkbox. Until 2026-07-16 nothing said in a room ever
        # reached a kin's memory — an unbuilt wire, not a decision, but
        # rooms are where the most private conversations happen and
        # people have been talking in them for months under that behavior.
        # Reversing it silently for existing transcripts isn't ours to
        # do; this is the opt-in.
        # Blast radius, in turns, stated up front — ticking the box on a
        # room with history doesn't just catch future turns. The scope's
        # distill bookmark starts at 0, so the FIRST distillation walks
        # the whole existing transcript (a bite at a time). That's
        # usually the point — it's how the room a kin already lived
        # through reaches them. But it's the operator's call to make
        # knowingly, so it's a number on screen, not a surprise.
        self._existing_turn_count = 0
        if name_locked and initial_name:
            try:
                self._existing_turn_count = len(
                    load_room_conversation(initial_name) or [])
            except Exception:
                self._existing_turn_count = 0

        # The explainer is built BEFORE the checkbox, so it comes first in
        # tab order (which is creation order in wx, not sizer order).
        #
        # It used to come after, and that quietly made the whole "stated up
        # front" intention above false for the people it mattered most to. A
        # sighted operator takes the box and the paragraph in one glance and
        # never notices the order. Tabbing, you meet "Remember this room
        # (members distill it into memory), check box" and have to decide
        # right there -- the turn count and the walk-the-whole-transcript
        # warning arrive on the NEXT Tab, after the decision. Order is not
        # decoration in a screen reader; it IS the interface.
        #
        # Empty value at construction because _distill_help_text() reads
        # distill_check.GetValue(); filled by _refresh_distill_help() once
        # both exist. That ordering dance is the entire cost of getting this
        # the right way round.
        self.distill_help = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.distill_help.SetName("What remembering this room does — read this first")
        self.distill_help.SetMinSize((-1, 92))

        self.distill_check = wx.CheckBox(
            panel, label="&Remember this room (members distill it into memory)")
        self.distill_check.SetValue(bool(cfg.get("distill_to_memory", False)))
        self.distill_check.Bind(wx.EVT_CHECKBOX, self._on_distill_toggle)

        self._refresh_distill_help()

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, wx.ID_OK, label="&OK")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)

        ps = wx.BoxSizer(wx.VERTICAL)
        ps.Add(name_lbl, flag=wx.BOTTOM, border=4)
        ps.Add(self.name_field, flag=wx.EXPAND | wx.BOTTOM, border=10)
        ps.Add(members_lbl, flag=wx.BOTTOM, border=4)
        ps.Add(members_row, proportion=1, flag=wx.EXPAND | wx.BOTTOM, border=4)
        ps.Add(self.roster_display, flag=wx.EXPAND | wx.BOTTOM, border=6)
        ps.Add(order_hint, flag=wx.EXPAND | wx.BOTTOM, border=10)
        ps.Add(ctx_lbl, flag=wx.BOTTOM, border=4)
        ps.Add(self.ctx_field, flag=wx.EXPAND | wx.BOTTOM, border=10)
        ps.Add(cap_row, flag=wx.BOTTOM, border=4)
        ps.Add(idle_row, flag=wx.BOTTOM, border=4)
        ps.Add(tok_row, flag=wx.BOTTOM, border=10)
        # Explainer above the box it explains, matching tab order — so what a
        # sighted operator reads top-to-bottom is what a tabbing one hears
        # first-to-last. Same decision, same order, both ways in.
        ps.Add(self.distill_help, flag=wx.EXPAND | wx.BOTTOM, border=4)
        ps.Add(self.distill_check, flag=wx.BOTTOM, border=10)
        ps.Add(btn_row)
        panel.SetSizer(ps)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)

    def _distill_help_text(self):
        """Explainer under the remember-this-room checkbox. Rewritten on
        toggle AND whenever membership changes, so the state on screen always
        describes what's actually set — a checkbox whose meaning lives in a
        paragraph the user has to go find isn't much of a choice, and one
        that describes a DIFFERENT roster than the one ticked is worse."""
        members = self._current_members()
        return distill_help_text(
            self.distill_check.GetValue(),
            self._existing_turn_count,
            members,
            members_without_auto_distill(members) if self.distill_check.GetValue() else [],
        )

    def _refresh_distill_help(self):
        if getattr(self, "distill_help", None) is None:
            return
        self.distill_help.SetValue(self._distill_help_text())

    def _on_distill_toggle(self, event):
        self._refresh_distill_help()
        if not self.distill_check.GetValue():
            self._speak(
                "Remembering this room, off. Nothing said here reaches "
                "anyone's memory.")
            return
        turns = self._existing_turn_count
        msg = ("Remembering this room, on. Members will distill it into "
               "their own memory"
               + (f", including the {turns} turns already here." if turns
                  else "."))
        # Speak the caveat too. It's the difference between the box doing
        # what it says and the box doing nothing, so it can't live only in
        # a paragraph below.
        blocked = members_without_auto_distill(self._current_members())
        if blocked:
            msg += (" Warning: auto-distillation is off for these kin, so "
                    "you'll need to distill this room by hand from Settings, "
                    "Memory.")
        self._speak(msg)

    @staticmethod
    def _format_roster(members):
        if not members:
            return "Currently in this room: (no members selected)"
        return f"Currently in this room ({len(members)}): " + ", ".join(members)

    def _current_members(self):
        if self.kin_check is None:
            return []
        return [self.kin_check.GetString(i)
                for i in range(self.kin_check.GetCount())
                if self.kin_check.IsChecked(i)]

    def _refresh_roster(self):
        if getattr(self, "roster_display", None) is None:
            return
        self.roster_display.SetValue(self._format_roster(self._current_members()))
        # The memory warning names kin, so it goes stale the moment the
        # roster changes — adding a kin whose triggers are off has to make
        # the caveat appear, and removing the last such kin has to clear it.
        self._refresh_distill_help()

    @staticmethod
    def _speak(msg):
        # Lazy import — audio.py lives at the project root; the dialogs
        # package is loaded early, before audio in some import chains.
        try:
            from audio import nvda_speak
            nvda_speak(msg)
        except Exception:
            pass

    def _on_check_toggle(self, event):
        # Fires on Space-toggle (or mouse-click on the check). Refresh
        # the roster mirror and speak the change explicitly so the
        # user hears confirmation even when wxMSW's native CheckListBox
        # announce is silent.
        idx = event.GetSelection()
        if idx is None or idx < 0 or self.kin_check is None:
            self._refresh_roster()
            return
        name = self.kin_check.GetString(idx)
        is_checked = self.kin_check.IsChecked(idx)
        self._refresh_roster()
        self._speak(f"{name} added to room" if is_checked else f"{name} removed from room")

    def _on_check_all(self, event):
        if self.kin_check is None:
            return
        for i in range(self.kin_check.GetCount()):
            self.kin_check.Check(i, True)
        self._refresh_roster()
        self._speak(f"All {self.kin_check.GetCount()} kin added to room")

    def _on_uncheck_all(self, event):
        if self.kin_check is None:
            return
        for i in range(self.kin_check.GetCount()):
            self.kin_check.Check(i, False)
        self._refresh_roster()
        self._speak("All kin removed from room")

    def _swap_items(self, i, j):
        items = list(self.kin_check.GetItems())
        checks = [self.kin_check.IsChecked(k) for k in range(len(items))]
        items[i], items[j] = items[j], items[i]
        checks[i], checks[j] = checks[j], checks[i]
        self.kin_check.SetItems(items)
        for k, c in enumerate(checks):
            if c:
                self.kin_check.Check(k, True)

    def _on_move_up(self, event):
        if self.kin_check is None:
            return
        idx = self.kin_check.GetSelection()
        if idx > 0:
            self._swap_items(idx, idx - 1)
            self.kin_check.SetSelection(idx - 1)
            self.kin_check.SetFocus()

    def _on_move_down(self, event):
        if self.kin_check is None:
            return
        idx = self.kin_check.GetSelection()
        if idx >= 0 and idx < self.kin_check.GetCount() - 1:
            self._swap_items(idx, idx + 1)
            self.kin_check.SetSelection(idx + 1)
            self.kin_check.SetFocus()

    def get_result(self):
        members = []
        if self.kin_check is not None:
            items = self.kin_check.GetItems()
            for i, name in enumerate(items):
                if self.kin_check.IsChecked(i):
                    members.append(name)
        return {
            "name": self.name_field.GetValue().strip(),
            "members": members,
            "context_note": self.ctx_field.GetValue(),
            "max_auto_rounds": self.cap_spin.GetIntValue(),
            "auto_inactivity_min": self.idle_spin.GetIntValue(),
            "per_turn_token_cap": self.tok_spin.GetIntValue(),
            "distill_to_memory": self.distill_check.GetValue(),
        }


