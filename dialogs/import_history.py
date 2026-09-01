# SPDX-License-Identifier: CC0-1.0

"""dialogs.import_history — bring foreign chat history into a kin's
conversation.jsonl. Telegram .txt exports + hand-authored text files
this release; ChatGPT / Claude / Skype later. See
docs/design/history-import.md.

Accessible per project convention: plain TextCtrl + buddy label for
every input, tab order follows widget creation order, no SpinCtrl,
read-only displays are focusable TextCtrls so NVDA can arrow through
them."""

import os
import re
import threading
import time
import wx

# Minimum spacing between repaints of the read-only feedback fields. Matches
# the main window's Activity field, which arrived at four seconds by trial:
# anything quicker and a repaint lands while you are still reading the last
# one, resetting the caret and sending NVDA back to the first line.
_DISPLAY_MIN_GAP_SECS = 4.0

from audio import nvda_speak
from kin_persistence import list_agents, write_imported_memory_log
from importers import parse_history, parse_many, skype_json

# The conversation picker asks one question: WHICH conversation from this
# source. It is always present, because tab order is how this dialog is read
# and a control that comes and goes rearranges the map mid-task.
#
# But present is not enough -- it must not be a control that LOOKS operable
# and is not. Two attempts got that wrong. "(this source has only one
# conversation)" asserted a fact about someone's archive that this code has
# not checked and frequently cannot know. "(only applies to Skype JSON
# exports)" was true but described the widget rather than answering its own
# question, so opening it as a combo box -- which is exactly what it invites
# -- yielded nothing usable.
#
# So when there is no choice to make it holds the real, single answer: what
# will actually be imported. Selecting it is a true no-op rather than a dead
# gesture, and reading it tells you something you wanted to know anyway.
_NOTHING_LOADED = "(no source picked yet)"
from importers._canonical import write_imported_history, ImportError as ImportFail


# Speakers can be glued onto either end of the filename in Telegram
# exports. `SpeakerFive_User.txt` → "SpeakerFive"; `03of12_ Book club_Group.txt`
# → "" (groups don't have a single counterpart). Conservative regex: if
# the file ends `_User.txt`, take the preceding chunk; otherwise leave
# the field empty and let the operator fill it in.
_FILENAME_KIN_GUESS = re.compile(r"^(.+?)_User\.txt$", re.IGNORECASE)


