# SPDX-License-Identifier: CC0-1.0
"""dialogs.person_window — one window for one person, on any remote surface.

Telegram and Discord used to have separate hand-built windows, and every new
per-person setting had to be added to each by hand. Discord fell behind that
way. This window is built from tools._person_settings.PERSON_SETTINGS, so both
surfaces always offer the same settings, in the same order, with the same
names. Only the ID field, and the wording where the surfaces really differ,
change between them.

Accessibility rules carried over from the old windows:
  - a radio group's question is a read-only TextCtrl in the tab order, since
    radios name themselves from their own labels and ignore a StaticText;
  - every explainer is a read-only TextCtrl right after its control, so it's
    reachable by Tab;
  - every shortcut letter is used once (checked by the window tests).
"""

import wx

from tools._person_settings import PERSON_SETTINGS

_SURFACE_NAME = {"telegram": "Telegram", "discord": "Discord"}


def _hint(parent, text, name, lines=3):
    t = wx.TextCtrl(parent, value=text,
                    style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
                    | wx.TE_WORDWRAP)
    t.SetName(name)
    t.SetMinSize((-1, 20 + 16 * lines))
    return t


def _wording(surface):
    """Per-surface words. Everything not listed here is the same on both."""
    tg = surface == "telegram"
    where = _SURFACE_NAME[surface]
    return {
        "title": f"{where} person",
        "id_label": ("&User ID (numeric Telegram ID):" if tg else
                     "&User ID (numeric Discord ID, or * for anyone):"),
        "id_hint": (
            "Tip: the person can type /whoami to the bot to find their ID."
            if tg else
            "To find someone's ID: turn on Developer Mode in Discord's "
            "settings (Advanced), then right-click them and choose Copy User "
            "ID. Entering * instead lets ANYONE in the servers this bot has "
            "joined talk to the kin, and every setting below applies to all "
            "of them."),
        "id_hint_name": ("How to find a user's Telegram ID" if tg else
                         "How to find a Discord user ID, and what the star "
                         "means"),
        "label_hint": (
            f"What the kin calls this person. Set it to \"Alex\" and the kin "
            f"sees \"[Alex] hello\". Leave blank to use the name {where} "
            f"gives them."),
        "share_label": (f"S&hare this person's conversation with the "
                        f"Hearthkin desktop (usually only you)"),
        "share_hint": (
            "When on: this person's messages and the kin's replies to them "
            "go into the kin's main conversation, the same one the desktop "
            "uses. It doesn't make them you, and doesn't change their "
            "tool-call limit. Off: their conversation is kept separate."
            if tg else
            "When on: this person's messages and the kin's replies to them "
            "go into the kin's main conversation, the same one the desktop "
            "uses. On Discord the kin still only reads back this channel's "
            "part of the conversation, so your desktop chat is never shown "
            "to a server. It doesn't make them you, and doesn't change their "
            "tool-call limit. Off: kept separate."),
        "is_me_hint": (
            "Tick this only on your own entry. It's the one thing that tells "
            "Hearthkin this person is you: commands that ask first are "
            "asked right here in the chat (handy from your phone), you can "
            "use /reset, and you see full error details. Everyone else's "
            "commands are asked on this computer." if tg else
            "Tick this only on your own entry. It's the one thing that tells "
            "Hearthkin this person is you. On Discord, commands that ask "
            "first are still asked on this computer, never in a server."),
        "mirror_label": (
            "&Mirror desktop messages to this person on Telegram" if tg else
            "&Mirror desktop messages to this person in Discord direct "
            "messages"),
        "mirror_hint": (
            "When on: every desktop message and kin reply for this kin is "
            "also sent to this person, marked as from the desktop. Useful "
            "for yourself, to carry a desktop conversation to your phone. "
            "Off: desktop conversations stay on the desktop."),
        "exec_hint": (
            "Only matters if this person has Full tools. \"Use the kin's "
            "setting\" follows the box \"Run remote (Telegram/Discord) exec "
            "without asking\" in Settings, Tools. \"Ask me first\" always "
            "asks you, never them: "
            + ("in the chat if \"This is me\" is ticked, otherwise in a "
               "window on this computer." if tg else
               "in a window on this computer.")
            + " Commands on the never-run list are always refused."),
    }


