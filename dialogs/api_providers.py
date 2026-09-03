# SPDX-License-Identifier: CC0-1.0

"""dialogs.api_providers — manage the API-provider registry.

The registry is a hand-editable Markdown file (~/.hearthkin/providers.md) of
`name = https://host/v1` lines (kin_persistence.API_PROVIDERS_FILE). A kin
points at a provider by the prefix on its model name — `featherless/x/y` — so
this list is what decides which model names mean "send this over the
internet" and which mean "ask the local Ollama".

Same shape and same reasoning as dialogs.ollama_machines: this is the
accessible UI for that file, so a non-coder never has to edit Markdown by
hand. List + Add / Edit / Remove, and a small entry sub-dialog with a Test
button that actually calls the provider.

Every hosted provider worth adding speaks the same OpenAI chat-completions
shape, which is why this can be a text file at all: a provider is a name, a
base URL and a key, not a code path.

Keys are NOT stored in providers.md — a registry file is exactly the sort of
thing someone pastes into a chat when asking for help. They go through
llm_backend.write_provider_key() into ~/.ai_programs/<name>_key.json.
"""

import re
import threading

import wx

from kin_persistence import load_api_providers, save_api_providers

# Mirrors kin_persistence.API_PROVIDER_NAME_RE. Duplicated deliberately: this
# dialog has to explain a rejection in a sentence, before the loader would
# silently drop the line. Importing the pattern wouldn't give us the sentence.
_NAME_OK = re.compile(r"^[a-z0-9_-]+$")