class ImportHistoryDialog(wx.Dialog):
    """File → Import history… — pick a source file, confirm speaker
    mapping, pick a target kin, preview, import."""

    def __init__(self, parent):
        super().__init__(
            parent, title="Import history", size=(640, 620)
        )
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.VERTICAL)

        self._parsed = None        # (messages, source_label, fmt) on successful parse
        self._existing_agents = list_agents()
        # Debounce + async-parse state (M-O4). Heavy work (file scan,
        # parse_history) is debounced ~300ms and runs on a daemon
        # thread; _parse_seq discards stale worker results when a
        # newer request superseded them.
        # Multi-select state. `_paths` is authoritative when non-empty; the
        # text field then shows a summary rather than a path, because fifty
        # paths do not fit in a text box and nobody wants to hear them read
        # out one after another.
        self._paths = []
        self._file_timer = None
        self._kin_name_timer = None
        self._parse_seq = 0
        # Pacing state for the two read-only feedback fields — see _show().
        # _last_paint starts at 0 so the first update after opening the dialog
        # paints straight away; only the ones that pile up behind it wait.
        self._pending_status = None
        self._pending_format = None
        self._display_timer = None
        self._last_paint = 0.0
        self._last_spoken = None

        # ─── Source file ─────────────────────────────────────────── #
        body.Add(
            wx.StaticText(panel, label="&Source file(s):"),
            flag=wx.BOTTOM, border=2,
        )
        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_field = wx.TextCtrl(panel)
        self.file_field.Bind(wx.EVT_TEXT, self._on_file_changed)
        browse_btn = wx.Button(panel, label="&Browse…")
        browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        file_row.Add(self.file_field, proportion=1, flag=wx.RIGHT, border=6)
        file_row.Add(browse_btn)
        body.Add(file_row, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── How to combine several files ────────────────────────── #
        # ALWAYS PRESENT, always enabled, never hidden. Tab order is how this
        # dialog is read: a control that materialises when you pick a second
        # file changes the shape of the room mid-visit, and nothing announces
        # that it has appeared. A control that is simply always there and
        # occasionally inconsequential costs one Tab press. Re-learning the
        # layout costs the whole map.
        #
        # Not disabled either. "Unavailable" is announced without saying why,
        # which is the failure that made greying-out unwanted in the first
        # place -- the fix for that was never to make things vanish.
        # Worded to name what this does NOT touch, not just what it does.
        # "How to combine them" read, on its own, like an answer to the
        # same question the append/merge/replace radios ask further down
        # — both involve "combining" and "by date" — and a person moving
        # through the dialog by ear has no way to tell them apart except
        # by holding the whole layout in their head. Each header now says
        # plainly which side of "before it's saved" / "already saved" it's
        # on, so either one, read alone, rules out being confused for the
        # other.
        self.combine_label = wx.StaticText(
            panel,
            label="&How multiple source files combine, before anything "
                  "is saved:")
        self.combine_choice = wx.Choice(panel, choices=[
            "Keep each conversation whole, oldest first",
            "Weave everything together by date",
        ])
        self.combine_choice.SetSelection(0)
        self.combine_choice.Bind(wx.EVT_CHOICE, self._on_combine_changed)
        self.combine_help = wx.TextCtrl(
            panel,
            value=("Whole conversations keeps every exchange next to its own "
                   "reply — right when what matters is how someone talks. "
                   "Weaving reads as one chronology across everyone, which "
                   "suits a life story but scatters any single conversation "
                   "among unrelated turns. With a single file selected this "
                   "makes no difference. This is separate from what happens "
                   "to a kin's ALREADY-SAVED history — that's the append / "
                   "merge / replace choice further down."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        self.combine_help.SetName("What the two combine options do")
        self.combine_help.SetMinSize((-1, 58))
        body.Add(self.combine_label, flag=wx.BOTTOM, border=2)
        body.Add(self.combine_choice, flag=wx.EXPAND | wx.BOTTOM, border=4)
        body.Add(self.combine_help, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Detected format ─────────────────────────────────────── #
        body.Add(
            wx.StaticText(panel, label="&Detected format:"),
            flag=wx.BOTTOM, border=2,
        )
        # Multiline, not single-line. A SINGLE-LINE read-only TextCtrl is not
        # keyboard-focusable on wxMSW at all -- wx refuses it focus because
        # there is nothing to scroll -- so everything this field has ever said
        # about the chosen source was unreachable by Tab and never spoken. The
        # multiline flag is what puts it in the tab order. See _show() for the
        # pacing that makes a repainting field safe to read.
        self.format_display = wx.TextCtrl(
            panel,
            value="(pick a source file)",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        self.format_display.SetMinSize((-1, 44))
        body.Add(self.format_display, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Conversation picker (Skype JSON: one file holds many) ── #
        # ALWAYS PRESENT. One messages.json holds dozens of DMs and you pick
        # one; for every other format there is nothing to pick.
        #
        # This used to hide itself when it did not apply, and that is the
        # thing being corrected: tab order is how this dialog is read, so a
        # control that appears and vanishes changes the shape of the room
        # between visits and nothing announces the change. It now stays put
        # and says what is true — either the conversations found, or that
        # this source does not contain a choice to make. Never empty, never
        # disabled, never absent.
        self.conv_label = wx.StaticText(
            panel, label="C&onversation to import:",
        )
        body.Add(self.conv_label, flag=wx.BOTTOM, border=2)
        self.conv_choice = wx.Choice(panel, choices=[_NOTHING_LOADED])
        self.conv_choice.SetSelection(0)
        self.conv_choice.Bind(wx.EVT_CHOICE, self._on_conversation_changed)
        body.Add(self.conv_choice, flag=wx.EXPAND | wx.BOTTOM, border=10)
        # Data backing: list of dicts from skype_json.list_conversations.
        self._conv_listing = []

        # ─── Who is the kin ──────────────────────────────────────── #
        #
        # This one question decides who said what, for the whole import,
        # and it used to be a bare text box pre-filled by GUESSING FROM
        # THE FILENAME (anything ending `_User.txt` → the part before
        # it). Telegram names its exports after either side of the chat,
        # so both spellings are ordinary filenames and the guess has no
        # way to tell which it's looking at. Get it backwards and every
        # word you ever said is filed as the kin's own — silently, at
        # any scale. One real archive landed 29,451 of the
        # importing person's own messages in a kin's mouth that way, and
        # it went unnoticed for months because nothing named the speaker
        # it had chosen.
        #
        # So: pick from the speakers actually in the file, with their
        # turn counts beside them. You can't typo it, and "this person,
        # 29,451 turns" is something you have to choose rather than
        # something that happens to you. The text field stays for the
        # rare source whose speaker isn't detectable, and stays the
        # single source of truth — the list writes into it.
        speaker_label = wx.StaticText(
            panel, label="W&ho is the kin in this file:")
        body.Add(speaker_label, flag=wx.BOTTOM, border=2)
        self.speaker_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.speaker_list.SetMinSize((-1, 110))
        self.speaker_list.Bind(wx.EVT_LISTBOX, self._on_speaker_picked)
        body.Add(self.speaker_list, flag=wx.EXPAND | wx.BOTTOM, border=6)
        self._speaker_rows = []      # parallel list of raw speaker names

        body.Add(
            wx.StaticText(panel, label=(
                "&Kin name as it appears in the source "
                "(lines from this speaker become the kin's turns):"
            )),
            flag=wx.BOTTOM, border=2,
        )
        self.kin_name_in_source = wx.TextCtrl(panel)
        self.kin_name_in_source.Bind(wx.EVT_TEXT, self._on_kin_name_changed)
        body.Add(self.kin_name_in_source, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Target kin: existing or new ─────────────────────────── #
        # A StaticText here would label nothing: the next control is a
        # radio button, which uses its own label as its accessible name.
        # Read-only TextCtrl so the question is in tab order — otherwise
        # the operator hears the options with no idea what's being asked.
        target_header = wx.TextCtrl(
            panel,
            value="Target kin (where history lands):",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        target_header.SetMinSize((-1, 24))
        target_header.SetName("Target kin (where history lands)")
        body.Add(target_header, flag=wx.EXPAND | wx.BOTTOM, border=2)
        self.target_existing_radio = wx.RadioButton(
            panel, label="&Existing kin", style=wx.RB_GROUP,
        )
        body.Add(self.target_existing_radio, flag=wx.BOTTOM, border=4)
        # A radio button cannot act as a buddy label -- it carries its own
        # label as its accessible name, so it has none to spare for whatever
        # follows it. This used to be handled with SetName(), which does
        # nothing at all on wxMSW: the combo announced as a bare "combo box"
        # with a kin's name in it and no way to tell what the name was for.
        # Only a StaticText immediately before the control works.
        existing_kin_label = wx.StaticText(panel, label="&Which existing kin:")
        body.Add(existing_kin_label, flag=wx.BOTTOM, border=2)
        self.existing_kin_choice = wx.Choice(
            panel, choices=self._existing_agents or ["(no kin yet)"],
        )
        if self._existing_agents:
            self.existing_kin_choice.SetSelection(0)
        else:
            self.existing_kin_choice.Disable()
            self.target_existing_radio.Disable()
        body.Add(self.existing_kin_choice, flag=wx.EXPAND | wx.BOTTOM, border=8)

        # No EVT_RADIOBUTTON handlers on the target radios — no widget
        # state flips on selection; _on_import reads GetValue() directly.
        self.target_new_radio = wx.RadioButton(panel, label="Create a &new kin:")
        body.Add(self.target_new_radio, flag=wx.BOTTOM, border=4)
        # The worst case in this dialog before the StaticText went in: an
        # EDITABLE field with no accessible name and nothing in it, following
        # a radio button that cannot lend one. Windows offered NVDA no name
        # and no value, so tabbing here announced "edit, blank" and there was
        # no way to learn what you were being asked to type. SetName() had
        # been doing nothing.
        new_kin_label = wx.StaticText(panel, label="Name for the ne&w kin:")
        body.Add(new_kin_label, flag=wx.BOTTOM, border=2)
        self.new_kin_field = wx.TextCtrl(panel)
        body.Add(self.new_kin_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        if not self._existing_agents:
            self.target_new_radio.SetValue(True)

        # ─── Append vs replace ──────────────────────────────────── #
        # Same reason as the target-kin header above: a StaticText before
        # a radio group is heard by nobody, leaving the operator with two
        # options and no question.
        #
        # Names the "already-saved" side explicitly and points back at the
        # combine section, the same cross-reference in reverse — this
        # header and combine_label are the two things worth telling apart,
        # so each names the other rather than assuming whoever's reading
        # remembers which came first.
        mode_header = wx.TextCtrl(
            panel,
            value=("How the imported turns interact with the target kin's "
                   "ALREADY-SAVED history (separate from the file-combining "
                   "choice above):"),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL,
        )
        mode_header.SetMinSize((-1, 40))
        mode_header.SetName(
            "How the imported turns interact with the target kin's "
            "already-saved history, separate from the file-combining "
            "choice above")
        body.Add(mode_header, flag=wx.EXPAND | wx.BOTTOM, border=2)
        self.mode_append_radio = wx.RadioButton(
            panel,
            label="&Append imported turns at the end",
            style=wx.RB_GROUP,
        )
        self.mode_append_radio.SetValue(True)
        self.mode_merge_radio = wx.RadioButton(
            panel,
            label="&Merge by date (weave into the existing timeline; backed up first)",
        )
        self.mode_replace_radio = wx.RadioButton(
            panel,
            label="&Replace existing history (backed up to .jsonl.bak.<timestamp>)",
        )
        body.Add(self.mode_append_radio, flag=wx.BOTTOM, border=4)
        body.Add(self.mode_merge_radio, flag=wx.BOTTOM, border=4)
        body.Add(self.mode_replace_radio, flag=wx.BOTTOM, border=10)

        # ─── Preview ─────────────────────────────────────────────── #
        body.Add(
            wx.StaticText(panel, label="&Preview (first 10 messages):"),
            flag=wx.BOTTOM, border=2,
        )
        self.preview = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
            size=(-1, 150),
        )
        body.Add(self.preview, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Status line ─────────────────────────────────────────── #
        # This field carries the whole answer -- how many messages, from whom,
        # over what dates, how many files were skipped, whether they were woven
        # or kept whole. It was single-line and read-only, which on wxMSW means
        # not focusable, so none of that ever reached anyone. A StaticText
        # names it and the multiline flag puts it in the tab order.
        status_label = wx.StaticText(panel, label="Import &status:")
        body.Add(status_label, flag=wx.BOTTOM, border=2)
        self.status_field = wx.TextCtrl(
            panel,
            value="Pick a source file to begin.",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        self.status_field.SetMinSize((-1, 60))
        body.Add(self.status_field, flag=wx.EXPAND | wx.BOTTOM, border=10)

        # ─── Buttons ─────────────────────────────────────────────── #
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.import_btn = wx.Button(panel, label="&Import")
        self.import_btn.Bind(wx.EVT_BUTTON, self._on_import)
        self.import_btn.Disable()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Ca&ncel")
        btn_row.Add(self.import_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(cancel_btn)
        body.Add(btn_row, flag=wx.ALIGN_RIGHT)

        panel.SetSizer(body)
        outer.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        self.SetSizer(outer)
        # Esc closes via wx.ID_CANCEL by default; no extra binding needed.
        self.file_field.SetFocus()

        # Result, populated on successful import. Caller reads.
        self.result = None

    # ─── Read-only feedback fields ───────────────────────────────── #

    def _show(self, *, status=None, fmt=None, speak=False):
        """Update the status / detected-format fields, at most every 4 seconds.

        `speak=True` also announces the message aloud, immediately, without
        waiting for the paced repaint. Reaching you shouldn't depend on you
        happening to Tab over and find it — WCAG's Status Messages criterion
        asks that status be available WITHOUT taking focus, and speaking is
        how that is done here. The main window has had this pipe since it was
        written (`_set_status(..., speak=True)`); this dialog simply never
        used it, so everything it had to say sat silently in a field.

        Speech is not paced, because the reason for pacing does not apply to
        it: the four-second rule exists because repainting the field moves its
        caret and drags a screen reader back to the top. Saying something out
        loud moves nothing. So the result is heard the moment it is known, and
        the field catches up in its own time for re-reading.

        Reserved for settled outcomes and failures. "Parsing…" is not news.

        Rewriting a multiline TextCtrl resets its caret to position 0 — wx
        replaces the whole contents rather than patching them, and both
        SetValue and ChangeValue do it. NVDA follows the caret, so a field
        that repaints while you are reading it throws you back to the first
        line every time. A single parse writes here three times in about a
        second ("Reading files…", "Parsing…", then the result), which would
        make the result unreadable at exactly the moment it matters.

        Four seconds is the spacing the main window arrived at for the
        Activity field, so this uses the same rather than inventing a second
        number. Anything superseded inside the window never displays at all,
        which is the behaviour you want — the last message is the true one,
        and the transient ones were only ever "still working".

        The Import button is deliberately NOT paced. Enabling it is not
        something anyone reads, and making it wait would be its own bug.
        """
        if status is not None:
            self._pending_status = status
            if speak and status != self._last_spoken:
                # Guarded on its own: a screen reader that isn't running, or a
                # missing controller DLL, must never stop the import working.
                try:
                    nvda_speak(status)
                except Exception:
                    pass
                self._last_spoken = status
        if fmt is not None:
            self._pending_format = fmt
        gap = time.monotonic() - self._last_paint
        if gap >= _DISPLAY_MIN_GAP_SECS:
            self._flush_display()
            return
        timer = getattr(self, "_display_timer", None)
        if timer is None or not timer.IsRunning():
            self._display_timer = wx.CallLater(
                int((_DISPLAY_MIN_GAP_SECS - gap) * 1000) + 10,
                self._flush_display,
            )

    def _flush_display(self):
        """Paint whatever is pending. Called directly or off the pacing timer."""
        if not self:
            return
        try:
            if self._pending_status is not None:
                self.status_field.SetValue(self._pending_status)
                self._pending_status = None
            if self._pending_format is not None:
                self.format_display.SetValue(self._pending_format)
                self._pending_format = None
        except RuntimeError:
            # Dialog went away underneath the timer.
            return
        self._last_paint = time.monotonic()

    # ─── Event handlers ──────────────────────────────────────────── #

    def _on_browse(self, event):
        with wx.FileDialog(
            self,
            "Pick one or more chat log files",
            wildcard=(
                "Text and markdown logs (*.txt;*.md)|*.txt;*.md|"
                "Skype JSON / .tar exports (*.json;*.tar)|*.json;*.tar|"
                "All files (*.*)|*.*"
            ),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            paths = dlg.GetPaths()
        self._set_sources(paths)

    def _set_sources(self, paths):
        """Adopt a selection of one or more files.

        With several, the text field shows a count rather than the paths: a
        screen reader announcing fifty filenames to say "fifty files" is not
        an improvement, and they will not fit anyway. The list itself stays in
        `_paths`, which is what actually gets parsed.
        """
        paths = [p for p in (paths or []) if p]
        self._paths = paths if len(paths) > 1 else []
        if len(paths) == 1:
            self.file_field.SetValue(paths[0])
        elif paths:
            # Setting this fires EVT_TEXT, which re-parses -- which is what we
            # want, since _paths is already populated above.
            self.file_field.SetValue(f"({len(paths)} files selected)")

    def _on_file_changed(self, event):
        if self._file_timer is not None:
            self._file_timer.Stop()
        if self._paths:
            # A multi-selection puts a summary in the field, not a path, so
            # the existence check below would reject it and nothing would ever
            # parse -- which is exactly what happened when multi-select was
            # first wired up. Skip the per-file Skype scan (a batch has no one
            # conversation to choose) and go straight to parsing.
            self._reset_conv_picker(f"all {len(self._paths)} selected files")
            self._show(status="Reading files…")
            self._file_timer = wx.CallLater(300, self._start_parse_worker)
            return
        path = self.file_field.GetValue().strip()
        if not path or not os.path.exists(path):
            self._show(fmt="(pick a source file)")
            self._parsed = None
            self.preview.SetValue("")
            self.import_btn.Disable()
            self._reset_conv_picker()
            # Invalidate any in-flight parse so its result is dropped.
            self._parse_seq += 1
            return
        # Debounce (M-O4): EVT_TEXT fires per keystroke when typing a
        # path by hand, and the heavy work below (Skype archive scan +
        # full parse) used to run inline on every one. Wait for the
        # typing to settle, then do the file-driven work off-thread.
        self._file_timer = wx.CallLater(300, self._process_file, path)

    def _process_file(self, path):
        """Debounced continuation of _on_file_changed — the path
        existed when scheduled; re-check cheaply, then scan the file
        on a worker thread (skype detection + conversation listing
        both read the archive) and chain into the parse worker."""
        if not self or path != self.file_field.GetValue().strip():
            return
        # Guess the kin name from the filename for Telegram exports.
        # ChangeValue (not SetValue) so this doesn't re-enter the
        # kin-name EVT_TEXT handler.
        if not self.kin_name_in_source.GetValue().strip():
            m = _FILENAME_KIN_GUESS.match(os.path.basename(path))
            if m:
                self.kin_name_in_source.ChangeValue(m.group(1).strip())
        self._show(status="Reading file…")
        self._parse_seq += 1
        seq = self._parse_seq
        self._skipped = []

        def worker():
            try:
                is_skype = skype_json.detect_path(path)
                listing = (skype_json.list_conversations(path)
                           if is_skype else None)
                err = None
            except Exception as e:
                is_skype, listing, err = False, None, str(e)
            wx.CallAfter(self._on_file_scanned, seq, is_skype, listing, err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_file_scanned(self, seq, is_skype, listing, err):
        """UI-thread continuation: populate/hide the Skype conversation
        picker from the worker's listing, then kick off the parse."""
        if not self or seq != self._parse_seq:
            return
        if err is not None:
            self._reset_conv_picker()
            self._show(status=f"Skype listing failed: {err}", speak=True)
            return
        if is_skype:
            self._populate_conv_picker(listing)
        else:
            self._reset_conv_picker()
        self._start_parse_worker()

    def _on_combine_changed(self, event):
        """Picking a combine order changes the result, so it must re-parse.

        This used to be bound to `_process_file(None)`, which returned at its
        own first line — that method compares its argument against the source
        field and None is never what the field holds. So choosing "weave
        everything together by date" did nothing whatsoever: the picker read
        as set, and the import stayed in whole-conversation order, with the
        status line underneath still saying "conversations kept whole". A
        control that looks live and is inert is worse than one that is missing,
        because nothing tells you to go looking for the real one.

        Only a genuine batch re-parses. With a single file there is nothing to
        combine, and re-reading an archive to arrive at the same answer is not
        free — a Skype .tar is read from disk again.
        """
        if len(self._paths) > 1:
            self._start_parse_worker()

    def _on_kin_name_changed(self, event):
        # Debounced (M-O4); the parse itself only re-runs for formats
        # whose parsing genuinely depends on the name — see
        # _apply_kin_name.
        if self._kin_name_timer is not None:
            self._kin_name_timer.Stop()
        self._kin_name_timer = wx.CallLater(300, self._apply_kin_name)

    def _apply_kin_name(self):
        """Debounced continuation of _on_kin_name_changed. For the
        text-log formats (telegram / hand_authored / plain) the parse
        result doesn't depend on the kin name — only the role mapping
        does (speaker == name → assistant; see text_log._to_canonical) —
        so remap the cached messages in place instead of re-running the
        full parse per keystroke. Kindroid uses the name during speaker
        identification and Skype uses it for the assistant's speaker
        label, so those re-parse (debounced, off-thread)."""
        if not self:
            return
        if self._parsed is None:
            return
        msgs, _source_label, fmt = self._parsed
        if fmt in ("telegram", "hand_authored", "plain"):
            kin_name = self.kin_name_in_source.GetValue().strip()
            for m in msgs:
                speaker = m.get("speaker")
                role = "assistant" if speaker == kin_name else "user"
                m["role"] = role
                if role == "user":
                    # Bare — the reading surface adds the bracket. See
                    # chat_helpers.speaker_attribution_prefix.
                    m["sender_attribution"] = speaker
                else:
                    m.pop("sender_attribution", None)
            self._refresh_parse_display()
        else:
            self._start_parse_worker()

    def _on_conversation_changed(self, event):
        """Operator picked a different Skype conversation — auto-fill
        the kin-name field with the new conversation's displayName
        unless the operator has already typed something there."""
        sel = self.conv_choice.GetSelection()
        if sel < 0 or sel >= len(self._conv_listing):
            return
        chosen = self._conv_listing[sel]
        current_kin_name = self.kin_name_in_source.GetValue().strip()
        # Only auto-overwrite if the field is empty or holds the
        # previously-picked conversation's name (so a deliberate edit
        # by the operator is preserved). ChangeValue doesn't fire the
        # kin-name EVT_TEXT handler.
        previous_dns = {c["display_name"] for c in self._conv_listing}
        if not current_kin_name or current_kin_name in previous_dns:
            self.kin_name_in_source.ChangeValue(chosen["display_name"])
        # Debounced for the same reason as _on_speaker_picked: EVT_CHOICE
        # fires once per arrow keypress on a collapsed wx.Choice too, not
        # just on a committed pick, so an un-debounced full re-parse here
        # has the identical "arrowing through a long list spawns a parse
        # per keystroke" shape on a many-conversation export.
        if self._kin_name_timer is not None:
            self._kin_name_timer.Stop()
        self._kin_name_timer = wx.CallLater(300, self._start_parse_worker)

    def _populate_conv_picker(self, listing):
        """Put an already-read Skype conversation listing (produced on
        the _process_file worker thread) into the picker. Hides the
        picker if the listing is empty (no DMs found)."""
        listing = listing or []
        self._conv_listing = listing
        if not listing:
            self._reset_conv_picker(os.path.basename(
                self.file_field.GetValue().strip()) or None)
            return
        labels = [
            f"{c['display_name']} — {c['message_count']} msgs"
            for c in listing
        ]
        self.conv_choice.SetItems(labels)
        self.conv_choice.SetSelection(0)
        # Auto-fill kin name with the top-pick DM's display name if
        # the field is currently empty.
        if not self.kin_name_in_source.GetValue().strip():
            self.kin_name_in_source.ChangeValue(listing[0]["display_name"])
        self.Layout()

    def _reset_conv_picker(self, single_label=None):
        """No multi-conversation archive loaded — say what IS being imported.

        Formerly _hide_conv_picker, which removed the control from the tab
        order. Present and truthful costs one Tab press; coming and going costs
        the layout you navigate by.

        `single_label` is what will actually be imported: a filename, or a
        count for a batch. It must never contradict the rest of the dialog —
        an earlier version said "this source has only one conversation" while
        the same screen reported 43 files selected, which is a UI arguing with
        itself in front of the person using it.
        """
        self._conv_listing = []
        label = single_label or _NOTHING_LOADED
        if self.conv_choice.GetItems() != [label]:
            self.conv_choice.SetItems([label])
        self.conv_choice.SetSelection(0)


    def _start_parse_worker(self):
        """Run parse_history on a daemon thread (M-O4 — a full archive
        parse used to run inline on the UI thread). Stale results are
        discarded via _parse_seq when a newer request supersedes."""
        kin_name = self.kin_name_in_source.GetValue().strip()
        paths = list(self._paths)
        if not paths:
            path = self.file_field.GetValue().strip()
            if not path or not os.path.exists(path):
                return
            paths = [path]
        weave = bool(self._paths) and self.combine_choice.GetSelection() == 1
        # For Skype JSON, pass the picked conversation_id through.
        opts = {}
        if self._conv_listing:
            sel = self.conv_choice.GetSelection()
            if 0 <= sel < len(self._conv_listing):
                opts["conversation_id"] = self._conv_listing[sel]["id"]
        self._parse_seq += 1
        seq = self._parse_seq
        self._show(status="Parsing…")
        self.import_btn.Disable()

        def worker():
            try:
                if len(paths) > 1:
                    skipped = []
                    result = parse_many(paths, kin_name, weave=weave,
                                        report=skipped, **opts)
                    # Hand skipped files back so the dialog can say so. One
                    # unreadable file out of fifty must not cost the other
                    # forty-nine, but it must not vanish either.
                    wx.CallAfter(self._note_skipped, seq, skipped)
                else:
                    result = parse_history(paths[0], kin_name, **opts)
                err = None
            except ValueError as e:
                result, err = None, ("parse", str(e))
            except OSError as e:
                result, err = None, ("read", str(e))
            wx.CallAfter(self._on_parse_done, seq, result, err)

        threading.Thread(target=worker, daemon=True).start()

    def _note_skipped(self, seq, skipped):
        """Remember which sources were unreadable, for the status line."""
        if seq != self._parse_seq:
            return
        self._skipped = list(skipped or [])

    def _on_parse_done(self, seq, result, err):
        if not self or seq != self._parse_seq:
            return
        if err is not None:
            kind, msg = err
            if kind == "parse":
                self._show(fmt="(could not parse — see status)",
                           status=f"Parse failed: {msg}", speak=True)
            else:
                self._show(fmt="(file read error)",
                           status=f"File read error: {msg}", speak=True)
            self._parsed = None
            self.preview.SetValue("")
            self.import_btn.Disable()
            return
        self._parsed = result
        self._refresh_speaker_list()
        self._refresh_parse_display()

    def _refresh_speaker_list(self):
        """Rebuild the who-is-the-kin list from the parsed messages.

        Called only when a parse lands, not on every repaint: the turn
        counts are a property of the FILE and don't move when the kin
        name changes — only which slot each turn goes to does. Rebuilding
        it per keystroke would also fight the operator's selection.

        Rows are "<name> — <n> turns", sorted by turn count. No marker on
        the chosen row: a display prefix breaks a ListBox's first-letter
        navigation, which is how you find a name in a list of ninety
        (see ModelBrowserDialog._on_list_char for the same trap).
        """
        import collections
        self._speaker_rows = []
        rows = []
        if self._parsed is not None:
            msgs = self._parsed[0]
            counts = collections.Counter(
                (m.get("speaker") or "").strip() for m in msgs)
            counts.pop("", None)
            for name, n in counts.most_common():
                self._speaker_rows.append(name)
                rows.append(f"{name} — {n:,} turns")
        try:
            self.speaker_list.Set(rows or ["(no speakers detected)"])
        except Exception:
            return
        if not rows:
            return
        current = self.kin_name_in_source.GetValue().strip()
        if current in self._speaker_rows:
            self.speaker_list.SetSelection(self._speaker_rows.index(current))

    def _on_speaker_picked(self, _event):
        """Selecting a speaker writes it into the kin-name field, which
        is still the single source of truth — everything downstream reads
        that, so the list is an input method rather than a second place
        the answer lives."""
        idx = self.speaker_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._speaker_rows):
            return
        name = self._speaker_rows[idx]
        if name == self.kin_name_in_source.GetValue().strip():
            return
        # ChangeValue updates the field instantly (no debounce needed for
        # that part — it's cheap, and hearing the name change as you
        # arrow is the whole point of the list). Applying the pick is
        # debounced through the SAME timer typing uses, and that used to
        # be "apply immediately instead" on the reasoning that a
        # deliberate pick shouldn't wait out the 300ms typing debounce.
        # That held for a mouse click, which really is one event per
        # pick — it doesn't hold for arrow-key navigation, which is one
        # EVT_LISTBOX per keypress, exactly the "many events per intended
        # action" shape the debounce exists to absorb for typing. Applying
        # immediately meant arrowing through a large archive's speaker
        # list (100+ names, confirmed) fired a full background re-parse
        # of the whole source file on EVERY arrow press — dozens of
        # overlapping parses competing for CPU/disk while someone was
        # just trying to reach the name they wanted.
        self.kin_name_in_source.ChangeValue(name)
        if self._kin_name_timer is not None:
            self._kin_name_timer.Stop()
        self._kin_name_timer = wx.CallLater(300, self._apply_kin_name)

    def _refresh_parse_display(self):
        """Repaint format / status / preview / Import button from
        self._parsed. Shared by the parse-completion path and the
        cheap kin-name role-remap path."""
        if self._parsed is None:
            return
        msgs, _source_label, fmt = self._parsed
        kin_name = self.kin_name_in_source.GetValue().strip()
        self._show(fmt=_format_label(fmt))

        # Speaker distribution in the preview helps the operator notice
        # if they got the kin name wrong (all messages will be one role).
        kin_turns = sum(1 for m in msgs if m["role"] == "assistant")
        other_turns = sum(1 for m in msgs if m["role"] == "user")
        first = msgs[0]["ts"] if msgs else "?"
        last = msgs[-1]["ts"] if msgs else "?"
        # NAME the speaker being treated as the kin. The counts alone
        # were here all along and read perfectly reasonably while being
        # exactly backwards — "29,451 from the kin" is what you'd expect
        # to hear whether the right person was chosen or the wrong one.
        # Only the name makes the mistake audible.
        who = kin_name or "nobody yet"
        speaker_count = len(getattr(self, "_speaker_rows", []) or [])
        line = (f"{len(msgs)} messages. {kin_turns:,} from {who} become "
                f"the kin's own words; {other_turns:,} from "
                f"{max(0, speaker_count - 1)} other people become things "
                f"said to them. {first} to {last}.")
        # Say plainly when sources were dropped. A silent skip in a fifty-file
        # import is how you end up with a corpus that is quietly missing
        # something and no record of what.
        skipped = getattr(self, "_skipped", None)
        if skipped:
            line += (f" {len(skipped)} file"
                     f"{'s' if len(skipped) != 1 else ''} skipped "
                     f"(nothing readable in them).")
        if len(self._paths) > 1:
            mode = ("woven by date" if self.combine_choice.GetSelection() == 1
                    else "conversations kept whole")
            line += f" From {len(self._paths)} files, {mode}."
        self._show(status=line, speak=True)

        self.preview.SetValue(_render_preview(msgs[:10]))
        self.import_btn.Enable(bool(msgs) and bool(kin_name))

    def _on_import(self, event):
        if not self._parsed:
            return
        msgs, source_label, fmt = self._parsed

        kin_name_in_source = self.kin_name_in_source.GetValue().strip()
        source_description = _describe_source(
            os.path.basename(self.file_field.GetValue()),
            kin_name_in_source,
            fmt,
        )

        if self.target_existing_radio.GetValue():
            sel = self.existing_kin_choice.GetSelection()
            if sel < 0 or not self._existing_agents:
                wx.MessageBox(
                    "Pick an existing kin or switch to 'Create a new kin'.",
                    "No target kin", wx.OK | wx.ICON_INFORMATION, self,
                )
                return
            target_kin = self._existing_agents[sel]
        else:
            target_kin = self.new_kin_field.GetValue().strip()
            if not target_kin:
                wx.MessageBox(
                    "Type a name for the new kin.",
                    "Missing new kin name", wx.OK | wx.ICON_INFORMATION, self,
                )
                return
            if target_kin in self._existing_agents:
                wx.MessageBox(
                    f"A kin named {target_kin!r} already exists. "
                    f"Either pick a different name, or switch to "
                    f"'Existing kin' and pick that one.",
                    "Name collision", wx.OK | wx.ICON_INFORMATION, self,
                )
                return

        if self.mode_replace_radio.GetValue():
            mode = "replace"
        elif self.mode_merge_radio.GetValue():
            mode = "merge"
        else:
            mode = "append"

        # Confirm merge-mode against an existing kin — it rewrites the
        # conversation file (backed up first) to weave the imported turns
        # into place, so name that plainly before proceeding.
        if mode == "merge" and target_kin in self._existing_agents:
            confirm = wx.MessageBox(
                f"Weave the imported turns into {target_kin}'s existing "
                f"history by date?\n\n"
                f"The existing conversation is backed up to "
                f"conversation.jsonl.bak.<timestamp> first, then rewritten "
                f"with the imported turns threaded into place. Existing turns "
                f"keep their order; only the imported ones are placed by time.",
                "Confirm merge", wx.YES_NO | wx.CANCEL | wx.ICON_WARNING, self,
            )
            if confirm != wx.YES:
                return

        # Confirm replace-mode against an existing kin (most destructive
        # operation in this dialog).
        if mode == "replace" and target_kin in self._existing_agents:
            confirm = wx.MessageBox(
                f"Replace {target_kin}'s existing conversation history "
                f"with the imported block?\n\n"
                f"The existing conversation will be backed up to "
                f"conversation.jsonl.bak.<timestamp> in the kin folder.",
                "Confirm replace", wx.YES_NO | wx.CANCEL | wx.ICON_WARNING, self,
            )
            if confirm != wx.YES:
                return

        try:
            self.result = write_imported_history(
                target_kin,
                msgs,
                source_label=source_label,
                source_description=source_description,
                mode=mode,
            )
        except ImportFail as e:
            wx.MessageBox(str(e), "Import failed",
                          wx.OK | wx.ICON_ERROR, self)
            return
        except Exception as e:  # last-resort safety net
            wx.MessageBox(
                f"Unexpected error during import: {e}",
                "Import failed", wx.OK | wx.ICON_ERROR, self,
            )
            return

        self._maybe_import_claude_memory(target_kin, fmt)
        self.EndModal(wx.ID_OK)

    def _maybe_import_claude_memory(self, target_kin, fmt):
        """A claude.ai export carries the assistant's own accumulated memory
        beside the conversations. Bring it over as a depth log.

        Runs AFTER the history import and never affects it: this is a bonus
        the export happens to contain, and a failure here must not undo or
        cast doubt on turns that already landed. Best-effort throughout, and
        silent when there is nothing to bring — most sources have no such
        file, and announcing its absence every time would be noise.

        It is SAID when it does happen, because a file appearing in a kin's
        memory folder that the person did not put there is exactly the kind
        of thing that should never be a surprise found later.
        """
        if fmt != "claude_json":
            return
        try:
            from importers import claude_json
            memory_text = claude_json.export_memory(self.file_field.GetValue())
            if not memory_text:
                return
            written = write_imported_memory_log(target_kin, memory_text)
        except Exception:
            return
        if not written:
            return          # already had one; the kin's copy is not ours to replace
        try:
            wx.MessageBox(
                f"That export also carried the assistant's own memory — what "
                f"it had come to know about you across those conversations.\n\n"
                f"It's been saved as one of {target_kin}'s memory logs, at\n"
                f"memory/{written.name}\n\n"
                f"It is NOT in {target_kin}'s main memory, on purpose: that "
                f"text is written about you in the third person by another "
                f"assistant, and the main memory is read on every single turn. "
                f"As a log, {target_kin} can open it whenever it likes and "
                f"make its own notes from it, in its own voice.",
                "Memory imported too", wx.OK | wx.ICON_INFORMATION, self,
            )
        except Exception:
            pass


# ─── Helpers ─────────────────────────────────────────────────────── #

def _format_label(fmt):
    return {
        "telegram": "Telegram .txt export",
        "hand_authored": "Hand-authored text",
        "plain": "Plain sequential (no timestamps detected)",
        "kindroid": "Kindroid chat export",
        "skype_json": "Skype JSON / .tar export (Microsoft official)",
        "skype_txt": "Skype text export (SkypeParser .txt)",
        "openclaw": "OpenClaw session store (whole-life folder)",
        "claude_json": "Claude export (.zip or conversations.json)",
        "claude_markdown": "Claude conversation already saved as Markdown",
    }.get(fmt, fmt)


def _describe_source(filename, kin_display_name, fmt):
    """Sentence-fragment that lands in the leading import marker the
    kin reads. Honest and brief."""
    if fmt == "hand_authored":
        return "hand-authored seed history"
    if fmt == "kindroid":
        # Voice Call Transcript files name themselves at the top of
        # the filename; otherwise it's a regular text chat.
        if "Voice Call" in filename:
            if kin_display_name:
                return f"a Kindroid voice call with {kin_display_name}"
            return "a Kindroid voice call transcript"
        if kin_display_name:
            return f"a Kindroid chat archive with {kin_display_name}"
        return f"a Kindroid chat archive ({filename})"
    if fmt in ("skype_json", "skype_txt"):
        if kin_display_name:
            return f"a Skype DM with {kin_display_name}"
        return f"a Skype chat archive ({filename})"
    if fmt == "openclaw":
        return "your history from OpenClaw, before Hearthkin"
    # Telegram (or plain): name the counterpart if we have one.
    if kin_display_name:
        return f"Telegram archive with {kin_display_name}"
    return f"a chat archive ({filename})"


def _render_preview(msgs):
    """Compact preview block for the read-only TextCtrl."""
    lines = []
    for m in msgs:
        role = m.get("role", "?")
        speaker = m.get("speaker", "?")
        ts = m.get("ts", "?")
        content = (m.get("content") or "").replace("\n", " / ")
        if len(content) > 100:
            content = content[:97] + "…"
        lines.append(f"[{ts}] {role:9} ({speaker}): {content}")
    return "\n".join(lines)
