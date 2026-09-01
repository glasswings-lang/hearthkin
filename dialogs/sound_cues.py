"""Per-cue sound settings — what Hearthkin makes a noise about, and how loudly.

Sound is doing real work here, not decoration. For someone who can't see the
window, a model call is otherwise completely silent: a prefill can run four
minutes with no output at all, and nothing distinguishes "thinking hard" from
"died". These cues are the only channel that reports it.

They are *sounds* rather than spoken announcements for a specific reason. A
screen reader running character echo — every keystroke spoken — has no free
moment to say anything else; a notification either interrupts the typing or
queues behind it and is never heard. And a Windows toast lands in the same
stack as Telegram, Signal and everything else, where it is immediately buried.
Audio is the one channel nothing else is competing for.

Five cues, rising in pitch with progress so the sequence is legible by ear
without anyone learning a code:

    sent     440 Hz   the request went out; nothing to show yet
    first    660 Hz   the first token came back; it really is answering
    working  330 Hz   still going (repeats; sits below the others deliberately)
    done     880 Hz   finished
    chunk    440->880 one chunk of a redistill done; pitch tracks how far
                      through it is, so an hour-long job is legible by ear

Each has its own switch and volume because they carry different information
and are wanted in different amounts. "Finished" wants to be audible from
another room. "Still working" repeats, so it wants to be quiet and occasional
or it becomes a dripping tap.

Every tone can be replaced by dropping a WAV into `~/.hearthkin/sounds/` named
after the cue — `done.wav`, `working.wav` and so on. Anything you might want
to change lives in a file you can change.
"""

import wx

from ._shared import _IntField

# (key, label, what it means, default volume as a percentage)
CUES = [
    ("send", "Request &sent",
     "The moment your message goes to the model. Nothing has come back yet — "
     "this is confirmation it left.", 80),
    ("first", "&First word back",
     "The model has started answering. On a long wait this is the one that "
     "tells you the silence is over.", 80),
    ("working", "Still &working (repeats)",
     "Plays every so often while a call is in flight. Reading a long prompt "
     "can take minutes with nothing to show, and without this there is no way "
     "to tell it apart from a crash. Quiet and occasional by design.", 40),
    ("done", "&Reply finished",
     "The reply is complete. This fires for every kin on every surface — "
     "desktop, Telegram, a scheduled wake-up — not just whoever you are "
     "looking at.", 90),
    ("chunk", "Redistill &progress",
     "One beep each time a chunk of a 'redistill from start' finishes, "
     "rising in pitch as it gets closer to done — low at the beginning, "
     "high at the end. A redistill can run for an hour, and its written "
     "progress report is spoken, which means it is cut off by your own "
     "typing every time. This is the version that arrives: the same beep "
     "twice means stuck, a climbing one means it's working through. It "
     "replaces the 'still working' repeat for the length of a redistill "
     "rather than adding to it. A chunk.wav of your own plays flat — a "
     "file can't be re-pitched.", 55),
]


