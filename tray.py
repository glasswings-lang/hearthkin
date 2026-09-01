# SPDX-License-Identifier: CC0-1.0

"""System-tray icon, right-click menu, and the small mini-chat
window the menu can open.

Construction order is: the main frame creates a HearthkinTaskBarIcon
in __init__ (after the icon file is loaded) and stores it on
self._tray_icon. The frame's _on_close decides whether Alt+F4 should
exit the app outright or just hide the main window into the tray —
both behaviors keep the TaskBarIcon alive; only frame.exit_from_tray()
actually destroys it.

Mini chat: a small always-on-top window with a recent-turns transcript
and an input field. Sending a message goes through the parent frame's
send_from_mini_chat() so the conversation, persistence, and memory
distillation all behave exactly the same as a main-window send. The
reply lands non-streamed (one wx.CallAfter when the model finishes)
because the streaming chunked-paint plumbing is wired into the main
chat tab and would be invasive to re-route here."""

import sys
import webbrowser
from pathlib import Path

import wx
import wx.adv


# ─── Icon loader ────────────────────────────────────────────────────────────

def load_app_icon():
    """Find Hearthkin.ico and return a wx.Icon, or None if no usable
    icon can be produced. Caller decides whether to construct the
    TaskBarIcon without one.

    Search order:
      1. Bundled next to the frozen .exe (installer / PyInstaller).
      2. Repo root (dev / source install).
      3. wx.ArtProvider stock bitmap (last resort)."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "Hearthkin.ico")
    candidates.append(Path(__file__).parent / "Hearthkin.ico")
    for p in candidates:
        try:
            if p.exists():
                icon = wx.Icon(str(p), wx.BITMAP_TYPE_ICO)
                if icon.IsOk():
                    return icon
        except Exception:
            continue
    # Fallback: a stock bitmap turned into an icon. Validate every
    # step — wx.Icon() with no arg is invalid until populated, and
    # CopyFromBitmap on a NullBitmap silently produces nothing.
    try:
        bmp = wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_OTHER, (32, 32))
        if bmp and bmp.IsOk():
            icon = wx.Icon()
            icon.CopyFromBitmap(bmp)
            if icon.IsOk():
                return icon
    except Exception:
        pass
    return None


# ─── TaskBarIcon ────────────────────────────────────────────────────────────

class HearthkinTaskBarIcon(wx.adv.TaskBarIcon):
    """The notification-area icon. Owned by the main frame; survives
    minimize-to-tray and is destroyed only on a real exit."""

    def __init__(self, frame, icon=None, tooltip="Hearthkin"):
        super().__init__()
        self.frame = frame
        # Track whether we actually got a visible icon set. The frame's
        # close handler reads this to decide hide-into-tray vs.
        # minimize-to-taskbar (you can't minimize "into nothing").
        self.icon_visible = False
        if icon is None:
            icon = load_app_icon()
        if icon is not None and icon.IsOk():
            try:
                self.icon_visible = bool(self.SetIcon(icon, tooltip))
            except Exception:
                self.icon_visible = False
        # Left-click (or single-tap) restores the main window. Bound
        # whether or not the icon is visible — TaskBarIcon may still
        # respond to events on platforms with quirks.
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self._on_left_click)
        # Double-click should also restore (Windows convention).
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_left_click)

    # --- Right-click menu --- #
    # wx asks for the popup menu via CreatePopupMenu rather than us
    # binding a right-click event, so it integrates with the OS shell's
    # native handling.
    def CreatePopupMenu(self):
        menu = wx.Menu()

        open_item = menu.Append(wx.ID_ANY, "&Open Hearthkin")
        menu.Bind(wx.EVT_MENU, lambda _e: self._show_main_frame(), open_item)

        mini_item = menu.Append(wx.ID_ANY, "&Mini chat…")
        menu.Bind(wx.EVT_MENU, lambda _e: self._open_mini_chat(), mini_item)

        menu.AppendSeparator()

        prefs_item = menu.Append(wx.ID_ANY, "&Preferences…")
        menu.Bind(wx.EVT_MENU, lambda _e: self._open_preferences(), prefs_item)

        usage_item = menu.Append(wx.ID_ANY, "&Usage stats…")
        menu.Bind(wx.EVT_MENU, lambda _e: self._open_usage(), usage_item)

        guide_item = menu.Append(wx.ID_ANY, "&User guide")
        menu.Bind(wx.EVT_MENU, lambda _e: self._open_user_guide(), guide_item)

        about_item = menu.Append(wx.ID_ANY, "&About Hearthkin")
        menu.Bind(wx.EVT_MENU, lambda _e: self._open_about(), about_item)

        menu.AppendSeparator()

        exit_item = menu.Append(wx.ID_ANY, "E&xit")
        menu.Bind(wx.EVT_MENU, lambda _e: self._exit_app(), exit_item)

        return menu

    # --- Helpers --- #

    def _on_left_click(self, _event):
        self._show_main_frame()

    def _show_main_frame(self):
        f = self.frame
        if f is None:
            return
        # Prefer the frame's robust foreground path (Win32 AttachThreadInput
        # handshake) so the window actually comes forward instead of just
        # flashing in the taskbar when Windows' foreground-lock is active.
        try:
            if hasattr(f, "bring_to_front"):
                f.bring_to_front()
                return
        except Exception:
            pass
        # Fallback: the old naive path if bring_to_front isn't available.
        try:
            if not f.IsShown():
                f.Show()
            if f.IsIconized():
                f.Iconize(False)
            f.Raise()
            f.SetFocus()
        except Exception:
            pass

    def _open_mini_chat(self):
        try:
            self.frame.open_mini_chat()
        except Exception as e:
            wx.MessageBox(
                f"Couldn't open mini chat: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )

    def _open_preferences(self):
        # Preferences is a menu-triggered dialog on the main frame.
        # Open it directly without raising the main window first —
        # the dialog is its own top-level window and floats over
        # whatever else is on screen, so the user can poke at it
        # without context-switching.
        try:
            self.frame.open_preferences_dialog()
        except Exception as e:
            wx.MessageBox(
                f"Couldn't open Preferences: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )

    def _open_usage(self):
        try:
            self.frame.open_usage_dialog()
        except Exception as e:
            wx.MessageBox(
                f"Couldn't open Usage stats: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )

    def _open_user_guide(self):
        # Bundled docs/user-guide.html opened in the user's default
        # browser. Falls back to the published GitHub copy if the
        # local file isn't found (which can happen if the build was
        # run without bundling docs/). The lookup is the shared
        # kin_persistence._find_bundled_doc (frozen-exe-adjacent →
        # source-tree docs/) rather than a third hand-rolled copy.
        p = None
        try:
            from kin_persistence import _find_bundled_doc
            p = _find_bundled_doc("user-guide.html")
        except Exception:
            p = None
        if p is not None:
            try:
                webbrowser.open(p.resolve().as_uri())
                return
            except Exception:
                pass
        webbrowser.open(
            "https://github.com/glasswings-lang/hearthkin/blob/master/docs/user-guide.html"
        )

    def _open_about(self):
        try:
            self.frame.show_about_dialog()
        except Exception:
            wx.MessageBox(
                "Hearthkin\n\n"
                "Multi-kin local-LLM chat for Windows. Accessibility-first.\n"
                "Released under CC0 1.0 Universal.\n\n"
                "https://github.com/glasswings-lang/hearthkin",
                "About Hearthkin", wx.OK | wx.ICON_INFORMATION,
            )

    def _exit_app(self):
        try:
            self.frame.exit_from_tray()
        except Exception:
            try:
                self.frame.Destroy()
            except Exception:
                pass


# ─── Mini chat window ───────────────────────────────────────────────────────

class MiniChatFrame(wx.Frame):
    """Always-on-top quick-chat window. Recent turns rendered read-only;
    sending fires through the parent frame's normal chat path so the
    conversation, persistence, and memory distillation all behave the
    same as a main-window send. Closing hides instead of destroying so
    the next open keeps state."""

    HISTORY_TURNS = 10  # last N user/assistant messages painted on populate

    def __init__(self, parent_frame):
        super().__init__(
            None,
            title="Hearthkin — quick chat",
            style=wx.DEFAULT_FRAME_STYLE | wx.FRAME_TOOL_WINDOW |
                  wx.STAY_ON_TOP,
            size=(460, 360),
        )
        self.parent_frame = parent_frame
        self._build_ui()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.kin_label = wx.StaticText(
            panel, label="Kin: (none selected)",
        )
        font = self.kin_label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.kin_label.SetFont(font)

        self.transcript = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 |
                  wx.TE_WORDWRAP,
        )
        self.transcript.SetName("Quick chat transcript")

        input_label = wx.StaticText(panel, label="Your &message:")
        self.input_field = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.input_field.SetName("Quick chat input")
        self.send_btn = wx.Button(panel, label="&Send")
        self.input_field.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)

        input_row = wx.BoxSizer(wx.HORIZONTAL)
        input_row.Add(self.input_field, proportion=1,
                      flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        input_row.Add(self.send_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        sizer.Add(self.kin_label, flag=wx.ALL, border=6)
        sizer.Add(self.transcript, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        sizer.Add(input_label,
                  flag=wx.LEFT | wx.RIGHT, border=6)
        sizer.Add(input_row, flag=wx.EXPAND | wx.ALL, border=6)
        panel.SetSizer(sizer)

    def populate(self):
        """Refresh from the parent frame's current kin and recent
        turns. Called every time the mini chat is opened from the
        tray menu so state stays in sync with the main UI."""
        f = self.parent_frame
        kin = getattr(f, "current_agent", "") or ""
        if not kin:
            self.kin_label.SetLabel("Kin: (none selected)")
            self.transcript.SetValue(
                "(No kin loaded — open Hearthkin and pick a kin first.)"
            )
            self.input_field.Disable()
            self.send_btn.Disable()
            return

        self.kin_label.SetLabel(f"Kin: {kin}")
        self.input_field.Enable()
        self.send_btn.Enable()

        convo = list(getattr(f, "conversation", []) or [])
        # Skip system / tool turns to keep the mini view conversational.
        kept = [
            m for m in convo
            if (m or {}).get("role") in ("user", "assistant")
            and (m or {}).get("content")
        ]
        kept = kept[-self.HISTORY_TURNS:]
        lines = []
        for m in kept:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            tag = "You" if role == "user" else kin
            lines.append(f"{tag}: {content}")
        if lines:
            self.transcript.SetValue("\n\n".join(lines))
            self.transcript.SetInsertionPointEnd()
            try:
                self.transcript.ShowPosition(self.transcript.GetLastPosition())
            except Exception:
                pass
        else:
            self.transcript.SetValue("(No messages yet — say hi.)")
        self.input_field.SetFocus()

    def _on_send(self, _event):
        text = self.input_field.GetValue().strip()
        if not text:
            return
        self.input_field.SetValue("")
        self.input_field.Disable()
        self.send_btn.Disable()
        # Echo locally so the user sees their message immediately,
        # before the reply round-trip lands.
        self._append_line("You", text)
        try:
            self.parent_frame.send_from_mini_chat(text, self)
        except Exception as e:
            self._append_line("(error)", str(e))
            self._enable_input()

    def append_assistant_reply(self, text):
        """Called by the parent frame from the chat worker's done
        callback once the reply has landed."""
        kin = getattr(self.parent_frame, "current_agent", "") or "kin"
        self._append_line(kin, text or "[no reply]")
        self._enable_input()

    def _append_line(self, tag, content):
        current = self.transcript.GetValue().rstrip()
        new = f"{current}\n\n{tag}: {content}".lstrip()
        self.transcript.SetValue(new)
        self.transcript.SetInsertionPointEnd()
        try:
            self.transcript.ShowPosition(self.transcript.GetLastPosition())
        except Exception:
            pass

    def _enable_input(self):
        try:
            self.input_field.Enable()
            self.send_btn.Enable()
            self.input_field.SetFocus()
        except Exception:
            pass

    def _on_close(self, event):
        # During an app shutdown — Inno installer's Restart Manager,
        # Windows logoff/reboot, the tray-menu Exit — the wxApp's
        # default OnQueryEndSession walks every top-level window and
        # calls Close() on each. If we Veto here, the WHOLE shutdown
        # is vetoed and the installer hangs. Allow real close when
        # the parent frame has flagged _quitting so the cascade
        # completes cleanly. (The parent's full exit path also
        # Destroy()s us explicitly via destroy_for_real() — both
        # routes converge on a real close.)
        if getattr(self.parent_frame, "_quitting", False):
            event.Skip()
            return
        # Normal case: user clicked the X. Hide instead of destroying
        # so the next open from the tray keeps window position, size,
        # and the in-progress transcript.
        self.Hide()
        if event.CanVeto():
            event.Veto()

    def destroy_for_real(self):
        """Called by the parent frame on actual app exit (not on
        close-to-tray)."""
        try:
            self.Destroy()
        except Exception:
            pass
