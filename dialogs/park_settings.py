# SPDX-License-Identifier: CC0-1.0

"""dialogs.park_settings — how a kin plays its Time for Family park.

Two settings, neither of which had any UI before: `park` (how the kin acts in
a park at all) and `park_save` (which park it tends). `park` was JSON-only,
which is why the first keeper had to be configured by hand.

`park_save` is what lets several kin — and the operator — tend ONE park
together instead of each keeping a private one. The cross-process lock in
tools/_game_host.py keys on the save PATH rather than the kin, so co-tenancy is
already safe; this just lets an operator point a kin at a shared file. It also
reaches parks outside Hearthkin entirely: the standalone Time for Family app's
save, including a park a multiplayer server is hosting.

Accessibility (see CLAUDE.md): a StaticText with an &mnemonic immediately
precedes each control so NVDA picks it up as the field's name; explanatory
text sits in read-only TextCtrls so it's reachable by Tab rather than only by
object navigation.
"""

import os

import wx
import wx.lib.scrolledpanel as scrolled

import park_keeper as _PK
from ._shared import _IntField

_MODE_CHOICES = [
    ("off", "Off — this kin has no park"),
    ("chat", "Chat — it can tend while you talk (a '> command' in a reply runs)"),
    ("keeper", "Keeper — tending is its job (scheduled wake-ups are park turns)"),
]


_DEFAULT_PARK_PORT = "8765"