def _normalise_name(raw):
    """Turn what someone typed into a usable provider name, or "" if it can't
    be salvaged.

    Spaces and dots become hyphens, because "Featherless AI" is what a person
    types and `featherless-ai` is what actually has to work — as a model
    prefix, as a filename, and as an environment variable name.
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"[\s.]+", "-", s)
    s = re.sub(r"[^a-z0-9_-]", "", s)
    s = s.strip("-")
    return s if s and _NAME_OK.match(s) else ""


class _ProviderEntryDialog(wx.Dialog):
    """Add or edit one provider: a name, a base URL, and optionally its key."""

    def __init__(self, parent, name="", url="", editing=False):
        super().__init__(parent, title="API provider",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._editing = editing
        outer = wx.BoxSizer(wx.VERTICAL)

        name_lbl = wx.StaticText(self, label="&Name:")
        self.name_field = wx.TextCtrl(self, value=name)
        self.name_field.Bind(wx.EVT_TEXT, self._on_name_typed)
        outer.Add(name_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.name_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        # Read-only TextCtrl rather than StaticText: the control after a
        # StaticText takes its accessible name from it, so a StaticText used
        # as a hint reaches nobody. Same pattern as the machines dialog.
        self.name_hint = wx.TextCtrl(
            self, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        self.name_hint.SetName("Name hint")
        self.name_hint.SetMinSize((-1, 64))
        outer.Add(self.name_hint,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        url_lbl = wx.StaticText(self, label="&Address (the provider's API URL):")
        self.url_field = wx.TextCtrl(self, value=url or "https://")
        outer.Add(url_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.url_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        url_hint = wx.TextCtrl(
            self,
            value="The base address from the provider's own documentation, "
                  "usually ending in /v1 — for example "
                  "https://api.featherless.ai/v1. Don't put a model name or "
                  "/chat/completions on the end; Hearthkin adds that part.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        url_hint.SetName("Address hint")
        url_hint.SetMinSize((-1, 64))
        outer.Add(url_hint,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        key_lbl = wx.StaticText(
            self, label="&Key (leave blank to keep the one already saved):")
        self.key_field = wx.TextCtrl(self, value="")
        outer.Add(key_lbl, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.key_field, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        key_hint = wx.TextCtrl(
            self,
            value="Not hidden as you type, so you can check what you pasted. "
                  "It is never written into providers.md — it goes to its own "
                  "file in your home folder, which keeps the provider list "
                  "safe to paste when you're asking someone for help.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        key_hint.SetName("Key hint")
        key_hint.SetMinSize((-1, 64))
        outer.Add(key_hint,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        test_row = wx.BoxSizer(wx.HORIZONTAL)
        self.test_btn = wx.Button(self, label="&Test connection")
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        test_row.Add(self.test_btn,
                     flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        self.test_result = wx.TextCtrl(self, value="", style=wx.TE_READONLY)
        self.test_result.SetName("Test result")
        test_row.Add(self.test_result, proportion=1,
                     flag=wx.ALIGN_CENTER_VERTICAL)
        outer.Add(test_row,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

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
        self._on_name_typed(None)
        (self.name_field if not name else self.url_field).SetFocus()

    def _on_name_typed(self, _event):
        """Say, live, what the typed name is actually going to become.

        Someone types "Featherless AI"; the thing that has to work is
        `featherless-ai`, and it turns up later as a prefix on every model
        name and as the name of a key file. Far better to meet that while
        typing it than to find it later in a model list.
        """
        cleaned = _normalise_name(self.name_field.GetValue())
        if not cleaned:
            self.name_hint.SetValue(
                "Your short name for this provider — for example "
                "Featherless. Letters and numbers; spaces become hyphens.")
        else:
            self.name_hint.SetValue(
                "Saved as \"%s\". Its models will be named \"%s/...\", and "
                "its key is read from the %s_API_KEY environment variable, "
                "or from %s_key.json in your home folder."
                % (cleaned, cleaned, cleaned.upper().replace("-", "_"),
                   cleaned))

    def _on_test(self, _event):
        name = _normalise_name(self.name_field.GetValue())
        url = self.url_field.GetValue().strip().rstrip("/")
        key = self.key_field.GetValue().strip()
        if not url or url in ("https:/", "https://", "http:/", "http://"):
            self.test_result.SetValue("Enter an address first.")
            return
        if not name:
            self.test_result.SetValue("Enter a name first.")
            return
        self.test_result.SetValue("Testing...")
        self.test_btn.Disable()

        def worker():
            msg = self._probe(url, key, name)
            wx.CallAfter(self._on_test_done, msg)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _probe(url, key, name):
        """Ask the provider for its model list.

        Deliberately not a chat call: a chat costs money or quota on some
        providers, and nobody should be billed for finding out they typed the
        address wrong.
        """
        import json
        import urllib.error
        import urllib.request
        if not key:
            # Editing an existing provider without retyping the key should
            # still be testable — fall back to whatever is already saved.
            try:
                import llm_backend
                key = llm_backend.resolve_provider_key(name)
            except Exception:
                key = ""
        req = urllib.request.Request(url + "/models")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return ("Reached it, but the key was refused (%d). Check the "
                        "key." % e.code)
            if e.code == 404:
                return ("Reached the server, but there's nothing at that "
                        "address. Check it ends in /v1.")
            return "Could not reach it: HTTP %d" % e.code
        except Exception as e:
            return "Could not reach it: %s" % e
        try:
            data = json.loads(raw)
        except Exception:
            return "Answered, but not with a model list. Check the address."
        models = data.get("data") if isinstance(data, dict) else None
        if models is None and isinstance(data, list):
            models = data
        if models is None:
            return "Answered, but not with a model list. Check the address."
        n = len(models)
        return "OK — reachable, %d model%s available." % (
            n, "" if n == 1 else "s")

    def _on_test_done(self, msg):
        if not self:
            return
        self.test_result.SetValue(msg)
        self.test_btn.Enable()

    def get_values(self):
        """(name, url, key). Name is normalised; key may be blank."""
        return (_normalise_name(self.name_field.GetValue()),
                self.url_field.GetValue().strip().rstrip("/"),
                self.key_field.GetValue().strip())


class ApiProvidersDialog(wx.Dialog):
    """Manage the API-provider registry: Add / Edit / Remove, written back to
    providers.md on Save."""

    def __init__(self, parent):
        super().__init__(parent, title="API providers",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._entries = [list(pair) for pair in load_api_providers()]
        # Keys typed this session, held until Save so Cancel really cancels.
        self._pending_keys = {}

        outer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.TextCtrl(
            self,
            value="Services Hearthkin can reach over the internet. Add one "
                  "here, then pick its models in the model browser — they "
                  "appear with the provider's name in front. OpenRouter is "
                  "built in and isn't listed. Your own machines aren't "
                  "providers; those live under Ollama machines.",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL
            | wx.TE_WORDWRAP,
        )
        intro.SetName("About API providers")
        intro.SetMinSize((-1, 80))
        outer.Add(intro, flag=wx.EXPAND | wx.ALL, border=8)

        from ._shared import rebuild_listbox
        self._rebuild_listbox = rebuild_listbox
        # A StaticText immediately before the list is the only thing wxMSW
        # takes an accessible name from — SetName() on the list itself does
        # nothing, and the list announces as a bare "list box".
        providers_label = wx.StaticText(self, label="&Providers:")
        self.providers_list = wx.ListBox(self, style=wx.LB_SINGLE)
        self.providers_list.SetMinSize((460, 160))
        self.providers_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_edit)
        outer.Add(providers_label, flag=wx.LEFT | wx.RIGHT, border=8)
        outer.Add(self.providers_list, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(self, label="&Add…")
        add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        edit_btn = wx.Button(self, label="&Edit…")
        edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        remove_btn = wx.Button(self, label="&Remove")
        remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        btn_row.Add(add_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(edit_btn, flag=wx.RIGHT, border=6)
        btn_row.Add(remove_btn)
        outer.Add(btn_row, flag=wx.ALL, border=8)

        close_row = wx.BoxSizer(wx.HORIZONTAL)
        close_row.AddStretchSpacer(1)
        save_btn = wx.Button(self, wx.ID_OK, label="&Save && close")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Ca&ncel")
        save_btn.SetDefault()
        close_row.Add(save_btn, flag=wx.RIGHT, border=6)
        close_row.Add(cancel_btn)
        outer.Add(close_row, flag=wx.EXPAND | wx.ALL, border=8)

        self.SetSizer(outer)
        self.Fit()
        self._refresh()

    def _refresh(self, saved_index=None):
        if saved_index is None:
            saved_index = self.providers_list.GetSelection()
        labels = ["%s = %s" % (name, url) for name, url in self._entries]
        self._rebuild_listbox(self.providers_list, labels,
                              saved_index=saved_index)

    def _on_add(self, _event):
        dlg = _ProviderEntryDialog(self)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                name, url, key = dlg.get_values()
                if not name or not url:
                    wx.MessageBox(
                        "A provider needs both a name and an address, and "
                        "the name has to survive being turned into a model "
                        "prefix. Nothing was added.",
                        "Not added", wx.OK | wx.ICON_INFORMATION, self)
                    return
                self._entries = [e for e in self._entries if e[0] != name]
                self._entries.append([name, url])
                if key:
                    self._pending_keys[name] = key
                self._refresh(saved_index=len(self._entries) - 1)
        finally:
            dlg.Destroy()

    def _on_edit(self, _event):
        idx = self.providers_list.GetSelection()
        if idx < 0 or idx >= len(self._entries):
            return
        old_name, old_url = self._entries[idx]
        dlg = _ProviderEntryDialog(self, name=old_name, url=old_url,
                                   editing=True)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                name, url, key = dlg.get_values()
                if not name or not url:
                    return
                self._entries[idx] = [name, url]
                if key:
                    self._pending_keys[name] = key
                self._refresh(saved_index=idx)
        finally:
            dlg.Destroy()

    def _on_remove(self, _event):
        idx = self.providers_list.GetSelection()
        if idx < 0 or idx >= len(self._entries):
            return
        name = self._entries[idx][0]
        # Unlike removing an Ollama machine, this DOES break kin that use it.
        # The provider name is part of the model name, so "featherless/x"
        # stops being recognised as remote and gets asked of the local
        # Ollama, which has never heard of it. Say that plainly rather than
        # letting it be discovered as a mysterious failure at send time.
        if wx.MessageBox(
                "Remove \"%s\"?\n\n"
                "Any kin whose model starts with \"%s/\" will stop working "
                "until you give it a different model — Hearthkin will look "
                "for that model on your own machine and not find it.\n\n"
                "The saved key is left alone." % (name, name),
                "Remove provider",
                wx.YES_NO | wx.CANCEL | wx.ICON_WARNING, self) != wx.YES:
            return
        del self._entries[idx]
        self._refresh(saved_index=idx)

    def get_entries(self):
        return [tuple(e) for e in self._entries]

    def commit(self):
        """Persist the list, then any keys typed this session.

        Returns True when the list itself was written. A key that fails to
        save is reported on the spot rather than silently leaving a provider
        that can never authenticate.
        """
        ok = save_api_providers(self._entries)
        for name, key in list(self._pending_keys.items()):
            try:
                import llm_backend
                llm_backend.write_provider_key(name, key)
            except Exception as e:
                wx.MessageBox(
                    "Saved the provider list, but couldn't save the key for "
                    "\"%s\": %s\n\nYou can set it as the %s_API_KEY "
                    "environment variable instead."
                    % (name, e, name.upper().replace("-", "_")),
                    "Key not saved", wx.OK | wx.ICON_WARNING, self)
        self._pending_keys = {}
        return ok