class SoundCuesDialog(wx.Dialog):
    def __init__(self, parent, config, save_config):
        super().__init__(parent, title="Sound cues",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.config = config
        self._save = save_config

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        def help(text):
            t = wx.StaticText(panel, label=text)
            t.Wrap(520)
            t.SetForegroundColour(wx.Colour(110, 110, 110))
            sizer.Add(t, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        stages = self.config.get("chime_stages")
        if not isinstance(stages, dict):
            stages = {}
        # Falling back to the old single switch keeps an existing install
        # sounding exactly as it did until someone changes something here.
        master = bool(self.config.get("reply_chime", False))
        try:
            base_vol = int(round(float(self.config.get("chime_volume", 0.8) or 0) * 100))
        except (TypeError, ValueError):
            base_vol = 80

        self.rows = {}
        for key, label, blurb, default_vol in CUES:
            entry = stages.get(key) if isinstance(stages.get(key), dict) else {}
            on = bool(entry.get("on", master))
            try:
                vol = int(round(float(entry.get("volume", base_vol / 100.0)) * 100))
            except (TypeError, ValueError):
                vol = default_vol
            vol = max(0, min(100, vol))

            check = wx.CheckBox(panel, label=label)
            check.SetValue(on)
            sizer.Add(check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)

            row = wx.BoxSizer(wx.HORIZONTAL)
            # Distinct per cue, for the same reason the Test button below is:
            # four fields all announcing "Volume:" are indistinguishable when
            # tabbing through, and there is nothing to tell you which cue you
            # are setting. The disambiguation used to live in the field's
            # name= instead, which does nothing at all on wxMSW — the label
            # immediately before a control is the only thing Windows reads.
            plain_label = label.replace("&", "")
            row.Add(wx.StaticText(panel, label=f"{plain_label} volume (0-100):"),
                    flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
            field = _IntField(
                panel, value=vol, min_val=0, max_val=100, size=(70, -1),
                on_commit=lambda v, k=key: self._commit(k))
            row.Add(field, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=10)
            # A distinct label per button: four buttons all called "Test" are
            # indistinguishable when tabbing through.
            test = wx.Button(panel, label=f"Test {label.replace('&', '').lower()}")
            test.Bind(wx.EVT_BUTTON, lambda _e, k=key: self._test(k))
            row.Add(test, flag=wx.ALIGN_CENTER_VERTICAL)
            sizer.Add(row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=24)

            check.Bind(wx.EVT_CHECKBOX, lambda _e, k=key: self._commit(k))
            self.rows[key] = (check, field)
            help(blurb)

        int_row = wx.BoxSizer(wx.HORIZONTAL)
        # "0 = never" belongs in the visible label. It used to live only in the
        # field's name=, which wxMSW never reads out, so the one thing you had
        # to know to switch the repeat off never reached anyone.
        int_row.Add(wx.StaticText(
            panel,
            label="Repeat the working sound e&very (seconds, 0 = never):"),
            flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        try:
            every = int(self.config.get("chime_working_secs", 30) or 30)
        except (TypeError, ValueError):
            every = 30
        self.every_field = _IntField(
            panel, value=every, min_val=0, max_val=600, size=(70, -1),
            on_commit=self._commit_every)
        int_row.Add(self.every_field)
        sizer.Add(int_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        help("0 turns the repeat off without silencing the other cues. "
             "Long by default — these waits are measured in minutes, and a "
             "tick every few seconds would be maddening rather than useful.")

        btns = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btns.AddStretchSpacer()
        btns.Add(close_btn)
        sizer.Add(btns, flag=wx.EXPAND | wx.ALL, border=12)

        self.SetEscapeId(wx.ID_CLOSE)
        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        # Grown for the fifth cue. The dialog is resizable and scroll-free,
        # so a size that clips the last row hides a control rather than
        # merely looking cramped.
        self.SetInitialSize((600, 820))
        self.Layout()

    def _current(self):
        out = {}
        for key, (check, field) in self.rows.items():
            try:
                vol = max(0, min(100, int(field.GetValue()))) / 100.0
            except Exception:
                vol = 0.8
            out[key] = {"on": bool(check.GetValue()), "volume": vol}
        return out

    def _commit(self, key):
        self.config["chime_stages"] = self._current()
        try:
            self._save()
        except Exception:
            pass
        self._test(key)

    def _commit_every(self, value):
        self.config["chime_working_secs"] = int(value)
        try:
            self._save()
        except Exception:
            pass

    def _test(self, key):
        """Play the cue as it is currently configured. Changing a sound
        setting without hearing the result would be guessing — especially
        volume, where the only meaningful question is whether you can hear
        it from where you actually sit."""
        try:
            from audio import play_chime
            from frame.status_voice_mixin import StatusVoiceMixin
            check, field = self.rows[key]
            if not check.GetValue():
                return
            vol = max(0, min(100, int(field.GetValue()))) / 100.0
            if vol <= 0:
                return
            if key == "chunk":
                # Demonstrate the gradient, not one arbitrary point on
                # it. The rise IS the information this cue carries, and a
                # single beep would tell you nothing about what you're
                # agreeing to hear sixty times.
                lo = StatusVoiceMixin._CHUNK_TONE_LOW
                hi = StatusVoiceMixin._CHUNK_TONE_HIGH
                for i in range(3):
                    freq = int(lo + (hi - lo) * (i / 2.0))
                    wx.CallLater(i * 260, play_chime,
                                 freq, 55, vol, "chunk")
                return
            freq, dur = StatusVoiceMixin._CHIME_TONES.get(key, (880, 140))
            play_chime(freq, dur, volume=vol, name=key)
        except Exception:
            pass

    def _on_close(self, _event):
        self.config["chime_stages"] = self._current()
        try:
            self._save()
        except Exception:
            pass
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