def _split_server_url(url):
    """'http://127.0.0.1:8765' -> ('127.0.0.1', '8765').

    The dialog used to take the whole URL in one box, so joining a server
    meant knowing to write scheme, colon, two slashes, host, colon, port, in
    that order, with no mistakes -- while the server itself is started with
    --host and --port as separate values. Typing punctuation exactly is a
    poor thing to require of anybody and a worse thing to require by ear.
    Forgiving on the way in: scheme optional, port optional, stray spaces
    and a trailing slash ignored.
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return "", ""
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):                      # bracketed IPv6
        host, _, rest = raw.partition("]")
        return host + "]", rest.lstrip(":")
    if raw.count(":") > 1:                       # bare IPv6, no port
        return raw, ""
    host, _, port = raw.partition(":")
    return host.strip(), port.strip()


def _join_server_url(host, port):
    """('127.0.0.1', '8765') -> 'http://127.0.0.1:8765'. '' when no host."""
    host = (host or "").strip().rstrip("/")
    port = (port or "").strip() or _DEFAULT_PARK_PORT
    if not host:
        return ""
    if "://" in host:                            # someone pasted a full URL
        scheme, host = host.split("://", 1)
        host = host.split("/", 1)[0]
    else:
        scheme = "http"
    if ":" in host and not host.startswith("["):
        host = host.split(":", 1)[0]             # port typed into host box
    return "%s://%s:%s" % (scheme, host, port)


class ParkSettingsDialog(wx.Dialog):
    def __init__(self, parent, cfg, save_param, kin_name=""):
        title = "Park settings"
        if kin_name:
            title = f"{title} — {kin_name}"
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(700, 560))
        self.cfg = cfg or {}
        self._save_param = save_param
        self._kin_name = kin_name

        # Everything except the OK/Cancel row lives on a SCROLLING panel.
        # Without it the dialog was a fixed 700x560 with no Fit(), so choosing
        # "a park on a server" revealed seven more controls, the sizer packed
        # them past the bottom edge, and the address / password / name fields
        # simply were not on screen. Nothing announced that: the fields existed,
        # were "shown", and could not be reached. A dialog whose contents change
        # size has to be able to scroll, or it silently loses controls.
        panel = scrolled.ScrolledPanel(self)
        self._panel = panel
        outer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            panel, value=(
                "Time for Family is a small creature park a kin can keep. "
                "Turn it on here and choose whether this kin tends its own "
                "park or shares one with you and the other kin.\n\n"
                "Sharing means exactly that: the same creatures, the same "
                "rooms. What one of you does, the others see on their next "
                "turn. Turns are serialized behind the scenes, so two of you "
                "acting at once can't overwrite each other."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        intro.SetName("About parks")
        intro.SetMinSize((-1, 110))
        outer.Add(intro, 0, wx.EXPAND | wx.ALL, 8)

        # ── How this kin plays ───────────────────────────────────────
        outer.Add(wx.StaticText(panel, label="How this kin &plays:"),
                  0, wx.LEFT | wx.TOP, 8)
        self.mode_choice = wx.Choice(
            panel, choices=[label for _v, label in _MODE_CHOICES])
        self.mode_choice.SetName("How this kin plays")
        cur = str(self.cfg.get("park", "off")).strip().lower()
        idx = next((i for i, (v, _l) in enumerate(_MODE_CHOICES) if v == cur), 0)
        self.mode_choice.SetSelection(idx)
        outer.Add(self.mode_choice, 0, wx.EXPAND | wx.ALL, 8)

        # ── How much it may do in one turn ────────────────────────────
        # This was a constant in the code and the answer was 1, which meant a
        # kin could not look at a room and then act on what it saw: the single
        # move it had got spent on the look. Now it plays on until it stops
        # asking for anything, up to this many moves.
        outer.Add(wx.StaticText(panel, label="&Moves it may take in one turn:"),
                  0, wx.LEFT | wx.TOP, 8)
        self.moves_field = _IntField(
            panel,
            value=int(self.cfg.get("park_moves_max",
                                   _PK.DEFAULT_PARK_MOVES_MAX) or 0),
            min_val=0, max_val=999,
            name="Moves it may take in one turn")
        outer.Add(self.moves_field, 0, wx.LEFT | wx.BOTTOM, 8)
        _moves_note = wx.TextCtrl(
            panel,
            value=("After each move the kin is shown what happened and asked "
                   "what it wants to do next, until it stops asking or reaches "
                   "this number. It usually stops well before — a reply with "
                   "no move in it means it's finished for now. Set it to 0 for "
                   "no limit at all. Every move is a fresh call to the kin's "
                   "model, so on a slow local model a large number here is a "
                   "long wait."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL,
            size=(-1, 76))
        _moves_note.SetName("About moves per turn")
        outer.Add(_moves_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # ── The last-resort cap ──────────────────────────────────────
        # Answering the game's own questions doesn't cost a move, which is what
        # lets a twelve-question walkthrough finish in one turn. This is the
        # stop behind that, counting everything. It was a constant, which put a
        # number nobody could reach in charge of when a kin stops.
        outer.Add(wx.StaticText(
            panel, label="Most moves of &any kind in one turn:"),
            0, wx.LEFT | wx.TOP, 8)
        self.hard_stop_field = _IntField(
            panel,
            value=int(self.cfg.get("park_answer_hard_stop",
                                   _PK.ANSWER_HARD_STOP) or 0),
            min_val=0, max_val=9999,
            name="Most moves of any kind in one turn")
        outer.Add(self.hard_stop_field, 0, wx.LEFT | wx.BOTTOM, 8)
        _hard_note = wx.TextCtrl(
            panel,
            value=("Answering the game's own questions is free — a kin can "
                   "work through a long walkthrough without spending any of "
                   "the moves above, which is what lets it finish making a "
                   "creature in one message. This is the stop behind that, and "
                   "it counts everything: it only matters if a kin somehow "
                   "answers so badly that the questions never end. The default "
                   "is far above any real walkthrough, so you'd not normally "
                   "see it. Set it to 0 for no cap."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL,
            size=(-1, 92))
        _hard_note.SetName("About the last-resort cap")
        outer.Add(_hard_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # ── Which park ───────────────────────────────────────────────
        outer.Add(wx.StaticText(panel, label="Which park it &tends:"),
                  0, wx.LEFT | wx.TOP, 8)
        self.own_radio = wx.RadioButton(
            panel, label="This &kin's own park (private to it)",
            style=wx.RB_GROUP)
        self.shared_radio = wx.RadioButton(
            panel, label="A shared park &file — no server: several kin on THIS "
                          "computer tend one save directly")
        self.server_radio = wx.RadioButton(
            panel, label="A park on a s&erver — join a running Time for Family "
                          "game over the network. Use this one if you have the "
                          "server running.")
        outer.Add(self.own_radio, 0, wx.LEFT | wx.RIGHT, 16)
        outer.Add(self.shared_radio, 0, wx.LEFT | wx.RIGHT, 16)
        outer.Add(self.server_radio, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        self.path_label = wx.StaticText(panel, label="Shared park &file:")
        outer.Add(self.path_label, 0, wx.LEFT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.path_field = wx.TextCtrl(panel, value=self.cfg.get("park_save", ""))
        self.path_field.SetName("Shared park file")
        self.browse_btn = wx.Button(panel, label="B&rowse…")
        row.Add(self.path_field, 1, wx.EXPAND | wx.RIGHT, 6)
        row.Add(self.browse_btn, 0)
        outer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        _host0, _port0 = _split_server_url(self.cfg.get("park_server", ""))
        self.server_label = wx.StaticText(
            panel, label="Server &address (name or IP, e.g. 127.0.0.1):")
        outer.Add(self.server_label, 0, wx.LEFT, 8)
        self.server_field = wx.TextCtrl(panel, value=_host0)
        self.server_field.SetName("Server address")
        outer.Add(self.server_field, 0, wx.EXPAND | wx.ALL, 8)

        self.port_label = wx.StaticText(
            panel, label="Server p&ort (the server prints it; usually 8765):")
        outer.Add(self.port_label, 0, wx.LEFT, 8)
        self.port_field = wx.TextCtrl(panel, value=_port0)
        self.port_field.SetName("Server port")
        outer.Add(self.port_field, 0, wx.EXPAND | wx.ALL, 8)

        self.pw_label = wx.StaticText(panel, label="Server pass&word:")
        outer.Add(self.pw_label, 0, wx.LEFT, 8)
        self.pw_field = wx.TextCtrl(panel, value=self.cfg.get("park_password", ""))
        self.pw_field.SetName("Server password")
        outer.Add(self.pw_field, 0, wx.EXPAND | wx.ALL, 8)

        self.player_label = wx.StaticText(panel, label="Your &name in the park:")
        outer.Add(self.player_label, 0, wx.LEFT, 8)
        self.player_field = wx.TextCtrl(
            panel, value=self.cfg.get("park_player", ""))
        self.player_field.SetName("Your name in the park")
        outer.Add(self.player_field, 0, wx.EXPAND | wx.ALL, 8)

        self.test_btn = wx.Button(panel, label="&Verify connection")
        outer.Add(self.test_btn, 0, wx.LEFT | wx.BOTTOM, 8)

        outer.Add(wx.StaticText(panel, label="Park file stat&us:"), 0, wx.LEFT, 8)
        self.status = wx.TextCtrl(
            panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.status.SetName("Park file status")
        self.status.SetMinSize((-1, 76))
        outer.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)

        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "&OK")
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, "&Cancel"))
        btns.Realize()
        panel.SetSizer(outer)
        panel.SetupScrolling(scroll_x=False, scrollToTop=False)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, 1, wx.EXPAND)
        frame_sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(frame_sizer)

        if (self.cfg.get("park_server") or "").strip():
            self.server_radio.SetValue(True)
        elif (self.cfg.get("park_save") or "").strip():
            self.shared_radio.SetValue(True)
        else:
            self.own_radio.SetValue(True)

        self.own_radio.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
        self.shared_radio.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
        self.server_radio.Bind(wx.EVT_RADIOBUTTON, self._on_scope)
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        self.browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        self.path_field.Bind(wx.EVT_TEXT, lambda _e: self._refresh_status())
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        self._on_scope(None)

    # ---- helpers ----

    def _sharing(self):
        return self.shared_radio.GetValue()

    def _serving(self):
        return self.server_radio.GetValue()

    def _on_scope(self, _evt):
        # Hide rather than grey out: a disabled control still lands in the tab
        # order and reads as a dead end to a screen reader.
        # Each field's LABEL has to travel with it. Hiding the field alone
        # leaves the label behind announcing a control that isn't there --
        # worse than greying out, not better: a screen reader reads "Server
        # password:" and then lands on whatever the next control happens to
        # be, so the operator hears a label attached to something else and
        # concludes there is nowhere to type the password. Reported live as
        # "I saw no place to enter the port, or the password, or anything".
        for w in (self.path_label, self.path_field, self.browse_btn):
            w.Show(self._sharing())
        for w in (self.server_label, self.server_field,
                  self.port_label, self.port_field,
                  self.pw_label, self.pw_field,
                  self.player_label, self.player_field,
                  self.test_btn):
            w.Show(self._serving())
        self._panel.Layout()
        self._panel.SetupScrolling(scroll_x=False, scrollToTop=False)
        self.Layout()
        self._refresh_status()

    def _on_test(self, _evt):
        """Ask the server whether it's there and whether the password works,
        before the kin's first turn rather than after a silent failure."""
        from tools import get_game
        probe = dict(self.cfg)
        probe["park_server"] = _join_server_url(
            self.server_field.GetValue(), self.port_field.GetValue())
        probe["park_password"] = self.pw_field.GetValue() or ""
        probe["park_player"] = (self.player_field.GetValue() or "").strip()
        import kin_persistence as k
        real = k.load_agent_config
        k.load_agent_config = lambda _n: probe
        try:
            ok, msg = get_game("tff").server_ping(self._kin_name or "kin")
        except Exception as e:
            ok, msg = False, f"Couldn't test that: {e}"
        finally:
            k.load_agent_config = real
        self.status.SetValue(("" if ok else "Not connected. ") + msg)
        try:
            from audio import nvda_speak
            nvda_speak(msg)
        except Exception:
            pass

    def _refresh_status(self):
        if self._serving():
            url = _join_server_url(self.server_field.GetValue(),
                                   self.port_field.GetValue())
            self.status.SetValue(
                "This kin joins a park running on a server — the same one you "
                "and anyone else can connect a console to. The server owns the "
                "park, so nothing is stored here." + os.linesep + os.linesep
                + ("Press Verify connection to check it's reachable."
                   if url else
                   "Enter the address and port the server prints when it "
                   "starts, and the password it was given."))
            return
        if not self._sharing():
            self.status.SetValue(
                "This kin keeps its own park, in its own folder. Nobody else "
                "tends it.")
            return
        p = (self.path_field.GetValue() or "").strip()
        if not p:
            self.status.SetValue(
                "Choose a park file to share. Point several kin at the same "
                "file and they'll all tend that one park together.")
            return
        path = os.path.expanduser(p)
        if os.path.exists(path):
            kb = os.path.getsize(path) // 1024
            served = os.path.exists(path + ".lock")
            warn = (os.linesep + os.linesep +
                    "Heads up: something has a lock on this file, which "
                    "usually means a Time for Family SERVER is serving this "
                    "park. If so, pick \"A park on a server\" above instead "
                    "and give it the address — that way the server stays the "
                    "only thing writing the file. Two writers on one save is "
                    "not what this mode is for." if served else "")
            self.status.SetValue(
                f"Found it ({kb} KB). This kin will tend this park directly, "
                f"and will be told what you and any other kin did there since "
                f"its last turn. No server is involved." + warn)
        elif os.path.isdir(os.path.dirname(path) or "."):
            self.status.SetValue(
                "That file doesn't exist yet. It will be created as a new "
                "empty park the first time this kin looks at it.")
        else:
            self.status.SetValue(
                "That folder doesn't exist, so this setting will be ignored "
                "and the kin will keep its own park instead. Check the path.")

    def _on_browse(self, _evt):
        dlg = wx.FileDialog(
            self, "Choose a park save file", wildcard="Park saves (*.json)|*.json",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                self.path_field.SetValue(dlg.GetPath())
                self._refresh_status()
        finally:
            dlg.Destroy()

    def _on_ok(self, evt):
        mode = _MODE_CHOICES[max(self.mode_choice.GetSelection(), 0)][0]
        self._save_param("park", mode)
        self._save_param("park_moves_max", self.moves_field.GetIntValue())
        self._save_param("park_answer_hard_stop",
                         self.hard_stop_field.GetIntValue())
        # Exactly one scope wins; the others are cleared so a stale value can't
        # quietly take precedence later (server beats shared file in GameHost).
        self._save_param(
            "park_save",
            (self.path_field.GetValue() or "").strip() if self._sharing() else "")
        serving = self._serving()
        self._save_param(
            "park_server",
            _join_server_url(self.server_field.GetValue(),
                             self.port_field.GetValue()) if serving else "")
        self._save_param(
            "park_password", (self.pw_field.GetValue() or "") if serving else "")
        self._save_param(
            "park_player",
            (self.player_field.GetValue() or "").strip() if serving else "")
        evt.Skip()
