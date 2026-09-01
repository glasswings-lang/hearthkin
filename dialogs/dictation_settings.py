"""Dictation settings — the transcription model, and where it lives.

A transcription model is chosen here the same way a chat model or a
distillation model is chosen elsewhere in this app: a model name, plus
the machine it runs on. An empty machine means "this computer". That is
the whole idea, and everything on this screen is in service of it.

Any machine speaking the ordinary OpenAI `/v1/audio/transcriptions`
interface can be named, so "put the transcription model wherever you
like" is one address in a box rather than a second feature. The screen
can ask a named machine what models it has, because typing a model name
from memory and hoping is not a thing to make somebody do.

App-level rather than per-kin, deliberately: this is about your voice
and your microphone, which do not change depending on who you are
talking to. Per-kin would mean setting it up again for every kin, and
forgetting once would look like the Talk button being broken for that
one kin.

Flat, with a heading per section rather than tabs — nesting tabs inside
a dialog adds a level of screen-reader depth for no gain. Sections that
do not apply are hidden AND disabled, not greyed out: a disabled control
still sits in the tab order, so greying it out leaves something to tab
into that explains nothing about why it does not work.

Widgets are created in the order they should be tabbed through, because
wxPython builds tab order from creation order, not from sizer order.
"""

import threading

import wx
import wx.lib.scrolledpanel as scrolled

import stt


# The three places a transcription model can live. This is a view of
# stt.route_for, not a second source of truth — _collect turns a choice
# here back into a (model, host) pair, and route_for is what decides.
_WHERE_CHOICES = [
    (stt.ROUTE_LOCAL,
     "On this computer (free, offline, no account)"),
    (stt.ROUTE_SERVER,
     "On another machine or service you name"),
    (stt.ROUTE_ELEVENLABS,
     "ElevenLabs Scribe (paid, needs an API key)"),
]

_DEVICE_CHOICES = [
    ("auto", "Choose automatically (graphics card if it will fit)"),
    ("cuda", "Graphics card"),
    ("cpu", "Processor"),
]