class PersonWindow(wx.Dialog):
    """Add or edit one person on one surface. `values` is a dict from
    tools._person_settings.load_person. After ShowModal() == wx.ID_OK, read
    get_user_id() and get_settings(); check cap_is_valid() first."""

    def __init__(self, parent, surface, user_id="", values=None):
        w = _wording(surface)
        super().__init__(parent, title=w["title"],
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.surface = surface
        from tools._person_settings import load_person
        v = dict(load_person({}, surface, ""))
        v.update(values or {})
        self._initial = v

        scroll = wx.ScrolledWindow(self, style=wx.VSCROLL)
        scroll.SetScrollRate(0, 16)
        p = scroll
        outer = wx.BoxSizer(wx.VERTICAL)
        B = 8

        def add(ctrl, flag=wx.LEFT | wx.RIGHT, expand=True):
            outer.Add(ctrl, flag=flag | (wx.EXPAND if expand else 0),
                      border=B)

        # --- who --------------------------------------------------------
        add(wx.StaticText(p, label=w["id_label"]), wx.LEFT | wx.RIGHT | wx.TOP)
        self.id_field = wx.TextCtrl(p, value=str(user_id or ""))
        add(self.id_field)
        add(_hint(p, w["id_hint"], w["id_hint_name"],
                  1 if surface == "telegram" else 5), wx.ALL)

        for s in PERSON_SETTINGS:
            getattr(self, "_build_" + s["key"])(p, add, w, v[s["key"]])

        p.SetSizer(outer)
        outer.Fit(p)

        top = wx.BoxSizer(wx.VERTICAL)
        top.Add(scroll, 1, wx.EXPAND)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        btns.AddStretchSpacer(1)
        ok = wx.Button(self, wx.ID_OK, label="&Save")
        # No shortcut letter: Escape cancels, and "N" belongs to "None".
        cancel = wx.Button(self, wx.ID_CANCEL, label="Cancel")
        ok.SetDefault()
        btns.Add(ok, 0, wx.RIGHT, 6)
        btns.Add(cancel)
        top.Add(btns, 0, wx.EXPAND | wx.ALL, B)
        self.SetSizer(top)
        best = outer.GetMinSize()
        disp = wx.Display(0).GetClientArea() if wx.Display.GetCount() else None
        h = min(best.height + 80, int(disp.height * 0.85)) if disp else 600
        self.SetSize((max(best.width + 40, 520), h))
        self.id_field.SetFocus() if not user_id else self.label_field.SetFocus()

    # --- one builder per setting, in PERSON_SETTINGS order ----------------
    def _build_label(self, p, add, w, value):
        add(wx.StaticText(p, label="&Display name (what the kin calls them):"))
        self.label_field = wx.TextCtrl(p, value=value or "")
        add(self.label_field)
        add(_hint(p, w["label_hint"], "What the display name does"), wx.ALL)

    def _build_is_me(self, p, add, w, value):
        self.is_me_check = wx.CheckBox(p, label="&This is me (the operator)")
        self.is_me_check.SetValue(bool(value))
        add(self.is_me_check, wx.LEFT | wx.RIGHT | wx.TOP)
        add(_hint(p, w["is_me_hint"], "What This is me does", 4), wx.ALL)

    def _build_bucket(self, p, add, w, value):
        add(_hint(p, "Tool access for this person:",
                  "Tool access for this user — heading for the options below",
                  1))
        from tools._buckets import BUCKET_ORDER, BUCKET_EXPLAINER
        self._bucket_radios = {}
        for i, name in enumerate(BUCKET_ORDER):
            rb = wx.RadioButton(
                p, label=f"&{name.capitalize()} — {BUCKET_EXPLAINER.get(name, '')}",
                style=wx.RB_GROUP if i == 0 else 0)
            rb.SetValue(name == value)
            self._bucket_radios[name] = rb
            add(rb, wx.LEFT | wx.RIGHT | wx.BOTTOM)

    def _build_share_desktop(self, p, add, w, value):
        self.share_desktop_check = wx.CheckBox(p, label=w["share_label"])
        self.share_desktop_check.SetValue(bool(value))
        add(self.share_desktop_check, wx.LEFT | wx.RIGHT | wx.TOP)
        add(_hint(p, w["share_hint"], "What sharing with the desktop does", 5),
            wx.ALL)

    def _build_mirror(self, p, add, w, value):
        self.mirror_check = wx.CheckBox(p, label=w["mirror_label"])
        self.mirror_check.SetValue(bool(value))
        add(self.mirror_check, wx.LEFT | wx.RIGHT)
        add(_hint(p, w["mirror_hint"], "What mirroring desktop messages does",
                  4), wx.ALL)

    def _build_webcam(self, p, add, w, value):
        add(_hint(p, "Webcam for this person:",
                  "Webcam permission for this user — heading for the options "
                  "below", 1))
        self._webcam_radios = {}
        opts = [("ask", "&Ask me each time, in a window on this computer"),
                ("auto", "Auto-a&pprove: take the picture without asking"),
                ("deny", "Alwa&ys refuse")]
        for i, (key, text) in enumerate(opts):
            rb = wx.RadioButton(p, label=text,
                                style=wx.RB_GROUP if i == 0 else 0)
            rb.SetValue(key == value)
            self._webcam_radios[key] = rb
            add(rb, wx.LEFT | wx.RIGHT | wx.BOTTOM)
        add(_hint(p, "Only matters when the kin has the webcam tool switched "
                     "on and this person has Write or Full tools.",
                  "When the webcam setting applies", 2), wx.ALL)

    def _build_tool_cap(self, p, add, w, value):
        add(wx.StaticText(p, label="Tool call &limit in one reply for this "
                                   "person:"))
        self.cap_field = wx.TextCtrl(p, value="" if value is None else str(value))
        self.cap_field.SetName(
            "Tool call limit in one reply for this person. Blank uses the "
            "kin's setting; 0 means no limit.")
        add(self.cap_field, expand=False)
        add(_hint(p, "Leave blank to use the kin's own setting (Settings, "
                     "Tools: Most tool calls in one reply over Telegram and Discord). "
                     "A number here applies to this person only. 0 means no "
                     "limit. Each call counts, even several asked for at once; "
                     "past the limit the rest don't run and the kin answers "
                     "with what it has.",
                  "What the tool call limit does", 4), wx.ALL)

    def _build_exec_approval(self, p, add, w, value):
        from tools._buckets import EXEC_APPROVAL_CHOICES
        add(wx.StaticText(p, label="&Commands this person asks for:"))
        self.exec_choice = wx.Choice(p, choices=[
            "Use the kin's setting", "Ask me first", "Run without asking"])
        self.exec_choice.SetSelection(EXEC_APPROVAL_CHOICES.index(value)
                                      if value in EXEC_APPROVAL_CHOICES else 0)
        self.exec_choice.SetName("Commands this person asks for")
        add(self.exec_choice, expand=False)
        add(_hint(p, w["exec_hint"], "What the commands setting does", 4),
            wx.ALL)

    def _build_file_reach(self, p, add, w, value):
        add(wx.StaticText(p, label="Files: kept inside the kin's folder, or "
                                   "allowed &outside:"))
        self.file_choice = wx.Choice(p, choices=[
            "Use the kin's setting", "Keep inside the kin's folder",
            "Allow outside the kin's folder"])
        order = ("default", "inside", "outside")
        self.file_choice.SetSelection(order.index(value) if value in order else 0)
        self.file_choice.SetName("Files: kept inside the kin's folder, or "
                                 "allowed outside")
        add(self.file_choice, expand=False)
        add(_hint(p, "Covers the kin's file tools and files it writes out in "
                     "a reply, when this person asked. \"Use the kin's "
                     "setting\" follows the box \"Let remote (Telegram/"
                     "Discord) file tools reach outside the kin folder\" in "
                     "Settings, Tools.",
                  "What the files setting does", 4), wx.ALL)

    # --- reading back -----------------------------------------------------
    def cap_is_valid(self):
        t = self.cap_field.GetValue().strip()
        return (not t) or t.isdigit()

    # Old name, kept for callers and tests written against the old windows.
    tool_cap_is_valid = cap_is_valid

    def get_user_id(self):
        """Telegram: digits only (a leading '-' kept), with a pasted
        "telegram:" or "tg:" prefix dropped. Discord: as typed. An empty
        string means the ID isn't usable."""
        raw = self.id_field.GetValue().strip()
        if self.surface != "telegram":
            return raw
        for prefix in ("telegram:", "tg:"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):].strip()
        sign = "-" if raw.startswith("-") else ""
        return sign + "".join(c for c in raw if c.isdigit())

    def get_settings(self):
        """{setting key: value} for every setting in PERSON_SETTINGS."""
        from tools._buckets import EXEC_APPROVAL_CHOICES, parse_tool_call_cap
        bucket = next((n for n, rb in self._bucket_radios.items()
                       if rb.GetValue()), "none")
        webcam = next((k for k, rb in self._webcam_radios.items()
                       if rb.GetValue()), "ask")
        ei = self.exec_choice.GetSelection()
        fi = self.file_choice.GetSelection()
        return {
            "label": self.label_field.GetValue().strip(),
            "is_me": bool(self.is_me_check.GetValue()),
            "bucket": bucket,
            "share_desktop": bool(self.share_desktop_check.GetValue()),
            "mirror": bool(self.mirror_check.GetValue()),
            "webcam": webcam,
            "tool_cap": parse_tool_call_cap(self.cap_field.GetValue()),
            "exec_approval": (EXEC_APPROVAL_CHOICES[ei] if 0 <= ei < 3
                              else "default"),
            "file_reach": (("default", "inside", "outside")[fi] if 0 <= fi < 3
                           else "default"),
        }

    # Old helpers, kept for callers written against the old windows.
    def get_tool_cap(self):
        return self.get_settings()["tool_cap"]

    def get_exec_approval(self):
        return self.get_settings()["exec_approval"]
