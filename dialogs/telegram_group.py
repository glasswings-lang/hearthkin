# SPDX-License-Identifier: CC0-1.0

"""dialogs.telegram_group - extracted from the former monolithic dialogs.py."""

import wx


class _TelegramGroupDialog(wx.Dialog):
    """Modal for adding or editing one Telegram-group entry on a
    kin. Three fields: chat ID (negative integer), display label,
    participation policy radio.

    The display label is NOT cosmetic — the kin sees it in the
    system prompt's room context ("You are participating in a
    Telegram group called <label>"). Blank = Telegram's own group
    title is used as the fallback, falling back further to
    "group <chat_id>" if Telegram doesn't supply one.

    Returns (chat_id_str, label_str, policy_name, share_desktop_bool,
    exclude_ids) via get_values() when ShowModal returns wx.ID_OK. Caller
    writes back to cfg.telegram.groups (label / policy / exclude) +
    cfg.telegram.group_share_desktop and persists. chat_id is normalized to
    a leading minus sign plus digits — group chat IDs from Telegram are
    always negative.

    The exclude list is per-group mute: the kin talks to everyone in an
    opted-in group EXCEPT these user_ids. It is independent of DM access
    (cfg.telegram.allow_from) — muting someone in a group doesn't touch
    whether they can DM the kin, and vice versa.

    Tab order: chat_id field → label field → policy radios → Save → Cancel.
    """

    def __init__(self, parent, chat_id="", label="", policy="mention_only",
                 share_desktop=False, exclude=None, seen_members=None):
        super().__init__(parent, title="Telegram group",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._policy = policy or "mention_only"
        self._share_desktop_initial = bool(share_desktop)
        # Muted members: user_ids the kin will NOT respond to in this group,
        # even though it talks to everyone else here. Independent of DM access.
        self._exclude_ids = []
        for x in (exclude or []):
            try:
                self._exclude_ids.append(int(str(x).strip()))
            except (TypeError, ValueError):
                pass
        # id -> display name, harvested from this group's stored history so the
        # operator picks names, not numbers. Includes anyone already muted even
        # if they've since gone quiet, so the list still reads with names.
        self._seen = {}
        for m in (seen_members or []):
            try:
                self._seen[int(m["id"])] = (m.get("name") or "").strip()
            except (TypeError, ValueError, KeyError):
                pass

        outer = wx.BoxSizer(wx.VERTICAL)

        id_lbl = wx.StaticText(
            self,
            label="&Chat ID (negative number, e.g. -100123456789):",
        )
        self.id_field = wx.TextCtrl(self, value=chat_id)
        outer.Add(id_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.id_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        # The only statement of how to obtain the ID the field above demands.
        # The next field takes its name from its own StaticText below, so this
        # one labels nothing and was reaching sighted users only.
        id_hint = wx.TextCtrl(
            self,
            value=(
                "To find a group's chat ID: add the bot to the group "
                "in Telegram, then send /whoami@<BotUsername> there. "
                "The bot replies with the negative chat ID."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        id_hint.SetName("How to find a group's chat ID")
        id_hint.SetMinSize((-1, 60))
        outer.Add(id_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        label_lbl = wx.StaticText(
            self,
            label="&Display label (kin sees this as the group's name):",
        )
        self.label_field = wx.TextCtrl(self, value=label)
        outer.Add(label_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.label_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        # Says the label is not cosmetic — the kin reads it as the group's name.
        # The policy radios below use their own labels, so this named nothing
        # and was unreachable from the keyboard.
        label_hint = wx.TextCtrl(
            self,
            value=(
                "Inlined into the kin's room context: \"You are "
                "participating in a Telegram group called <label>.\" "
                "Leave blank to use Telegram's own group title."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        label_hint.SetName("What the display label does")
        label_hint.SetMinSize((-1, 45))
        outer.Add(label_hint,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # The policy radios below name themselves from their own labels and
        # ignore a preceding StaticText, so as a StaticText this question was
        # spoken to nobody — you'd hear both options with no idea what they
        # decide. A read-only TextCtrl is in the tab order.
        policy_lbl = wx.TextCtrl(
            self,
            value="Participation policy:",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        policy_lbl.SetName("Participation policy — heading for the options below")
        policy_lbl.SetMinSize((-1, 24))
        outer.Add(policy_lbl, flag=wx.LEFT | wx.RIGHT, border=8)

        # Policy options as independent radio buttons so NVDA reads
        # each one's label and explainer separately. Two options for
        # now: mention_only (privacy-mode-compatible default) and
        # always (requires BotFather privacy mode off).
        self._policy_radios = {}
        mention_rb = wx.RadioButton(
            self,
            label=(
                "&Mention-only — kin replies when @-mentioned or when "
                "someone replies to one of its messages. Works with "
                "Telegram's default bot privacy mode."
            ),
            style=wx.RB_GROUP,
        )
        mention_rb.SetValue(self._policy == "mention_only")
        self._policy_radios["mention_only"] = mention_rb
        outer.Add(mention_rb, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        always_rb = wx.RadioButton(
            self,
            label=(
                "Al&ways — kin sees every group message and decides "
                "whether to engage. REQUIRES disabling privacy mode in "
                "BotFather first (/mybots → Bot Settings → Group "
                "Privacy → Turn off), otherwise Telegram won't deliver "
                "non-mention messages to the bot."
            ),
        )
        always_rb.SetValue(self._policy == "always")
        self._policy_radios["always"] = always_rb
        outer.Add(always_rb, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # Share-with-desktop toggle: merges this group's history into
        # the kin's main conversation.jsonl, parallel to the per-user
        # toggle. When on, wiping conversation.jsonl on the desktop
        # also wipes this group's history. When off (default), the
        # group keeps its segregated slice in telegram_history.json.
        self.share_desktop_check = wx.CheckBox(
            self,
            label=(
                "&Share with desktop — unify this group's history "
                "with the kin's main conversation. When on, group "
                "messages live in conversation.jsonl (so the desktop "
                "sees them and clear-chat / regen on the desktop "
                "affects this group too). Default off keeps groups "
                "isolated, which is right for multi-participant "
                "groups where the operator's desktop content "
                "shouldn't leak in."
            ),
        )
        self.share_desktop_check.SetValue(self._share_desktop_initial)
        outer.Add(self.share_desktop_check,
                  flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # ─── Muted members (per-group exclude list) ──────────────
        # The kin talks to everyone in this group by default; this list is
        # the exception. It does NOT affect who can DM the kin — that's the
        # separate "Users allowed to DM" list on the Telegram tab.
        muted_lbl = wx.StaticText(
            self, label="&Muted in this group (kin ignores these people here):")
        outer.Add(muted_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        self.muted_list = wx.ListBox(self, style=wx.LB_SINGLE)
        # The SetName wins over the StaticText above it, so it has to carry the
        # whole label — a bare "Muted in this group" drops what muting does and
        # that half then reaches sighted users only.
        self.muted_list.SetName("Muted in this group (kin ignores these people here)")
        self.muted_list.SetMinSize((-1, 90))
        outer.Add(self.muted_list,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        # Bounds what muting does — the mute list reads as an allow-list
        # otherwise. The next control is a button, which names itself from its
        # label, so as a StaticText this was unreachable from the keyboard.
        muted_hint = wx.TextCtrl(
            self,
            value=(
                "Everyone else in this group can talk to the kin (subject to "
                "the participation policy above). Muting someone here has no "
                "effect on whether they can DM the kin privately."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        muted_hint.SetName("What muting does and doesn't affect")
        muted_hint.SetMinSize((-1, 60))
        outer.Add(muted_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        muted_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        add_seen_btn = wx.Button(self, label="Add from &people seen here…")
        add_seen_btn.Bind(wx.EVT_BUTTON, self._on_add_from_seen)
        add_id_btn = wx.Button(self, label="Add by &ID…")
        add_id_btn.Bind(wx.EVT_BUTTON, self._on_add_by_id)
        unmute_btn = wx.Button(self, label="&Unmute")
        unmute_btn.Bind(wx.EVT_BUTTON, self._on_unmute)
        muted_btn_row.Add(add_seen_btn, flag=wx.RIGHT, border=6)
        muted_btn_row.Add(add_id_btn, flag=wx.RIGHT, border=6)
        muted_btn_row.Add(unmute_btn)
        outer.Add(muted_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        self._refresh_muted_list()

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        ok_btn = wx.Button(self, wx.ID_OK, label="&Save")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        ok_btn.SetDefault()
        btn_row.Add(ok_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)

        self.SetSizer(outer)
        self.Fit()
        if not chat_id:
            self.id_field.SetFocus()
        else:
            self.label_field.SetFocus()

    def _muted_label(self, uid):
        name = self._seen.get(uid, "")
        return f"{name} ({uid})" if name else f"(no name yet) ({uid})"

    def _refresh_muted_list(self):
        self.muted_list.Set([self._muted_label(u) for u in self._exclude_ids])

    def _on_add_from_seen(self, event):
        # Offer the people this kin has heard in this group, minus anyone
        # already muted. Names, not numbers — that's the whole point.
        candidates = [u for u in self._seen if u not in self._exclude_ids]
        candidates.sort(key=lambda u: ((self._seen.get(u) or "").lower(), u))
        if not candidates:
            wx.MessageBox(
                "Nobody to add yet — the kin hasn't recorded anyone speaking "
                "in this group, or everyone seen is already muted. Once "
                "people talk in the group they'll show up here, or use "
                "\"Add by ID…\".",
                "Add from people seen here", wx.OK | wx.ICON_INFORMATION)
            return
        choices = [self._muted_label(u) for u in candidates]
        dlg = wx.MultiChoiceDialog(
            self, "Mute these people in this group:",
            "People seen in this group", choices)
        if dlg.ShowModal() == wx.ID_OK:
            for i in dlg.GetSelections():
                self._exclude_ids.append(candidates[i])
            self._refresh_muted_list()
        dlg.Destroy()

    def _on_add_by_id(self, event):
        dlg = wx.TextEntryDialog(
            self,
            "Telegram user ID to mute in this group (a positive number; the "
            "person can send /whoami to the bot to find theirs):",
            "Add by ID")
        if dlg.ShowModal() == wx.ID_OK:
            raw = dlg.GetValue().strip()
            for prefix in ("telegram:", "tg:"):
                if raw.lower().startswith(prefix):
                    raw = raw[len(prefix):].strip()
            digits = "".join(c for c in raw if c.isdigit())
            if digits:
                uid = int(digits)
                if uid not in self._exclude_ids:
                    self._exclude_ids.append(uid)
                    self._refresh_muted_list()
            else:
                wx.MessageBox("That wasn't a numeric user ID.", "Add by ID",
                              wx.OK | wx.ICON_WARNING)
        dlg.Destroy()

    def _on_unmute(self, event):
        idx = self.muted_list.GetSelection()
        if 0 <= idx < len(self._exclude_ids):
            self._exclude_ids.pop(idx)
            self._refresh_muted_list()

    def get_values(self):
        """Return (chat_id, label, policy, share_desktop, exclude). chat_id is
        normalized to a leading '-' plus digits (group IDs are always
        negative in Telegram); empty string means invalid input. `exclude` is
        the list of muted user_ids (ints)."""
        raw = self.id_field.GetValue().strip()
        for prefix in ("telegram:", "tg:"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):].strip()
        sign = ""
        if raw.startswith("-"):
            sign = "-"
            raw = raw[1:]
        cid = sign + "".join(c for c in raw if c.isdigit())
        label = self.label_field.GetValue().strip()
        policy = "mention_only"
        for name, rb in self._policy_radios.items():
            if rb.GetValue():
                policy = name
                break
        share_desktop = bool(self.share_desktop_check.GetValue())
        return cid, label, policy, share_desktop, list(self._exclude_ids)