class DictationSettingsDialog(wx.Dialog):
    """Edit the app-level dictation settings.

    `config` is the live app config dict; `save` persists it. Both come
    from the frame, matching the sound-cues dialog, so a change here is
    saved the same way every other app-level preference is.
    """

    def __init__(self, parent, config, save):
        super().__init__(parent, title="Dictation settings",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.config = config
        self._save = save
        self._d = self._merged()
        self._server_models = []

        # Scrolled, because this screen is tall and a control that is
        # off the bottom of a fixed dialog cannot be tabbed to at all.
        panel = scrolled.ScrolledPanel(self)
        self._panel = panel
        sizer = wx.BoxSizer(wx.VERTICAL)

        blurb = wx.TextCtrl(
            panel,
            value=("Dictation puts what you say into the message box, so you "
                   "can speak to a kin instead of typing. Press Talk, speak, "
                   "then press Stop talking. What was heard is read back to "
                   "you and left in the box, so you can fix a wrong word "
                   "before sending it.\n\n"
                   "The transcription model is chosen the same way a chat "
                   "model is: a model, and the machine it runs on. By "
                   "default it runs on this computer, for free, with nothing "
                   "sent anywhere and no account needed. A graphics card "
                   "makes it quicker but is not needed — it uses the "
                   "processor otherwise."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        blurb.SetMinSize((-1, 150))
        blurb.SetName("About dictation")
        sizer.Add(blurb, flag=wx.EXPAND | wx.ALL, border=10)

        # --- What is doing the transcribing, in one line -------------
        current_label = wx.StaticText(panel, label="Transcription model &now:")
        self.current_field = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.current_field.SetName("Transcription model in use")
        sizer.Add(current_label, flag=wx.LEFT | wx.RIGHT, border=10)
        sizer.Add(self.current_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # --- Where it runs -------------------------------------------
        where_label = wx.StaticText(panel, label="&Where it runs:")
        self.where_choice = wx.Choice(
            panel, choices=[lbl for _k, lbl in _WHERE_CHOICES])
        self.where_choice.SetSelection(self._index_of(
            _WHERE_CHOICES, stt.route_for(self._d.get("model"),
                                          self._d.get("host")), 0))
        self.where_choice.Bind(wx.EVT_CHOICE, self._on_where)
        sizer.Add(where_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        sizer.Add(self.where_choice,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # --- On this computer ----------------------------------------
        self.local_box = wx.BoxSizer(wx.VERTICAL)

        model_label = wx.StaticText(panel, label="Speech &model:")
        self._model_keys = [k for k, _lbl in stt.MODEL_CHOICES]
        self.model_choice = wx.Choice(panel, choices=self._model_labels())
        self.model_choice.SetSelection(
            self._index_of([(k, "") for k in self._model_keys],
                           self._d.get("model"), 1))
        self.model_choice.Bind(wx.EVT_CHOICE, self._on_any_change)
        self.local_box.Add(model_label, flag=wx.LEFT | wx.RIGHT, border=10)
        self.local_box.Add(self.model_choice,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                           border=10)

        device_label = wx.StaticText(panel, label="&Run it on:")
        self.device_choice = wx.Choice(
            panel, choices=[lbl for _k, lbl in _DEVICE_CHOICES])
        self.device_choice.SetSelection(self._index_of(
            _DEVICE_CHOICES, self._d.get("device"), 0))
        device_note = wx.TextCtrl(
            panel,
            value=("Left to choose for itself it uses the graphics card when "
                   "there is room and the processor otherwise, which is "
                   "normal and quite fast enough. A card busy holding a "
                   "language model is the ordinary state of a machine like "
                   "this, not a fault."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        device_note.SetMinSize((-1, 66))
        device_note.SetName("About which part of the computer runs it")
        self.local_box.Add(device_label, flag=wx.LEFT | wx.RIGHT, border=10)
        self.local_box.Add(self.device_choice,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)
        self.local_box.Add(device_note,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                           border=10)

        self.preload_check = wx.CheckBox(
            panel,
            label="Get the speech model ready when Hearthkin &starts")
        self.preload_check.SetValue(bool(self._d.get("preload", True)))
        preload_note = wx.TextCtrl(
            panel,
            value=("Loading it the first time takes a little while. Doing "
                   "that at startup means the first thing you dictate does "
                   "not have to wait for it."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        preload_note.SetMinSize((-1, 52))
        preload_note.SetName("About getting the model ready at startup")
        self.local_box.Add(self.preload_check,
                           flag=wx.LEFT | wx.RIGHT, border=10)
        self.local_box.Add(preload_note,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                           border=10)
        sizer.Add(self.local_box, flag=wx.EXPAND)

        # --- On another machine --------------------------------------
        self.server_box = wx.BoxSizer(wx.VERTICAL)

        host_label = wx.StaticText(panel, label="Machine &address:")
        self.host_field = wx.TextCtrl(
            panel, value=str(self._d.get("host") or ""))
        self.host_field.Bind(wx.EVT_TEXT, self._on_any_change)
        host_note = wx.TextCtrl(
            panel,
            value=("Any machine or service that speaks the usual "
                   "transcription interface — for example "
                   "http://192.168.1.20:8080 for a box on your network, or "
                   "the address of a hosted service. Nothing has to be "
                   "installed on this computer for it, and the machine "
                   "doing the work does not need a graphics card either. "
                   "whisper.cpp's server, speaches and "
                   "faster-whisper-server all work."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        host_note.SetMinSize((-1, 96))
        host_note.SetName("About the machine address")
        self.server_box.Add(host_label, flag=wx.LEFT | wx.RIGHT, border=10)
        self.server_box.Add(self.host_field,
                            flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)
        self.server_box.Add(host_note,
                            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                            border=10)

        key_label = wx.StaticText(
            panel, label="Machine &key (leave empty if it needs none):")
        self.host_key_field = wx.TextCtrl(
            panel, value=str(self._d.get("host_key") or ""),
            style=wx.TE_PASSWORD)
        self.server_box.Add(key_label, flag=wx.LEFT | wx.RIGHT, border=10)
        self.server_box.Add(self.host_key_field,
                            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                            border=10)

        # Asking the machine what it has, rather than making somebody
        # type a model name from memory and find out later that it was
        # wrong. Falls back to typing it, because not every server
        # answers this question.
        self.ask_btn = wx.Button(panel, label="As&k that machine what it has")
        self.ask_btn.Bind(wx.EVT_BUTTON, self._on_ask_server)
        self.server_box.Add(self.ask_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM,
                            border=10)

        server_model_label = wx.StaticText(
            panel, label="Model name &on that machine:")
        self.server_model_field = wx.TextCtrl(
            panel, value=self._initial_server_model())
        self.server_model_field.Bind(wx.EVT_TEXT, self._on_any_change)
        self.server_box.Add(server_model_label, flag=wx.LEFT | wx.RIGHT,
                            border=10)
        self.server_box.Add(self.server_model_field,
                            flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        self.server_model_list = wx.ListBox(panel, choices=[])
        self.server_model_list.SetName("Models that machine offers")
        self.server_model_list.Bind(wx.EVT_LISTBOX, self._on_pick_server_model)
        self.server_model_list.SetMinSize((-1, 110))
        self.server_box.Add(self.server_model_list,
                            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                            border=10)
        sizer.Add(self.server_box, flag=wx.EXPAND)

        # --- Applies to any of them ----------------------------------
        lang_label = wx.StaticText(
            panel, label="&Language you speak (empty = work it out):")
        self.lang_field = wx.TextCtrl(
            panel, value=str(self._d.get("language") or ""))
        lang_note = wx.TextCtrl(
            panel,
            value=("A two-letter code such as en, fr, de. Naming it is faster "
                   "and steadier than leaving it to be worked out, which can "
                   "guess wrong on a short phrase."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        lang_note.SetMinSize((-1, 52))
        lang_note.SetName("About the language setting")
        sizer.Add(lang_label, flag=wx.LEFT | wx.RIGHT, border=10)
        sizer.Add(self.lang_field,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)
        sizer.Add(lang_note,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        self.autosend_check = wx.CheckBox(
            panel, label="Send stra&ight away, without letting me check it first")
        self.autosend_check.SetValue(bool(self._d.get("auto_send", False)))
        autosend_note = wx.TextCtrl(
            panel,
            value=("Off by default. A transcript you cannot correct before it "
                   "is sent is a worse deal than typing."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        autosend_note.SetMinSize((-1, 40))
        autosend_note.SetName("About sending straight away")
        sizer.Add(self.autosend_check, flag=wx.LEFT | wx.RIGHT, border=10)
        sizer.Add(autosend_note,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # --- Check it works ------------------------------------------
        self.check_btn = wx.Button(panel, label="&Check dictation")
        self.check_btn.Bind(wx.EVT_BUTTON, self._on_check)
        self.check_field = wx.TextCtrl(
            panel, value="Not checked yet.",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
                  | wx.TE_NO_VSCROLL)
        self.check_field.SetMinSize((-1, 90))
        self.check_field.SetName("Dictation check result")
        sizer.Add(self.check_btn, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        sizer.Add(self.check_field, flag=wx.EXPAND | wx.ALL, border=10)

        # OK / Cancel are parented to the DIALOG and live outside the
        # scrolled panel, so they stay put and are always reachable —
        # buttons that scroll away with the content are buttons somebody
        # has to go looking for.
        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "&OK")
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, "&Cancel"))
        btns.Realize()

        panel.SetSizer(sizer)
        panel.SetupScrolling(scroll_x=False, scrollToTop=False)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, 1, wx.EXPAND)
        frame_sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(frame_sizer)
        self.SetMinSize((580, 640))

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._sync_sections()

    # --- helpers ---------------------------------------------------

    def _merged(self):
        """The saved settings in the current shape, defaults filling any
        gap. Goes through the migration because these settings have
        already changed shape once, and a file written under the old one
        must still say what its owner meant rather than quietly
        reverting to a default."""
        from kin_persistence import (DEFAULT_CONFIG,
                                       migrate_dictation_config)
        try:
            return migrate_dictation_config(self.config.get("dictation"))
        except Exception:
            return dict(DEFAULT_CONFIG.get("dictation") or {})

    def _initial_server_model(self):
        """The saved model name, but only when it is actually a remote
        one. A local model name left in the box after switching would
        read as a name that machine has, which it may well not."""
        if stt.route_for(self._d.get("model"),
                         self._d.get("host")) == stt.ROUTE_SERVER:
            return str(self._d.get("model") or "")
        return ""

    def _model_labels(self):
        """Local model names with their download size, and whether this
        machine already has them. Saying which are ready turns a list of
        opaque names into a choice somebody can actually make."""
        have = stt.downloaded_models()
        out = []
        for key, label in stt.MODEL_CHOICES:
            size = stt.MODEL_SIZES.get(key, "")
            if key in have:
                out.append(f"{label} — already downloaded")
            elif size:
                out.append(f"{label} — would download {size}")
            else:
                out.append(label)
        return out

    @staticmethod
    def _index_of(pairs, value, default):
        for i, (key, _lbl) in enumerate(pairs):
            if key == value:
                return i
        return default

    def _current_where(self):
        i = self.where_choice.GetSelection()
        if i < 0:
            i = 0
        return _WHERE_CHOICES[i][0]

    def _sync_sections(self):
        """Show only the section that belongs to the chosen place.

        Hidden AND disabled, not greyed out: a disabled control is still
        in the tab order, so greying it out leaves something to tab into
        that explains nothing about why it does not work."""
        where = self._current_where()
        local = (where == stt.ROUTE_LOCAL)
        server = (where == stt.ROUTE_SERVER)
        for w in (self.model_choice, self.device_choice, self.preload_check):
            w.Show(local)
            w.Enable(local)
        for w in (self.host_field, self.host_key_field, self.ask_btn,
                  self.server_model_field, self.server_model_list):
            w.Show(server)
            w.Enable(server)
        self.local_box.ShowItems(local)
        self.server_box.ShowItems(server)
        # The language setting is honoured by both Whisper routes but not
        # by Scribe, which detects on its own.
        self.lang_field.Enable(where != stt.ROUTE_ELEVENLABS)
        self._refresh_current_line()
        self.Layout()
        # A scrolled panel keeps the scroll extent it was built with, so
        # hiding half its content leaves the scrollbar claiming there is
        # more below than there is — which for anyone navigating by
        # keyboard reads as content that will not come into view.
        try:
            self._panel.Layout()
            self._panel.FitInside()
            self._panel.SetupScrolling(scroll_x=False, scrollToTop=False)
        except Exception:
            pass

    def _refresh_current_line(self):
        """Repaint the one-line summary of what will do the work.

        Read-only and in the tab order, because it is the answer to the
        question this whole screen exists to settle, and it should be
        findable without inspecting four other controls to infer it."""
        d = self._collect()
        try:
            self.current_field.SetValue(
                stt.describe(d.get("model"), d.get("host")))
        except Exception:
            pass

    def _on_where(self, _event):
        self._sync_sections()
        self.check_field.SetValue("Not checked yet.")

    def _on_any_change(self, event):
        self._refresh_current_line()
        if event is not None:
            event.Skip()

    def _on_pick_server_model(self, _event):
        sel = self.server_model_list.GetSelection()
        if 0 <= sel < len(self._server_models):
            self.server_model_field.SetValue(self._server_models[sel])
            self._refresh_current_line()

    def _collect(self):
        """Read the widgets into a dictation settings dict.

        This is where a choice of "where" becomes a (model, host) pair —
        the pair is the real setting, and stt.route_for is what reads it
        back. Keeping the translation in one place is what stops the
        screen and the engine drifting into disagreeing about where the
        audio goes."""
        d = dict(self._d)
        where = self._current_where()
        if where == stt.ROUTE_LOCAL:
            mi = self.model_choice.GetSelection()
            if 0 <= mi < len(self._model_keys):
                d["model"] = self._model_keys[mi]
            d["host"] = ""
        elif where == stt.ROUTE_SERVER:
            d["model"] = self.server_model_field.GetValue().strip()
            d["host"] = self.host_field.GetValue().strip()
        else:
            d["model"] = stt.ELEVENLABS_PREFIX + "scribe_v1"
            d["host"] = ""
        di = self.device_choice.GetSelection()
        if 0 <= di < len(_DEVICE_CHOICES):
            d["device"] = _DEVICE_CHOICES[di][0]
        d["host_key"] = self.host_key_field.GetValue().strip()
        d["language"] = self.lang_field.GetValue().strip()
        d["preload"] = bool(self.preload_check.GetValue())
        d["auto_send"] = bool(self.autosend_check.GetValue())
        return d

    # --- actions ---------------------------------------------------

    def _on_ask_server(self, _event):
        """Ask the named machine what models it has. Off the UI thread —
        an unreachable address is a timeout, and a frozen window during
        one is the wrong answer to 'is this address right'."""
        host = self.host_field.GetValue().strip()
        key = self.host_key_field.GetValue().strip()
        if not host:
            self.check_field.SetValue(
                "Type the machine's address first, then ask it what it has.")
            return
        self.ask_btn.Disable()
        self.check_field.SetValue(f"Asking {host}…")

        def worker():
            try:
                models = stt.list_server_models(host, key)
                err = None
            except Exception as e:
                models, err = [], str(e)

            def done():
                try:
                    self.ask_btn.Enable()
                except Exception:
                    pass
                if err:
                    self._server_models = []
                    self.server_model_list.Set([])
                    msg = (f"{err} You can still type the model name in by "
                           "hand — not every machine answers this question.")
                else:
                    self._server_models = list(models)
                    self.server_model_list.Set(models or [])
                    if models:
                        msg = (f"{host} offers {len(models)} model(s). Choose "
                               "one from the list, or type a name.")
                    else:
                        msg = (f"{host} answered, but listed no models. Type "
                               "the name in by hand.")
                try:
                    self.check_field.SetValue(msg)
                    self.check_field.SetFocus()
                except Exception:
                    pass
                try:
                    from audio import nvda_speak
                    nvda_speak(msg)
                except Exception:
                    pass
            wx.CallAfter(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_check(self, _event):
        """Prove the chosen transcription model can actually transcribe,
        and say so in words. Runs on a worker thread — it may download a
        model or wait on a sleeping machine, and a frozen window during
        that would be the wrong answer to 'is this working'."""
        settings = self._collect()
        self.check_btn.Disable()
        self.check_field.SetValue(
            "Checking. This may take a while the first time — it loads, and "
            "if necessary downloads, the speech model.")

        def worker():
            from frame_shared import llm_backend
            try:
                ok, msg = stt.self_check(
                    settings,
                    get_api_key=lambda: llm_backend.resolve_provider_key(
                        "elevenlabs"),
                )
            except Exception as e:
                ok, msg = False, f"Dictation check failed: {e}"

            def done():
                try:
                    self.check_btn.Enable()
                    self.check_field.SetValue(msg)
                    self.check_field.SetFocus()
                except Exception:
                    pass
                try:
                    from audio import nvda_speak
                    nvda_speak(msg)
                except Exception:
                    pass
            wx.CallAfter(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ok(self, event):
        new = self._collect()
        old = dict(self._d)
        self.config["dictation"] = new
        try:
            self._save()
        except Exception:
            pass
        # A model or device change makes whatever is loaded no longer the
        # thing that was asked for. Dropping the cache means the next
        # dictation loads the right one rather than quietly using the old
        # one for the rest of the session.
        if any(new.get(k) != old.get(k)
               for k in ("model", "device", "compute")):
            try:
                stt.reset_cache()
            except Exception:
                pass
        # The Talk button appears or disappears with dictation being
        # usable at all, so the chat tab has to be told.
        try:
            self.GetParent()._refresh_talk_button_visibility()
        except Exception:
            pass
        event.Skip()
