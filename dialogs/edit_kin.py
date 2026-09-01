# SPDX-License-Identifier: CC0-1.0

"""dialogs.edit_kin - extracted from the former monolithic dialogs.py."""

import datetime
import subprocess
import threading
import time

import wx
import wx.lib.scrolledpanel as scrolled

import cron_helpers
import llm_backend
import tools as kin_tools
from kin_persistence import (
    DEFAULT_TELEGRAM_CONFIG, DEFAULT_DISCORD_CONFIG, DEFAULT_AGENT_CONFIG,
    DEFAULT_BASE_PROMPT, DEFAULT_DISTILL_PROMPT, APP_PROMPT_REGISTRY,
    live_distill_bookmark,
    agent_dir, room_dir, append_model_history,
    load_agent_config, save_agent_config,
    load_soul, save_soul, load_memory, save_memory,
    load_distill_prompt, save_distill_prompt,
    clear_distill_prompt, distill_prompt_is_overridden,
    load_base_prompt, save_kin_base_prompt,
    clear_kin_base_prompt, kin_base_prompt_is_overridden,
    load_app_prompt, save_kin_app_prompt,
    clear_kin_app_prompt, kin_app_prompt_is_overridden,
    load_kin_tools, save_kin_tools,
    load_ollama_hosts, THIS_MACHINE_NAME, resolve_kin_ollama_host,
)
from telegram_bot import telegram_test_token
from ._shared import _IntField, rebuild_listbox
from .cron_entry import CronEntryDialog
from .distill_prompt import DistillPromptDialog
from .app_prompt import AppPromptEditDialog
from .telegram_user import _TelegramUserDialog
from .discord_user import _DiscordUserDialog
from .telegram_group import _TelegramGroupDialog


def distill_progress_parts(*, stored, bookmark, total, trusted, walking,
                           advanced_at, pct=None, at_pct=0, paused=False):
    """Build the Memory tab's distillation-progress phrases for one scope.

    Pure so it can be tested without a frame — and it earns that, because
    this line is the ONLY instrument an operator has for whether
    distillation is alive. The old version printed a bare
    "N msgs undistilled", which is the same string whether a walk is
    chewing through chunks, finished, stalled twenty minutes ago, or is
    reporting a bookmark it failed to read. A real operator watching that
    number go up and down spent an evening hunting a bug that wasn't
    there, because nothing in it said when it was last true.

    So: show the bookmark beside the total rather than only their
    difference (a wrong bookmark hides inside a subtraction but not
    beside the number it's subtracted from); say when it last moved; say
    whether a walk is running; and when the position couldn't be read,
    say THAT rather than printing a number nobody can vouch for.
    """
    if not trusted:
        # No fallback to a cached snapshot. A confidently wrong number is
        # worse than an admitted gap when there's no second instrument.
        return [f"couldn't read progress from disk — {total} msgs total, "
                f"position unknown"]

    if bookmark <= 0:
        parts = [f"nothing distilled yet · {total} to go"]
    else:
        parts = [f"{bookmark} of {total} distilled · {total - bookmark} to go"]

    # live_distill_bookmark returns 0 when the stored bookmark is past the
    # end (restarted or externally rewritten history). Correct, but silent
    # — the counter drops to "nothing distilled" with no reason given.
    if stored > total:
        parts.append(f"bookmark was {stored}, past the end — re-reading "
                     f"from the start")

    if walking:
        # "walking" is this code's word for the from-start chain and has
        # never meant anything to a reader — the button next to it says
        # redistill, so this says redistill too.
        parts.append("redistilling now")
    elif paused:
        # Recorded as unfinished on disk with no chain running: the app
        # was closed part-way, or a chunk errored. Distinguishing this
        # from "idle" is the entire point of the row — they used to look
        # identical, so a redistill that had quietly stopped was
        # indistinguishable from one nobody had started, and the only
        # visible remedy was the button that starts over from zero.
        parts.append("redistill paused — press Continue redistilling")

    if advanced_at:
        stamp = str(advanced_at)
        parts.append(f"last advanced {stamp[11:19] or stamp}")
    elif bookmark > 0:
        parts.append("last advanced: not recorded")

    # Context share and the auto-fire threshold.
    if pct is None:
        parts.append("context share unknown")
        return parts
    parts.append(f"~{pct:.0f}% of context")

    if at_pct and at_pct > 0:
        if walking and pct >= at_pct:
            # Auto-fire is gated on _is_distill_in_flight, so while a walk
            # runs it CANNOT fire, however far over threshold the backlog
            # sits. Saying "auto-fires on next turn" here is simply false,
            # and it said it on every refresh for the length of the walk.
            parts.append(f"auto-fire held until the walk finishes "
                         f"(over the {at_pct}% threshold)")
        elif walking:
            parts.append(f"auto-fires at {at_pct}% (held while walking)")
        elif pct >= at_pct:
            # Note this is a standing condition, not an imminent event: a
            # backlog many times num_ctx stays over threshold for a long
            # time, so this line persists rather than counting down.
            parts.append(f"over the {at_pct}% threshold — fires on the "
                         f"next turn")
        else:
            parts.append(f"auto-fires at {at_pct}%")

    return parts


class EditKinDialog(wx.Dialog):
    """Edit a kin's settings: soul, generation parameters, memory, Telegram bot.

    Widget changes auto-persist to disk on change for sliders, spins, and the
    Telegram fields — same behavior the old Kin tab had. Soul and memory have
    explicit Save buttons + dirty tracking; closing the dialog with unsaved
    changes prompts to save.

    The chat window reads soul + memory from disk per turn, so this dialog
    being modal doesn't block chat-time data — closing the dialog cleanly
    leaves the kin in a consistent state.
    """

    def __init__(self, parent, kin_name):
        # Title framing: this dialog edits the kin's settings, not the
        # kin themself. "<name>'s settings" puts the kin as the owner of
        # what's being changed rather than as the object being edited.
        super().__init__(parent, title=f"{kin_name}'s settings",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(760, 700))
        self.frame = parent
        self.kin = kin_name
        self.cfg = load_agent_config(kin_name)
        self._loading = True
        self._soul_dirty = False
        self._memory_dirty = False
        self._suppress_soul_dirty = False
        self._suppress_memory_dirty = False
        # Per-slider debounce timers for the config write (L-B43) —
        # keyed by config key (generation sliders) or "voice:<key>"
        # (voice sliders). Display + NVDA speech stay per-step; only
        # the disk write waits for the value to settle.
        self._slider_save_timers = {}
        # Per-scope conversation cache for the counter overview (M-D5),
        # keyed by scope_key → ((mtime_ns, size), convo_list).
        self._scope_convo_cache = {}
        # Async schtasks-sync state (M-D3): one sync in flight at a
        # time; a newer request supersedes any queued one (latest wins).
        self._cron_sync_inflight = False
        self._cron_sync_latest = None

        # Outer scaffold: notebook for the tabs + a single Close button at
        # the bottom. The notebook has seven tabs (Identity / Model &
        # generation / Memory / Tools / Telegram / Cron / Voice) so the dialog
        # stops being one giant scrolling wall. wxPython's wx.Notebook +
        # wx.Panel-per-page setup gives us tab-scoped Tab navigation for
        # free — inactive pages are HWND-hidden and the native focus walk
        # uses IsWindowVisible, so NVDA Tab-key users only encounter the
        # controls on the active tab. No explicit Disable() of hidden
        # pages needed.
        outer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)

        # ─── Identity tab ────────────────────────────────────────────
        identity_panel = scrolled.ScrolledPanel(self.notebook)
        identity_sizer = wx.BoxSizer(wx.VERTICAL)
        host = identity_panel

        # Name row: shows the kin's current name (read-only TextCtrl for
        # tab-reachability — StaticText isn't in the wxMSW tab cycle) and
        # a Rename button that defers to the frame's rename flow. The
        # button closes this dialog before triggering the rename because
        # self.kin / self.cfg become stale the moment the directory is
        # renamed — keeping the dialog alive past that point would risk a
        # Save Soul / Save Memory writing to a directory that no longer
        # exists.
        name_label = wx.StaticText(host, label="Name:")
        font_n = name_label.GetFont(); font_n.SetWeight(wx.FONTWEIGHT_BOLD)
        name_label.SetFont(font_n)
        name_row = wx.BoxSizer(wx.HORIZONTAL)
        self.name_display = wx.TextCtrl(
            host, value=kin_name,
            style=wx.TE_READONLY | wx.TE_NO_VSCROLL,
        )
        self.name_display.SetName("Kin name")
        rename_btn = wx.Button(host, label="&Rename…")
        rename_btn.Bind(wx.EVT_BUTTON, self._on_rename_button)
        name_row.Add(self.name_display, proportion=1,
                     flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        name_row.Add(rename_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        soul_label = wx.StaticText(host, label="Soul (who they are):")
        font = soul_label.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD)
        soul_label.SetFont(font)
        self.soul_editor = wx.TextCtrl(host, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.soul_editor.SetMinSize((-1, 320))
        self.soul_editor.Bind(wx.EVT_TEXT, self._on_soul_changed)
        save_soul_btn = wx.Button(host, label="&Save soul")
        save_soul_btn.Bind(wx.EVT_BUTTON, self._on_save_soul)
        self.soul_status = wx.StaticText(host, label="")
        soul_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        soul_btn_row.Add(save_soul_btn, flag=wx.RIGHT, border=8)
        soul_btn_row.Add(self.soul_status, flag=wx.ALIGN_CENTER_VERTICAL)

        identity_sizer.Add(name_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        identity_sizer.Add(name_row,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        identity_sizer.Add(wx.StaticLine(host, style=wx.LI_HORIZONTAL),
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        identity_sizer.Add(soul_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        identity_sizer.Add(self.soul_editor, proportion=1,
                           flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        identity_sizer.Add(soul_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        host.SetSizer(identity_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(identity_panel, "Identity")

        # ─── Model & generation tab ──────────────────────────────────
        model_panel = scrolled.ScrolledPanel(self.notebook)
        model_sizer = wx.BoxSizer(wx.VERTICAL)
        host = model_panel

        model_label = wx.StaticText(host, label="Chat model:")
        font_m = model_label.GetFont(); font_m.SetWeight(wx.FONTWEIGHT_BOLD)
        model_label.SetFont(font_m)
        # Read-only display of the current model + "Change model…"
        # button. Replaces an old editable ComboBox + "Refresh models" +
        # "Browse models" combination that was leftover from before the
        # unified model browser existed. Two concrete problems with the
        # ComboBox:
        #   1. _populate's get_models() hit /api/show per Ollama model
        #      on cold cache — multi-second freeze on first Settings
        #      open.
        #   2. EVT_TEXT fires on every arrow-key step through dropdown
        #      items, and the handler called _refresh_ctx_model_max_label
        #      → _ollama_show_raw → blocking HTTP per never-seen model.
        # The unified ModelBrowserDialog handles both Ollama and
        # OpenRouter, has search/warmth filters, and loads async — so
        # the ComboBox was duplicating work another widget already did
        # better, while costing UI responsiveness.
        model_row = wx.BoxSizer(wx.HORIZONTAL)
        self.model_display = wx.TextCtrl(
            host,
            style=wx.TE_READONLY | wx.TE_MULTILINE
            | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        self.model_display.SetMinSize((-1, 48))
        self.model_display.SetName("Chat model")
        change_model_btn = wx.Button(host, label="&Change model…")
        change_model_btn.Bind(wx.EVT_BUTTON, self._on_browse_models)
        model_row.Add(self.model_display, proportion=2,
                      flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        model_row.Add(change_model_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        # Sampling / generation parameters (temperature, top-p / min-p,
        # the penalties, top-k) live in their own dialog behind this
        # button — see dialogs/sampling_settings.py. They're power-user
        # knobs most kin never need, so moving them off this tab keeps the
        # everyday Model controls short. Same button-opens-dialog pattern
        # as recall settings and the model browser, which NVDA discovers
        # reliably (an inline reveal checkbox got skimmed past).
        self.sampling_btn = wx.Button(host, label="&Sampling settings…")
        self.sampling_btn.Bind(wx.EVT_BUTTON, self._on_open_sampling_settings)
        # Read-only TextCtrl, not StaticText: a StaticText is only ever
        # spoken as the buddy label of the control created right after it,
        # and the next control here has its own name — so as StaticText
        # this blurb reaches sighted users only.
        sampling_blurb = wx.TextCtrl(
            host,
            value=("Temperature, top-p / min-p, the penalties, and top-k — "
                   "how the model picks each word. Defaults suit most models; "
                   "open to tune."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        sampling_blurb.SetMinSize((-1, 44))
        sampling_blurb.SetName("What sampling settings covers")

        ctx_row = wx.BoxSizer(wx.HORIZONTAL)
        ctx_lbl = wx.StaticText(host, label="Context window:", size=(120, -1))
        self.ctx_spin = _IntField(
            host,
            value=self.cfg.get("num_ctx", 8192),
            min_val=256, max_val=1048576,
            size=(140, -1),
            name="Context window",
            on_commit=lambda v: self._save_param("num_ctx", v),
        )
        # Read-only TextCtrl, not StaticText: the model's declared ceiling
        # is what tells the operator whether the Context window value above
        # is sane, and it's said nowhere else. As a StaticText it labels
        # nothing (the field beside it is named already) and is never spoken.
        self.ctx_model_max_lbl = wx.TextCtrl(
            host, style=wx.TE_READONLY | wx.BORDER_NONE)
        # Wide enough for the longest form this ever holds — the model max
        # plus the "(capped from prior value to N)" suffix.
        self.ctx_model_max_lbl.SetMinSize((380, -1))
        self.ctx_model_max_lbl.SetName("Model maximum context")
        ctx_row.Add(ctx_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        ctx_row.Add(self.ctx_spin)
        ctx_row.Add(self.ctx_model_max_lbl,
                    flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=8)
        # Async — see _kick_off_ctx_label_refresh for why. Construction-
        # time sync HTTP would freeze the dialog launch.
        self._kick_off_ctx_label_refresh()

        # Reply cap. Every other conversational surface had one (rooms:
        # per_turn_token_cap, Telegram: telegram_token_cap); the desktop
        # 1-on-1 path had none, and compat's output-cap warning pointed
        # the operator at a Settings field that did not exist.
        predict_row = wx.BoxSizer(wx.HORIZONTAL)
        predict_lbl = wx.StaticText(host, label="Reply cap (tokens):",
                                    size=(120, -1))
        self.predict_spin = _IntField(
            host,
            value=self.cfg.get("num_predict", 2000),
            min_val=256, max_val=131072,
            size=(140, -1),
            name="Reply cap in tokens",
            on_commit=lambda v: self._save_param("num_predict", v),
        )
        predict_row.Add(predict_lbl,
                        flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        predict_row.Add(self.predict_spin)

        think_label = wx.StaticText(host, label="Thinking:")
        font2 = think_label.GetFont(); font2.SetWeight(wx.FONTWEIGHT_BOLD)
        think_label.SetFont(font2)

        # Thinking effort tier — replaces the old binary "think" toggle.
        # Four wx.RadioButtons (not a wx.RadioBox; per the existing
        # CLAUDE.md note, RadioBox only announces the selected option
        # to NVDA, hiding the rest until the user expands. Independent
        # RadioButtons let NVDA Tab walk every option).
        #
        # "Off" matters: for OpenRouter-routed models like Claude
        # reasoning or OpenAI o-series that DEFAULT to thinking on,
        # the old binary toggle's "off" state just didn't send a
        # reasoning field — provider defaults took over and the kin
        # still thought. "Off" here sends reasoning.enabled=False,
        # which actually disables it.
        # Read-only TextCtrl, not StaticText: radio buttons take their name
        # from their own label and ignore any StaticText before them, so as a
        # StaticText this question is never spoken and a tabbing user hears
        # four options with nothing saying what is being chosen.
        think_effort_label = wx.TextCtrl(
            host, value="Thinking effort:",
            style=wx.TE_READONLY | wx.BORDER_NONE)
        think_effort_label.SetMinSize((130, -1))
        think_effort_label.SetName("Thinking effort")
        self.think_effort_off = wx.RadioButton(
            host, label="O&ff — never request reasoning, even on models that default to it",
            style=wx.RB_GROUP,
        )
        self.think_effort_low = wx.RadioButton(
            host, label="&Low — minimal reasoning effort",
        )
        self.think_effort_medium = wx.RadioButton(
            host, label="&Medium — provider default budget",
        )
        self.think_effort_high = wx.RadioButton(
            host, label="Hi&gh — heavy reasoning effort",
        )
        # Capability hint — repainted async after the model's
        # /api/show capabilities are known. Initial text covers the
        # before-fetch state so a sighted user sees something
        # explanatory, and so a screen-reader user tabbing to it
        # gets a non-empty announce.
        #
        # Read-only TextCtrl, not StaticText: this is the only thing that
        # says whether the tiers above do anything on this model, and a
        # StaticText isn't in the tab cycle — so the tabbing-user intent
        # above only holds if the widget can actually be focused.
        self.think_capability_hint = wx.TextCtrl(
            host, value="(checking model's reasoning support…)",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        self.think_capability_hint.SetMinSize((540, 58))
        self.think_capability_hint.SetName("Model reasoning support")
        self._think_effort_radios = [
            ("off", self.think_effort_off),
            ("low", self.think_effort_low),
            ("medium", self.think_effort_medium),
            ("high", self.think_effort_high),
        ]
        def _on_think_effort_change(_e):
            for tier, rb in self._think_effort_radios:
                if rb.GetValue():
                    self._save_param("think_effort", tier)
                    return
        for _tier, rb in self._think_effort_radios:
            rb.Bind(wx.EVT_RADIOBUTTON, _on_think_effort_change)
        # Which Ollama machine serves this kin's model. Read-only here —
        # the machine is chosen in the model browser ("Change model…"
        # above), alongside the model itself, so machine and model are
        # picked together in one place rather than two. No effect on
        # OpenRouter-routed kin.
        machine_lbl = wx.StaticText(host, label="Runs on (Ollama machine):")
        self.machine_display = wx.TextCtrl(host, style=wx.TE_READONLY)
        self.machine_display.SetName("Ollama machine")
        machine_row = wx.BoxSizer(wx.HORIZONTAL)
        machine_row.Add(machine_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        machine_row.Add(self.machine_display, proportion=1,
                        flag=wx.ALIGN_CENTER_VERTICAL)
        self._ollama_machine_row = machine_row

        # Lower-frequency model knobs (reasoning detail, image history,
        # caching, OpenRouter provider routing, the streaming watchdog,
        # Ollama keep-alive/preload) live in their own dialog behind this
        # button — see dialogs/model_options.py. Same button-opens-dialog
        # pattern as Sampling settings; keeps the everyday Model tab short.
        self.more_options_btn = wx.Button(host, label="M&ore model options…")
        self.more_options_btn.Bind(wx.EVT_BUTTON, self._on_open_model_options)

        model_sizer.Add(model_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        model_sizer.Add(model_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(ctx_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(predict_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(self._ollama_machine_row,
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(wx.StaticLine(host, style=wx.LI_HORIZONTAL),
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(think_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(think_effort_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(self.think_effort_off, flag=wx.LEFT | wx.RIGHT, border=6)
        model_sizer.Add(self.think_effort_low, flag=wx.LEFT | wx.RIGHT, border=6)
        model_sizer.Add(self.think_effort_medium, flag=wx.LEFT | wx.RIGHT, border=6)
        model_sizer.Add(self.think_effort_high, flag=wx.LEFT | wx.RIGHT, border=6)
        model_sizer.Add(self.think_capability_hint,
                        flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(wx.StaticLine(host, style=wx.LI_HORIZONTAL),
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(self.sampling_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(sampling_blurb, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        model_sizer.Add(self.more_options_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        host.SetSizer(model_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(model_panel, "Model && generation")

        # ─── Memory tab ──────────────────────────────────────────────
        # Everything memory-related lives here, top to bottom:
        # artifact (memory.md editor) → manual ops (Distill / Consolidate
        # / Edit prompt / Change distillation model) → auto-distillation
        # cadence (on-close, every-N, at-X%) → per-surface counter
        # overview + per-surface manual distill. Previously the cadence
        # controls + per-surface UI lived on a separate "Chat" tab (the
        # v0.2.35 split tried to separate "memory artifact" from
        # "chat-flow cadence"), but users naturally look for memory
        # controls under "Memory" — so it all lives here now.
        memory_panel = scrolled.ScrolledPanel(self.notebook)
        memory_sizer = wx.BoxSizer(wx.VERTICAL)
        host = memory_panel

        memory_label = wx.StaticText(host, label="Memory (your kin-curated index — staging notes feed in via tending):")
        font = memory_label.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD)
        memory_label.SetFont(font)
        self.memory_editor = wx.TextCtrl(host, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
        self.memory_editor.SetMinSize((-1, 220))
        self.memory_editor.Bind(wx.EVT_TEXT, self._on_memory_changed)

        save_mem_btn = wx.Button(host, label="Save &memory")
        save_mem_btn.Bind(wx.EVT_BUTTON, self._on_save_memory)
        self.distill_btn = wx.Button(host, label="Distill &all surfaces now")
        self.distill_btn.Bind(wx.EVT_BUTTON, self._on_distill_all_surfaces)
        self.consolidate_btn = wx.Button(host, label="Co&nsolidate")
        self.consolidate_btn.Bind(wx.EVT_BUTTON, self._on_consolidate_now)
        edit_prompt_btn = wx.Button(host, label="Edit &prompt...")
        edit_prompt_btn.Bind(wx.EVT_BUTTON, self._on_edit_distill_prompt)
        self.memory_status = wx.StaticText(host, label="")
        mem_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        mem_btn_row.Add(save_mem_btn, flag=wx.RIGHT, border=8)
        mem_btn_row.Add(self.distill_btn, flag=wx.RIGHT, border=8)
        mem_btn_row.Add(self.consolidate_btn, flag=wx.RIGHT, border=8)
        mem_btn_row.Add(edit_prompt_btn, flag=wx.RIGHT, border=8)
        mem_btn_row.Add(self.memory_status, flag=wx.ALIGN_CENTER_VERTICAL)

        mem_model_lbl = wx.StaticText(host, label="Distillation model:")
        # Same shape as the chat-model row above: read-only display +
        # "Change…" button. Empty cfg value (the common case) renders
        # as "(same as chat model)" so the meaning is obvious to a
        # screen-reader user landing on the field. A separate "Use
        # chat model" button clears the override back to the default.
        mem_model_row = wx.BoxSizer(wx.HORIZONTAL)
        self.mem_model_display = wx.TextCtrl(
            host,
            style=wx.TE_READONLY | wx.TE_MULTILINE
            | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        self.mem_model_display.SetMinSize((-1, 48))
        self.mem_model_display.SetName("Distillation model")
        change_mem_btn = wx.Button(host, label="Change &distillation model…")
        change_mem_btn.Bind(wx.EVT_BUTTON, self._on_change_memory_model)
        # "&m" belongs to "Save &memory" on this tab (L-B41).
        reset_mem_btn = wx.Button(host, label="&Use chat model")
        reset_mem_btn.Bind(wx.EVT_BUTTON, self._on_reset_memory_model)
        mem_model_row.Add(self.mem_model_display, proportion=2,
                          flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        mem_model_row.Add(change_mem_btn,
                          flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        mem_model_row.Add(reset_mem_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        # Auto-distillation cadence (moved here from the now-removed
        # Chat tab). Help text first, widgets below it.
        cadence_help = wx.TextCtrl(
            host,
            value=(
                "Auto-distillation triggers — when the summarizer "
                "should run on its own. Its output goes to the kin's "
                "staging area (one file per surface) for the kin to "
                "review during tending; it does NOT auto-rewrite "
                "memory.md. Each conversation surface (desktop chat, "
                "individual Telegram DMs, Telegram groups) keeps its "
                "own message count toward the threshold. Surfaces with "
                "share-with-desktop on share a single \"desktop\" "
                "counter; other surfaces each have an independent "
                "counter. The percentage trigger is a separate, "
                "independent trip that fires when the conversation a "
                "surface has piled up since its last distillation "
                "reaches a set share of the model's context window — a "
                "safety net so a fast-growing chat gets noted before "
                "it overflows. Either trigger firing distills that "
                "surface; set a trigger to 0 to disable it. "
                "One more thing governs both: if a surface is so far "
                "behind that a single distillation can't catch it up — "
                "usually after importing a long history — it is working "
                "through a backlog, and chasing it after every reply "
                "would keep the model busy all day without ever "
                "finishing. In that case the automatic triggers wait the "
                "number of minutes set below between runs. The backlog "
                "still gets done; it just stops competing with your "
                "conversation. An ordinary catch-up, where one run "
                "finishes the job, never waits."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        cadence_help.SetMinSize((-1, 180))
        cadence_help.SetName("Auto-distillation help")

        self.distill_on_close_check = wx.CheckBox(host, label="Auto-distill when leaving this kin")
        self.distill_on_close_check.Bind(wx.EVT_CHECKBOX, self._on_distill_on_close_toggle)

        every_n_row = wx.BoxSizer(wx.HORIZONTAL)
        every_n_lbl = wx.StaticText(host, label="Auto-distill every N messages (0 = off):")
        self.distill_every_n_spin = _IntField(
            host,
            value=self.cfg.get("memory_distill_every_n", 0),
            min_val=0, max_val=100,
            size=(80, -1),
            # The "(0 = off)" has to be in the NAME, not just the StaticText.
            # A StaticText before a control with its own SetName is never
            # announced, so a screen-reader user tabbing here heard
            # "Auto-distill every N messages, edit" and had no way to learn
            # that 0 is the off switch -- while it sat in plain sight for
            # everyone else. Worse than unclear: asked what 0 does, a reader
            # with only the spoken label reasonably guesses "every 0 messages"
            # = constantly, which is the exact opposite of what it does.
            name="Auto-distill every N messages (0 = off)",
            on_commit=lambda v: self._save_param("memory_distill_every_n", v),
        )
        every_n_row.Add(every_n_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        every_n_row.Add(self.distill_every_n_spin)

        at_pct_row = wx.BoxSizer(wx.HORIZONTAL)
        at_pct_lbl = wx.StaticText(
            host,
            label="Auto-distill at this % of the context window (0 = off):")
        self.distill_at_pct_spin = _IntField(
            host,
            value=self.cfg.get("memory_distill_at_pct", 0),
            min_val=0, max_val=95,
            size=(80, -1),
            # "(0 = off)" in the name for the same reason as the field above.
            name="Auto-distill at this percent of the context window (0 = off)",
            on_commit=lambda v: self._save_param("memory_distill_at_pct", v),
        )
        at_pct_row.Add(at_pct_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        at_pct_row.Add(self.distill_at_pct_spin)

        # Memory-log index cap. Created here so tab order reads it with the
        # other memory-size controls (wxPython tab order follows CREATION
        # order, not sizer order).
        #
        # The LABEL names the unit, and so does the config key. A field
        # reading "Memory log index cap: [1500]" tells a person nothing they
        # can act on — 1500 files? lines? tokens? Characters is what the
        # builder counts exactly, so characters is what both say.
        log_idx_row = wx.BoxSizer(wx.HORIZONTAL)
        log_idx_lbl = wx.StaticText(
            host,
            label="List memory logs in memory.md, up to this many "
                  "characters (0 = list them all):")
        self.memory_log_index_field = _IntField(
            host,
            value=self.cfg.get("memory_log_index_max_chars", 1500),
            min_val=0, max_val=50000,
            size=(90, -1),
            name=("List memory logs in memory dot md, up to this many "
                  "characters (0 = list them all)"),
            on_commit=lambda v: self._save_param(
                "memory_log_index_max_chars", v),
        )
        log_idx_row.Add(log_idx_lbl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
                        border=4)
        log_idx_row.Add(self.memory_log_index_field)

        # Memory-index budget. Created here so tab order reads it straight
        # after the logs-index cap beside it — together the two are "how
        # big may memory.md get". Tab order follows CREATION order.
        #
        # The label says "ask", not "cap", because that is what it does. A
        # person reading "budget" would reasonably expect a trim, and there
        # deliberately is not one: memory.md is the kin's own writing and
        # nothing here deletes it. A control that overstates its power is
        # worse than no control — it invites trusting a limit that is not
        # being enforced.
        mem_budget_row = wx.BoxSizer(wx.HORIZONTAL)
        mem_budget_lbl = wx.StaticText(
            host,
            label="Ask the kin to prune memory.md when its own notes pass "
                  "this many characters (0 = never ask):")
        self.memory_index_budget_field = _IntField(
            host,
            value=self.cfg.get("memory_index_budget_chars", 5000),
            min_val=0, max_val=100000,
            size=(90, -1),
            name=("Ask the kin to prune memory dot md when its own notes "
                  "pass this many characters (0 = never ask)"),
            on_commit=lambda v: self._save_param(
                "memory_index_budget_chars", v),
        )
        mem_budget_row.Add(mem_budget_lbl,
                           flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        mem_budget_row.Add(self.memory_index_budget_field)

        # Backlog pacing. Created here, immediately after the two triggers it
        # governs, because on wxPython tab order follows CREATION order, not
        # sizer order — a field added at the end of the method would be read
        # last however it is laid out.
        backlog_row = wx.BoxSizer(wx.HORIZONTAL)
        backlog_lbl = wx.StaticText(
            host,
            label="When catching up on a backlog, wait this many minutes "
                  "between automatic distillations (0 = don't wait):")
        self.distill_backlog_pace_spin = _IntField(
            host,
            value=self.cfg.get("distill_backlog_pace_mins", 30),
            min_val=0, max_val=1440,
            size=(80, -1),
            # "(0 = don't wait)" in the name, same reason as the two fields
            # above: a StaticText before a control that has its own SetName is
            # never announced, so anything only in the visible label reaches
            # sighted readers alone.
            name="When catching up on a backlog, wait this many minutes "
                 "between automatic distillations (0 = do not wait)",
            on_commit=lambda v: self._save_param(
                "distill_backlog_pace_mins", v),
        )
        backlog_row.Add(backlog_lbl,
                        flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        backlog_row.Add(self.distill_backlog_pace_spin)

        # Per-surface counter overview + per-surface manual distill.
        counters_label = wx.StaticText(host, label="Pending messages since last distillation:")
        font = counters_label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        counters_label.SetFont(font)
        # Auto-distillation trigger summary — the OFF state explainer
        # or the "fires every N / at X%" line. Read-only TextCtrl so
        # NVDA users can tab to it.
        self.chat_counters_summary = wx.TextCtrl(
            host,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        self.chat_counters_summary.SetMinSize((-1, 90))
        # Carries the section header's words, because the bold StaticText above
        # ("Pending messages since last distillation:") is not focusable and
        # this control's SetName overrides it as the announced name -- so
        # without this, the heading for this whole section reached sighted
        # users only, and a tabbing user met the summary with no idea what it
        # was summarising.
        self.chat_counters_summary.SetName(
            "Pending messages since last distillation — summary")
        # Per-scope rows in a ListBox so each surface is its own
        # NVDA-readable item with first-letter nav. The parallel
        # _chat_counter_scope_keys list maps the selected row index
        # back to the scope_key so _on_distill_selected_scope can
        # operate on whichever surface is highlighted.
        # SetName() does nothing on wxMSW and the summary above is a read-only
        # TextCtrl, which cannot label anything — so this list announced as a
        # bare "list box". Only a StaticText immediately before it works.
        chat_counters_label = wx.StaticText(host, label="Per-s&urface counters:")
        self.chat_counters_list = wx.ListBox(host, style=wx.LB_SINGLE)
        self.chat_counters_list.SetMinSize((-1, 140))
        self._chat_counter_scope_keys = []

        # How a "Redistill selected from start" walk paces itself. Default
        # ("Unattended") is the original all-the-way-through behavior —
        # nothing about existing use changes unless this is deliberately
        # switched. "One day/hour at a time" lets the operator listen to
        # what a redistill produces as it goes rather than only ever
        # choosing between "let it run unattended" and "cancel and lose
        # the only lever back to where it was". See _on_redistill_from_start
        # and MemoryMixin's pacing helpers (_walk_boundary_ts,
        # _WALK_PACING_*) for how a chosen pacing actually stops a walk at
        # a calendar boundary instead of chaining straight through it.
        pacing_label = wx.StaticText(host, label="Redistill &pacing:")
        self.walk_pacing_choice = wx.Choice(host, choices=[
            "Unattended (run straight through)",
            "One day at a time",
            "One hour at a time",
            "One chunk at a time",
        ])
        self.walk_pacing_choice.SetSelection(0)

        counters_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        refresh_counters_btn = wx.Button(host, label="&Refresh counters")
        refresh_counters_btn.Bind(wx.EVT_BUTTON,
                                  lambda _e: self._refresh_chat_counters_display())
        self.distill_scope_btn = wx.Button(
            host, label="Distill &selected surface now")
        self.distill_scope_btn.Bind(
            wx.EVT_BUTTON, self._on_distill_selected_scope)
        # Recovery / catch-up tool: resets the selected surface's
        # bookmark to 0 and walks the conversation in budget-bounded
        # chunks, auto-firing the next chunk after each completes.
        # See _on_redistill_from_start for the cost-confirmation flow
        # and the frame's _on_distill_done auto-chain that drives it.
        # "&d" belongs to "Change &distillation model…" on this tab (L-B41).
        self.redistill_btn = wx.Button(
            host, label="R&edistill selected from start")
        self.redistill_btn.Bind(
            wx.EVT_BUTTON, self._on_redistill_from_start)
        # Cancel a walk-from-start auto-chain that's in progress.
        # Enabled only while a walk is running for this kin. Clearing
        # the _walking_from_start flag is enough — the currently-running
        # bite finishes normally, no new bite gets scheduled. Without
        # this button there's no way to abort a walk except by closing
        # Hearthkin (the walk loops as long as the bookmark is short
        # of the conversation end).
        # Originally "Cancel redistill" rather than "Cancel walk" — "walk"
        # is this code's own word for the from-start chain
        # (_walking_from_start) and never appeared anywhere a user could
        # see it. Widened again since: this button now ALSO stops
        # "Distill all surfaces now" from draining any further surfaces
        # after the current one, and gives an honest thing to press
        # during a plain "Distill selected surface now" too — reported
        # live: an accidental press of "all surfaces now" had nothing
        # that could stop it short of quitting Hearthkin. "Distilling",
        # not "redistill", because the other two triggers aren't redistills
        # in this app's own vocabulary and the old label read as
        # unrelated to what had actually been pressed.
        # Continue a redistill that stopped part-way — from the bookmark,
        # NOT from zero. Enabled only when this kin has an unfinished
        # redistill recorded on disk and nothing running for it.
        #
        # Its absence is what made a long redistill so expensive to own.
        # Quitting the app, or one chunk erroring, left the progress on
        # disk but offered no way back to it — and the button that WAS
        # offered ("from start") reset the bookmark to zero, so the
        # obvious move threw away everything done so far. A kin's whole
        # history got walked from the beginning several times over.
        # "&o": every other letter in the label is taken on this tab —
        # m by "Save &memory", c by "&Cancel distilling", r by "&Refresh
        # counters", d by "Change &distillation model…", s and e by the
        # two buttons either side of this one.
        self.resume_walk_btn = wx.Button(
            host, label="C&ontinue redistilling")
        self.resume_walk_btn.Bind(
            wx.EVT_BUTTON, self._on_resume_walk)
        self.resume_walk_btn.Disable()
        # Widened beyond a "Redistill from start" walk — see
        # _on_cancel_walk. It used to be enabled only during a walk, so
        # there was no way to stop "Distill all surfaces now" from
        # continuing through every remaining surface after an accidental
        # press, and nothing at all to press during a plain "Distill
        # selected surface now". Same button, same mnemonic (&C) — the
        # label just no longer promises something narrower than what it
        # actually does.
        self.cancel_walk_btn = wx.Button(
            host, label="&Cancel distilling")
        self.cancel_walk_btn.Bind(
            wx.EVT_BUTTON, self._on_cancel_walk)
        self.cancel_walk_btn.Disable()
        # Catch up: chain forward from the bookmark until this surface is
        # done, unattended. "Redistill from start" without the "from start".
        #
        # Nothing else could do this. The automatic trigger only fires after
        # a reply, so a backlog never moves while you're asleep; "Distill
        # selected surface now" does one bite per press; and the walk chains
        # by itself but rewinds to zero first. On a surface sitting tens of
        # thousands of messages behind, that left pressing a button several
        # hundred times as the only way forward that didn't re-bill work
        # already paid for.
        # "&u": m, c, r, d, e, s and o are all taken on this tab.
        self.catchup_btn = wx.Button(
            host, label="Catch &up on selected")
        self.catchup_btn.Bind(wx.EVT_BUTTON, self._on_catch_up)
        counters_btn_row.Add(refresh_counters_btn, flag=wx.RIGHT, border=8)
        counters_btn_row.Add(self.distill_scope_btn, flag=wx.RIGHT, border=8)
        counters_btn_row.Add(self.catchup_btn, flag=wx.RIGHT, border=8)
        counters_btn_row.Add(self.redistill_btn, flag=wx.RIGHT, border=8)
        counters_btn_row.Add(self.resume_walk_btn, flag=wx.RIGHT, border=8)
        counters_btn_row.Add(self.cancel_walk_btn)

        memory_sizer.Add(memory_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        memory_sizer.Add(self.memory_editor, proportion=1,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        memory_sizer.Add(mem_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        memory_sizer.Add(mem_model_lbl, flag=wx.LEFT | wx.RIGHT, border=6)
        memory_sizer.Add(mem_model_row,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        memory_sizer.Add(wx.StaticLine(host, style=wx.LI_HORIZONTAL),
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(cadence_help,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(self.distill_on_close_check,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(every_n_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(at_pct_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(log_idx_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        memory_sizer.Add(mem_budget_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        memory_sizer.Add(backlog_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(wx.StaticLine(host, style=wx.LI_HORIZONTAL),
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(counters_label,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(self.chat_counters_summary,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(chat_counters_label,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(self.chat_counters_list,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(pacing_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(self.walk_pacing_choice,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(counters_btn_row,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=8)

        # ── Per-turn memory recall ─────────────────────────────────────
        # The recall knobs (how much relevant memory to auto-surface each
        # turn, what to favour/avoid) open in their own dialog behind a
        # button. An earlier inline "show advanced settings" checkbox was
        # skipped past by NVDA — a button that opens a dialog is the app's
        # standard, screen-reader-discoverable pattern (cf. the model
        # browser, the search-filter dialogs).
        recall_sep = wx.StaticLine(host, style=wx.LI_HORIZONTAL)
        recall_hdr = wx.StaticText(host, label="Memory recall (per-turn)")
        _rf = recall_hdr.GetFont(); _rf.SetWeight(wx.FONTWEIGHT_BOLD)
        recall_hdr.SetFont(_rf)
        # Read-only TextCtrl, not StaticText: StaticText isn't focusable on
        # wxMSW, and the control after this one is a button (which uses its own
        # label as its accessible name), so as a StaticText this paragraph
        # labelled nothing and was never announced to anyone tabbing. It
        # explained the whole feature to sighted users only. This is the house
        # pattern for explanatory text -- see the "Accessibility-first widgets"
        # convention in CLAUDE.md, and cadence_help above, which already does it.
        recall_blurb = wx.TextCtrl(
            host,
            value=("Before each reply, Hearthkin can surface the most relevant "
                   "slice of this kin's own depth logs and journals — inline, "
                   "no tool call. Open the settings to tune how much it pulls "
                   "and what it favours or avoids."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        recall_blurb.SetMinSize((-1, 60))
        recall_blurb.SetName("Memory recall (per-turn)")
        self.recall_settings_btn = wx.Button(host, label="Memory &recall settings…")
        self.recall_settings_btn.Bind(wx.EVT_BUTTON, self._on_open_recall_settings)

        memory_sizer.Add(recall_sep, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(recall_hdr, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        memory_sizer.Add(recall_blurb, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        memory_sizer.Add(self.recall_settings_btn,
                         flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=8)

        host.SetSizer(memory_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(memory_panel, "Memory")

        # ─── Tools tab ───────────────────────────────────────────────
        tools_panel = scrolled.ScrolledPanel(self.notebook)
        tools_sizer = wx.BoxSizer(wx.VERTICAL)
        host = tools_panel

        tools_header = wx.TextCtrl(
            host,
            value=(
                "Tools section. The boxes below are PERMISSIONS, not "
                "commands — checking one lets this kin call that tool "
                "during a reply when it decides the tool fits. You are "
                "not running the tools yourself. Toggle saves immediately. "
                "Note: enabling any tool switches this kin's chats to "
                "non-streaming mode (the model has to pause for each "
                "tool result, so replies arrive all at once rather than "
                "sentence-by-sentence). Leave every box unchecked to "
                "keep the streaming experience."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        tools_header.SetName("Tools section overview")
        tools_header.SetMinSize((-1, 110))

        self._tool_checks = {}
        _tool_widgets = []

        # Trust level: three RadioButtons inside a wx.StaticBox so the
        # whole group announces to NVDA as "Tool call gating, exec and
        # webcam, group" when focus enters. The earlier RadioBox-only
        # shape was inaudible-by-design (RadioBox suppresses unselected
        # options); the bare RadioButton shape (no box) was tab-walkable
        # but had no group context — NVDA would read each option's text
        # but never tell the user what the group as a whole was for.
        # StaticBox + RadioButtons gets both: group context AND each
        # option spoken.
        self._trust_explainers = [
            "Untrusted: every exec call needs your approval before "
            "running. Default for new kin. Safe choice when you're "
            "setting up a kin you don't know well yet.",
            "Trusted: only obviously destructive shapes need approval "
            "(rm -rf /, force-push to main, Windows drive wipes — see "
            "the denylist in tools/_exec_denylist.py for the full list). "
            "Everything else runs without prompting.",
            "Full: no approval gating at all. The kin can run any shell "
            "command without prompting you, including ones on the "
            "denylist. Root-access trust — only set this for a kin "
            "you've watched on Trusted for a while and explicitly want "
            "to hand the keys to.",
        ]
        _current_trust = self.cfg.get("tool_trust", "untrusted")

        trust_box = wx.StaticBox(
            host, label="Tool call gating (exec and webcam capture)",
        )
        trust_sizer = wx.StaticBoxSizer(trust_box, wx.VERTICAL)

        # Inner widgets parented to the StaticBox (trust_box), NOT host,
        # so NVDA nests them under the box label.
        trust_untrusted_rb = wx.RadioButton(
            trust_box,
            label="&Untrusted — every exec call asks for your approval (default)",
            style=wx.RB_GROUP,
        )
        trust_trusted_rb = wx.RadioButton(
            trust_box,
            label="T&rusted — only denylist patterns (rm -rf /, drive wipes, etc.) prompt",
        )
        trust_full_rb = wx.RadioButton(
            trust_box,
            label="&Full — no approval gating; the kin can run any shell command",
        )
        self._trust_radios = [
            ("untrusted", trust_untrusted_rb),
            ("trusted", trust_trusted_rb),
            ("full", trust_full_rb),
        ]
        _matched = False
        for tier, rb in self._trust_radios:
            if tier == _current_trust:
                rb.SetValue(True)
                _matched = True
                break
        if not _matched:
            trust_untrusted_rb.SetValue(True)

        _initial_trust_idx = ["untrusted", "trusted", "full"].index(
            _current_trust if _current_trust in ("untrusted", "trusted", "full") else "untrusted"
        )
        trust_explainer = wx.TextCtrl(
            trust_box,
            value=self._trust_explainers[_initial_trust_idx],
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        trust_explainer.SetName("Trust level explainer")
        trust_explainer.SetMinSize((-1, 70))
        self._trust_explainer = trust_explainer

        def _on_trust_change(_e):
            for tier, rb in self._trust_radios:
                if rb.GetValue():
                    self._save_param("tool_trust", tier)
                    if getattr(self, "_trust_explainer", None) is not None:
                        idx = ["untrusted", "trusted", "full"].index(tier)
                        self._trust_explainer.SetValue(self._trust_explainers[idx])
                    return
        for _, rb in self._trust_radios:
            rb.Bind(wx.EVT_RADIOBUTTON, _on_trust_change)

        # Pack the inner widgets into the StaticBoxSizer with a little
        # left padding so they don't crowd the box's left frame.
        for _, rb in self._trust_radios:
            trust_sizer.Add(rb, flag=wx.LEFT | wx.TOP, border=4)
        trust_sizer.Add(trust_explainer,
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=4)

        _tool_widgets.append(trust_sizer)

        # Tool-behaviour knobs (approval timeout, tool history kept, tool
        # result cap, max tool calls per reply) live in their own dialog
        # behind this button — see dialogs/tool_settings.py. Same
        # button-opens-dialog pattern as the model dialogs; keeps the Tools
        # tab focused on the trust level and the per-tool enable list.
        self.tool_settings_btn = wx.Button(host, label="Tool &behaviour settings…")
        self.tool_settings_btn.Bind(wx.EVT_BUTTON, self._on_open_tool_settings)
        _tool_widgets.append(self.tool_settings_btn)
        # Park: how this kin plays, and WHICH park it tends. Sits here because
        # the park is reached through the `tff` tool. Both settings were
        # JSON-only before — the shared-park one is what lets several kin and
        # the operator tend one park together.
        self.park_settings_btn = wx.Button(host, label="Par&k settings…")
        self.park_settings_btn.Bind(wx.EVT_BUTTON, self._on_open_park_settings)
        _tool_widgets.append(self.park_settings_btn)

        # Whether this kin's model will actually CALL any of the tools
        # below. Ollama's capability flag can't answer that -- it reports
        # whether the model's template can express a tool call, not
        # whether the weights ever emit one -- so this asks the model
        # directly and reports what came back. A model that fails here
        # leaves a kin looking perfectly well while quietly doing
        # nothing, which is the hardest failure to notice from outside.
        self.tool_probe_btn = wx.Button(host, label="Test tool &calling…")
        self.tool_probe_btn.Bind(wx.EVT_BUTTON, self._on_test_tool_calling)
        _tool_widgets.append(self.tool_probe_btn)

        enabled_set = set(load_kin_tools(self.kin))
        # reach_out (and any other proactive-only tool) is NOT an everyday
        # allowlist tool — it belongs to the Proactive-heartbeat feature on the
        # Cron tab, which grants it itself. Keeping it out of this list is the
        # de-weirding: no more lone "Enable reach_out" checkbox disconnected
        # from the thing it's part of.
        available = [
            t for t in kin_tools.list_available()
            if t not in kin_tools._PROACTIVE_TOOLS
        ]
        # Seed with the trust radios' mnemonics (&Untrusted / T&rusted /
        # &Full) so the auto-assigner below can't hand a tool checkbox
        # a letter that already belongs to a radio on this tab (L-B41).
        claimed_letters = {"u", "r", "f"}
        for tname in available:
            fn = kin_tools._REGISTRY.get(tname)
            first_line = ""
            if fn and fn.__doc__:
                _doc = fn.__doc__.strip().split("\n\n", 1)[0]
                _doc = " ".join(_doc.split())
                if ". " in _doc:
                    first_line = _doc.split(". ", 1)[0] + "."
                else:
                    first_line = _doc
            labeled = tname
            for _i, _ch in enumerate(tname):
                if _ch.isalpha() and _ch.lower() not in claimed_letters:
                    claimed_letters.add(_ch.lower())
                    labeled = tname[:_i] + "&" + tname[_i:]
                    break
            if first_line:
                chk_label = f"Enable {labeled} — {first_line}"
            else:
                chk_label = f"Enable {labeled}"
            chk = wx.CheckBox(host, label=chk_label)
            chk.SetValue(tname in enabled_set)
            chk.Bind(wx.EVT_CHECKBOX, lambda evt, n=tname: self._on_tool_toggle(n))
            self._tool_checks[tname] = chk
            _tool_widgets.append(chk)

        if not available:
            # Read-only TextCtrl, not StaticText: in this state it's the only
            # thing on the tab explaining why there are no tool checkboxes,
            # and a StaticText with no control after it is spoken to nobody.
            none_lbl = wx.TextCtrl(
                host, value="No tools registered in this build.",
                style=wx.TE_READONLY | wx.BORDER_NONE,
            )
            none_lbl.SetMinSize((260, -1))
            none_lbl.SetName("Tool list state")
            _tool_widgets.append(none_lbl)

        tools_sizer.Add(tools_header,
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        for _tw in _tool_widgets:
            tools_sizer.Add(_tw, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        host.SetSizer(tools_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(tools_panel, "Tools")

        # ─── Telegram tab ────────────────────────────────────────────
        tg_panel = scrolled.ScrolledPanel(self.notebook)
        tg_sizer = wx.BoxSizer(wx.VERTICAL)
        host = tg_panel

        tg_label = wx.StaticText(host, label="Telegram bot:")
        font = tg_label.GetFont(); font.SetWeight(wx.FONTWEIGHT_BOLD)
        tg_label.SetFont(font)

        token_lbl = wx.StaticText(host, label="Bot token:")
        # Commit on focus-loss / Enter rather than EVT_TEXT — the old
        # per-keystroke binding rewrote config.json once per typed
        # character (L-B43): pure disk churn that also widened the
        # window for clobbering concurrent config writes.
        self.tg_token_field = wx.TextCtrl(
            host, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        self.tg_token_field.Bind(wx.EVT_KILL_FOCUS, self._on_tg_token_changed)
        self.tg_token_field.Bind(wx.EVT_TEXT_ENTER, self._on_tg_token_changed)
        tg_test_btn = wx.Button(host, label="&Test token")
        tg_test_btn.Bind(wx.EVT_BUTTON, self._on_tg_test_token)
        token_row = wx.BoxSizer(wx.HORIZONTAL)
        token_row.Add(self.tg_token_field, proportion=1,
                      flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        token_row.Add(tg_test_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        # Read-only TextCtrl, not StaticText: this carries the whole result of
        # "Test token", and it's also the only explanation when ticking "Run
        # Telegram bot" silently un-ticks itself for want of a token. A
        # StaticText here is never spoken, so that failure is silent.
        self.tg_test_label = wx.TextCtrl(
            host, style=wx.TE_READONLY | wx.BORDER_NONE)
        self.tg_test_label.SetMinSize((420, -1))
        self.tg_test_label.SetName("Bot token test result")

        allow_lbl = wx.StaticText(
            host,
            label=("Users allowed to DM this kin (private chats only; tool "
                   "access set per user):")
        )
        self.tg_users_list = wx.ListBox(host, style=wx.LB_SINGLE)
        self.tg_users_list.SetMinSize((-1, 160))
        tg_users_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.tg_add_user_btn = wx.Button(host, label="&Add user...")
        self.tg_add_user_btn.Bind(wx.EVT_BUTTON, self._on_tg_add_user)
        self.tg_edit_user_btn = wx.Button(host, label="Ed&it user...")
        self.tg_edit_user_btn.Bind(wx.EVT_BUTTON, self._on_tg_edit_user)
        self.tg_remove_user_btn = wx.Button(host, label="Re&move user")
        self.tg_remove_user_btn.Bind(wx.EVT_BUTTON, self._on_tg_remove_user)
        tg_users_btn_row.Add(self.tg_add_user_btn, flag=wx.RIGHT, border=6)
        tg_users_btn_row.Add(self.tg_edit_user_btn, flag=wx.RIGHT, border=6)
        tg_users_btn_row.Add(self.tg_remove_user_btn)

        # ─── Groups subsection ────────────────────────────────────
        # Per-group opt-in for kin participation in Telegram groups and
        # supergroups. Without an entry here, the bot answers /whoami in any
        # group (for chat-ID discovery) but stays silent on normal messages —
        # the deliberate "kin can be added to a group without auto-engaging"
        # safety design. Once a group IS opted in, the kin talks to everyone
        # in it (subject to the group's participation policy), EXCEPT anyone
        # on that group's mute list — group participation is independent of
        # the DM allow-list above, so neither one grants the other.
        tg_groups_label = wx.StaticText(
            host,
            label="Groups (kin converses with everyone here except muted people; chat IDs are negative):",
        )
        self.tg_groups_list = wx.ListBox(host, style=wx.LB_SINGLE)
        self.tg_groups_list.SetMinSize((-1, 100))
        tg_groups_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.tg_add_group_btn = wx.Button(host, label="A&dd group...")
        self.tg_add_group_btn.Bind(wx.EVT_BUTTON, self._on_tg_add_group)
        # "&t" belongs to "&Test token" on this tab (L-B41).
        self.tg_edit_group_btn = wx.Button(host, label="Edit &group...")
        self.tg_edit_group_btn.Bind(wx.EVT_BUTTON, self._on_tg_edit_group)
        self.tg_remove_group_btn = wx.Button(host, label="Remo&ve group")
        self.tg_remove_group_btn.Bind(wx.EVT_BUTTON, self._on_tg_remove_group)
        tg_groups_btn_row.Add(self.tg_add_group_btn, flag=wx.RIGHT, border=6)
        tg_groups_btn_row.Add(self.tg_edit_group_btn, flag=wx.RIGHT, border=6)
        tg_groups_btn_row.Add(self.tg_remove_group_btn)
        # Read-only TextCtrl, not StaticText: adding a group needs its chat
        # ID and this is the only place that says how to get one. The next
        # control is a button, which takes its name from its own label and
        # ignores a preceding StaticText — so as StaticText these are
        # instructions a keyboard user can never reach.
        tg_groups_hint = wx.TextCtrl(
            host,
            value=(
                "To find a group's chat ID: add the bot to the group "
                "in Telegram, then send /whoami@<BotUsername> there. "
                "The bot replies with the negative chat ID."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        tg_groups_hint.SetMinSize((640, 44))
        tg_groups_hint.SetName("How to find a group's chat ID")

        # Message-behaviour knobs (tool-call display, tool-summary footer,
        # progress ping, history cap, reply length cap) live in their own
        # dialog behind this button — see dialogs/telegram_settings.py. The
        # tab keeps the everyday bits: token, the user/group lists, and the
        # run-bot toggle.
        self.tg_message_settings_btn = wx.Button(
            host, label="&Message settings…")
        self.tg_message_settings_btn.Bind(
            wx.EVT_BUTTON, self._on_open_telegram_settings)

        self.tg_enabled_check = wx.CheckBox(host, label="&Run Telegram bot for this kin")
        self.tg_enabled_check.Bind(wx.EVT_CHECKBOX, self._on_tg_enabled_toggle)

        # Read-only TextCtrl, not StaticText: the checkbox above shows what
        # the config asks for, this shows what the bot is actually doing —
        # a bot that failed to start is only visible here.
        self.tg_status_label = wx.TextCtrl(
            host, value="Status: Off",
            style=wx.TE_READONLY | wx.BORDER_NONE)
        self.tg_status_label.SetMinSize((300, -1))
        self.tg_status_label.SetName("Telegram bot status")

        # Read-only TextCtrl, not StaticText: the Users list above needs
        # numeric IDs and this is the only place that says how to get one.
        find_id_help = wx.TextCtrl(
            host,
            value="Tip: users can DM /whoami to the bot to find their ID.",
            style=wx.TE_READONLY | wx.BORDER_NONE,
        )
        find_id_help.SetMinSize((400, -1))
        find_id_help.SetName("How to find a user's ID")

        tg_sizer.Add(tg_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        tg_sizer.Add(token_lbl, flag=wx.LEFT | wx.RIGHT, border=6)
        tg_sizer.Add(token_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(self.tg_test_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(allow_lbl, flag=wx.LEFT | wx.RIGHT, border=6)
        tg_sizer.Add(self.tg_users_list,
                     flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(tg_users_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(tg_groups_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        tg_sizer.Add(self.tg_groups_list,
                     flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(tg_groups_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(tg_groups_hint, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(self.tg_message_settings_btn,
                     flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        tg_sizer.Add(self.tg_enabled_check, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(self.tg_status_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        tg_sizer.Add(find_id_help, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        host.SetSizer(tg_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(tg_panel, "Telegram")

        # ─── Discord tab ─────────────────────────────────────────────
        dc_panel = scrolled.ScrolledPanel(self.notebook)
        dc_sizer = wx.BoxSizer(wx.VERTICAL)
        host = dc_panel

        dc_label = wx.StaticText(host, label="Discord bot:")
        _dcf = dc_label.GetFont(); _dcf.SetWeight(wx.FONTWEIGHT_BOLD)
        dc_label.SetFont(_dcf)

        dc_token_lbl = wx.StaticText(host, label="&Bot token:")
        self.dc_token_field = wx.TextCtrl(
            host, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        self.dc_token_field.Bind(wx.EVT_KILL_FOCUS, self._on_dc_token_changed)
        self.dc_token_field.Bind(wx.EVT_TEXT_ENTER, self._on_dc_token_changed)

        dc_policy_lbl = wx.StaticText(host, label="When to &respond:")
        self.dc_policy_choice = wx.Choice(host, choices=[
            "Only when mentioned (@)",
            "Every message (quiet channels only)",
        ])
        self.dc_policy_choice.Bind(wx.EVT_CHOICE, self._on_dc_policy_changed)

        # Was a plain multi-line box of IDs labelled "empty = anyone", which
        # was untrue in the one direction that matters: empty means NOBODY,
        # deliberately, and the box also silently dropped the "*" that means
        # anyone — so the open-to-everyone setting had no way in from here at
        # all. It also left per-user tool access (which the bot reads) with no
        # screen, so every Discord user sat on 'none' forever. A list plus an
        # editor dialog fixes all three.
        dc_allow_lbl = wx.StaticText(
            host,
            label=("&Who can talk to this kin on Discord (nobody until you "
                   "add someone; tool access set per person):"))
        self.dc_users_list = wx.ListBox(host, style=wx.LB_SINGLE)
        self.dc_users_list.SetMinSize((-1, 140))
        dc_users_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.dc_add_user_btn = wx.Button(host, label="Add &person...")
        self.dc_add_user_btn.Bind(wx.EVT_BUTTON, self._on_dc_add_user)
        self.dc_edit_user_btn = wx.Button(host, label="&Edit person...")
        self.dc_edit_user_btn.Bind(wx.EVT_BUTTON, self._on_dc_edit_user)
        # "&m" belongs to "&Merge Discord history" further down this tab.
        self.dc_remove_user_btn = wx.Button(host, label="Remo&ve person")
        self.dc_remove_user_btn.Bind(wx.EVT_BUTTON, self._on_dc_remove_user)
        dc_users_btn_row.Add(self.dc_add_user_btn, flag=wx.RIGHT, border=6)
        dc_users_btn_row.Add(self.dc_edit_user_btn, flag=wx.RIGHT, border=6)
        dc_users_btn_row.Add(self.dc_remove_user_btn)

        self.dc_share_check = wx.CheckBox(
            host, label="&Merge Discord history into this kin's main "
                        "conversation")
        self.dc_share_check.Bind(wx.EVT_CHECKBOX, self._on_dc_share_toggle)

        # "&r" belongs to "When to &respond" above — this checkbox had the
        # same letter, so Alt+R on this tab reached whichever wx happened to
        # find first.
        self.dc_enabled_check = wx.CheckBox(
            host, label="R&un Discord bot for this kin")
        self.dc_enabled_check.Bind(wx.EVT_CHECKBOX, self._on_dc_enabled_toggle)

        # Read-only TextCtrl, not StaticText: this is the only explanation
        # when ticking "Run Discord bot" silently un-ticks itself for want of
        # a token, and the only readout of whether the bot actually started.
        # A StaticText is never spoken, so that failure is silent.
        self.dc_status_label = wx.TextCtrl(
            host, style=wx.TE_READONLY | wx.BORDER_NONE)
        self.dc_status_label.SetMinSize((420, -1))
        self.dc_status_label.SetName("Discord bot status")

        # Read-only TextCtrl, not StaticText: this is the only place any of
        # the Discord setup is written down — the token, the Message Content
        # Intent, the OAuth2 invite, why to leave the mention-only policy on,
        # and how to find a user ID. As StaticText none of it is ever spoken.
        dc_help = wx.TextCtrl(host, value=(
            "Needs a bot token from the Discord Developer Portal, with the "
            "Message Content Intent turned ON, and the bot invited to your "
            "server via OAuth2. On busy servers keep this on \"Only when "
            "mentioned\" — on local hardware a kin can't keep pace with a "
            "fast channel, so let it speak when spoken to. Nobody can reach "
            "the kin here until you add them above: that is on purpose, "
            "because a bot sits in whatever servers it has been invited to. "
            "Each person you add starts with no tools at all; you choose "
            "what they can reach when you add them. A shell command or a "
            "webcam capture always asks you on THIS desktop, never in the "
            "server."),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP
            | wx.TE_NO_VSCROLL,
        )
        dc_help.SetMinSize((520, 150))
        dc_help.SetName("Discord setup notes")

        # The people list and the token field stretch with the tab; everything
        # else keeps its natural width.
        _wide = (self.dc_token_field, self.dc_users_list)
        for _w in (dc_label, dc_token_lbl, self.dc_token_field, dc_policy_lbl,
                   self.dc_policy_choice, dc_allow_lbl, self.dc_users_list,
                   dc_users_btn_row,
                   self.dc_share_check, self.dc_enabled_check,
                   self.dc_status_label, dc_help):
            _flag = wx.LEFT | wx.RIGHT | wx.TOP
            if any(_w is _x for _x in _wide):
                _flag |= wx.EXPAND
            dc_sizer.Add(_w, flag=_flag, border=6, proportion=0)
        host.SetSizer(dc_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(dc_panel, "Discord")

        # ─── Cron tab ────────────────────────────────────────────────
        cron_panel = scrolled.ScrolledPanel(self.notebook)
        cron_sizer = wx.BoxSizer(wx.VERTICAL)
        host = cron_panel

        cron_header = wx.TextCtrl(
            host,
            value=(
                "Cron / scheduled wake-ups. Each entry fires the kin at "
                "a daily time via Windows Task Scheduler. Add multiple "
                "entries for separate wake-ups (e.g. 8 AM 'what do I "
                "want to do today' plus midnight 'what happened today'). "
                "Test now fires the selected entry immediately, bypassing "
                "the schedule. When Hearthkin is running, wake-ups can "
                "inject into the live conversation if the toggle below "
                "is on; when closed, they run isolated and append to "
                "the kin's conversation, journal, and Telegram."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        cron_header.SetName("Cron section overview")
        cron_header.SetMinSize((-1, 120))

        self.cron_inject_check = wx.CheckBox(
            host,
            label="&Inject cron wake-ups into the active conversation when Hearthkin is open",
        )
        self.cron_inject_check.SetValue(
            bool(self.cfg.get("cron_inject_when_running", True))
        )
        self.cron_inject_check.Bind(
            wx.EVT_CHECKBOX,
            lambda _e: self._save_param(
                "cron_inject_when_running",
                bool(self.cron_inject_check.GetValue()),
            ),
        )

        # A checkbox precedes this and carries its own label, so it has none
        # to lend; SetName() was doing nothing and the list announced as a
        # bare "list box".
        cron_list_label = wx.StaticText(host, label="Scheduled &wake-ups:")
        self.cron_listbox = wx.ListBox(host, choices=[], style=wx.LB_SINGLE)
        self.cron_listbox.SetMinSize((-1, 160))
        # First-letter / first-digit navigation. Each entry's display
        # is "[X] HH:MM — prompt" or "[ ] HH:MM — prompt"; both start
        # with "[", so wxMSW's native LISTBOX keyboard search would
        # never match a useful first character. Intercept EVT_CHAR
        # and match against the underlying time and prompt fields
        # from cfg.cron_entries — same pattern as the model browser
        # search that strips warmth prefixes.
        self._cron_search_buf = ""
        self._cron_search_last = 0.0
        self.cron_listbox.Bind(wx.EVT_CHAR, self._on_cron_list_char)
        self._refresh_cron_listbox()

        cron_add_btn = wx.Button(host, label="&Add entry")
        cron_edit_btn = wx.Button(host, label="E&dit entry")
        cron_remove_btn = wx.Button(host, label="Remo&ve entry")
        # self-attr so the async test flow (M-D2) can disable it while
        # the subprocess runs and re-enable on completion.
        self.cron_test_btn = wx.Button(host, label="Te&st now")
        cron_add_btn.Bind(wx.EVT_BUTTON, self._on_cron_add)
        cron_edit_btn.Bind(wx.EVT_BUTTON, self._on_cron_edit)
        cron_remove_btn.Bind(wx.EVT_BUTTON, self._on_cron_remove)
        self.cron_test_btn.Bind(wx.EVT_BUTTON, self._on_cron_test_now)

        # Proactive heartbeat (the kin reaches out on its own) lives behind its
        # own focused dialog so it doesn't clutter the cron controls. A
        # read-only note below summarises its state for NVDA at a glance.
        self.heartbeat_btn = wx.Button(host, label="Proactive &heartbeat…")
        self.heartbeat_btn.Bind(wx.EVT_BUTTON, self._on_heartbeat_settings)
        self._heartbeat_note = wx.TextCtrl(
            host, style=wx.TE_READONLY | wx.BORDER_NONE)
        self._heartbeat_note.SetName("Heartbeat status")

        cron_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cron_btn_row.Add(cron_add_btn, flag=wx.RIGHT, border=4)
        cron_btn_row.Add(cron_edit_btn, flag=wx.RIGHT, border=4)
        cron_btn_row.Add(cron_remove_btn, flag=wx.RIGHT, border=4)
        cron_btn_row.Add(self.cron_test_btn)

        cron_sizer.Add(cron_header,
                       flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        cron_sizer.Add(self.cron_inject_check,
                       flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        cron_sizer.Add(cron_list_label, flag=wx.LEFT | wx.RIGHT, border=6)
        cron_sizer.Add(self.cron_listbox,
                       flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        cron_sizer.Add(cron_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        cron_sizer.Add(self.heartbeat_btn,
                       flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        cron_sizer.Add(self._heartbeat_note,
                       flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        self._refresh_heartbeat_note()
        host.SetSizer(cron_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(cron_panel, "Cron")

        # ─── Voice tab ───────────────────────────────────────────────
        voice_panel = scrolled.ScrolledPanel(self.notebook)
        voice_sizer = wx.BoxSizer(wx.VERTICAL)
        host = voice_panel

        voice_header = wx.TextCtrl(
            host,
            value=(
                "Voice (TTS via ElevenLabs). When enabled, this kin's "
                "replies are spoken aloud sentence-by-sentence as the "
                "model generates them. Set your ElevenLabs API key under "
                "Tools → Preferences → Connections first; without it "
                "this tab can show voices but can't actually speak."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        voice_header.SetName("Voice section overview")
        voice_header.SetMinSize((-1, 80))

        self.voice_enabled_check = wx.CheckBox(
            host, label="Enable voice for this &kin (speak replies aloud)"
        )
        self.voice_enabled_check.Bind(
            wx.EVT_CHECKBOX, self._on_voice_enabled_toggle,
        )

        # Voice picker. Choice widget (dropdown) populated lazily on
        # first paint and on Refresh. Each entry is a wx.Choice item
        # with a parallel list of voice_ids (Choice's strings show
        # human-readable names; we map back to voice_id on selection).
        voice_pick_lbl = wx.StaticText(host, label="&Voice:")
        self.voice_pick_choice = wx.Choice(host, choices=["(loading…)"])
        self.voice_pick_choice.Disable()
        self.voice_pick_choice.Bind(
            wx.EVT_CHOICE, self._on_voice_pick_changed,
        )
        self._voice_pick_ids = []  # parallel to choice items

        voice_pick_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.voice_refresh_btn = wx.Button(host, label="&Refresh voice list")
        self.voice_refresh_btn.Bind(wx.EVT_BUTTON, self._on_voice_refresh)
        self.voice_preview_btn = wx.Button(host, label="&Preview sample")
        self.voice_preview_btn.Bind(wx.EVT_BUTTON, self._on_voice_preview)
        self.voice_test_btn = wx.Button(host, label="&Test with kin's settings")
        self.voice_test_btn.Bind(wx.EVT_BUTTON, self._on_voice_test)
        voice_pick_btn_row.Add(self.voice_refresh_btn, flag=wx.RIGHT, border=4)
        voice_pick_btn_row.Add(self.voice_preview_btn, flag=wx.RIGHT, border=4)
        voice_pick_btn_row.Add(self.voice_test_btn)

        # Voice tuning sliders (stability, similarity, style, speed) live
        # in their own dialog behind this button — see
        # dialogs/voice_settings.py. Keeps the Voice tab to the everyday
        # bits: enable + the voice picker.
        self.voice_tuning_btn = wx.Button(host, label="Voice &tuning…")
        self.voice_tuning_btn.Bind(wx.EVT_BUTTON, self._on_open_voice_tuning)

        voice_sizer.Add(voice_header,
                        flag=wx.EXPAND | wx.ALL, border=6)
        voice_sizer.Add(self.voice_enabled_check,
                        flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        voice_sizer.Add(voice_pick_lbl,
                        flag=wx.LEFT | wx.RIGHT | wx.TOP, border=6)
        voice_sizer.Add(self.voice_pick_choice,
                        flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=6)
        voice_sizer.Add(voice_pick_btn_row,
                        flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        voice_sizer.Add(self.voice_tuning_btn,
                        flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)

        host.SetSizer(voice_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(voice_panel, "Voice")

        # ─── Prompts tab ─────────────────────────────────────────────
        # Per-kin overrides for the prompts the harness wraps around this
        # kin. Each resolves kin-override -> install-wide shared -> in-code
        # default; this tab edits the kin-override tier for THIS kin only.
        prompts_panel = scrolled.ScrolledPanel(self.notebook)
        prompts_sizer = wx.BoxSizer(wx.VERTICAL)
        host = prompts_panel

        prompts_header = wx.TextCtrl(
            host,
            value=(
                "Per-kin prompts. These are the small instructions Hearthkin "
                "wraps around this kin behind the scenes — how it distils "
                "memory, how it's nudged to use tools, how a scheduled wake-up "
                "is framed, and so on. Each one falls back through three "
                "layers: this kin's own copy, then your install-wide copy "
                "(shared by all kin), then Hearthkin's built-in default. "
                "Editing here makes a copy for THIS kin only; other kin are "
                "untouched. 'Reset' removes this kin's copy so it goes back to "
                "the shared / default wording. A row marked [your copy] has "
                "been customised for this kin; [default] means it's using the "
                "shared or built-in wording."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        prompts_header.SetName("Prompts section overview")
        prompts_header.SetMinSize((-1, 150))

        self._build_prompt_specs()
        # The overview above is a read-only TextCtrl and cannot label this;
        # SetName() does nothing on wxMSW. Without the StaticText the list
        # announced as a bare "list box".
        prompts_list_label = wx.StaticText(host, label="Per-&kin prompts:")
        self.prompts_listbox = wx.ListBox(host, choices=[], style=wx.LB_SINGLE)
        self.prompts_listbox.SetMinSize((-1, 200))
        self.prompts_listbox.Bind(wx.EVT_LISTBOX_DCLICK,
                                  lambda _e: self._on_edit_kin_prompt(None))
        self._refresh_prompts_listbox()

        prompts_edit_btn = wx.Button(host, label="&Edit selected…")
        prompts_reset_btn = wx.Button(host, label="&Reset selected to shared/default")
        prompts_edit_btn.Bind(wx.EVT_BUTTON, self._on_edit_kin_prompt)
        prompts_reset_btn.Bind(wx.EVT_BUTTON, self._on_reset_kin_prompt)

        prompts_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        prompts_btn_row.Add(prompts_edit_btn, flag=wx.RIGHT, border=4)
        prompts_btn_row.Add(prompts_reset_btn)

        prompts_sizer.Add(prompts_header,
                          flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=6)
        prompts_sizer.Add(prompts_list_label,
                          flag=wx.LEFT | wx.RIGHT, border=6)
        prompts_sizer.Add(self.prompts_listbox,
                          flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        prompts_sizer.Add(prompts_btn_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=6)
        host.SetSizer(prompts_sizer)
        host.SetupScrolling(scroll_x=False, scroll_y=True)
        self.notebook.AddPage(prompts_panel, "Prompts")

        # ─── Bottom Close button (outside the notebook) ──────────────
        close_btn = wx.Button(self, wx.ID_CLOSE, label="&Close")
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_btn)
        close_btn.SetDefault()
        # wxPython auto-maps Escape only to wx.ID_CANCEL by default. Our
        # close button uses wx.ID_CLOSE (semantically correct — the dialog
        # auto-saves widget changes so there's no "cancel" to do), so we
        # explicitly tell the dialog Escape should fire ID_CLOSE.
        self.SetEscapeId(wx.ID_CLOSE)

        outer.Add(self.notebook, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        self.SetSizer(outer)

        self.Bind(wx.EVT_CLOSE, self._on_close_evt)

        # Populate values from disk
        self._populate()
        self._loading = False
        self.frame._edit_kin_dialog = self

    # --- Population --- #

    def _populate(self):
        # Soul
        soul = load_soul(self.kin)
        self._suppress_soul_dirty = True
        self.soul_editor.SetValue(soul)
        self._suppress_soul_dirty = False
        self._mark_soul_clean()

        # Memory
        memory_text = load_memory(self.kin)
        self._suppress_memory_dirty = True
        self.memory_editor.SetValue(memory_text)
        self._suppress_memory_dirty = False
        self._mark_memory_clean()

        # Model fields are now read-only displays paired with picker
        # buttons (Change model… / Change distillation model… / Use
        # chat model). No get_models() call here — that used to hit
        # /api/show per Ollama model on cold cache and freeze the UI
        # thread. The ModelBrowserDialog loads its list async when
        # actually opened.
        self._refresh_model_display()
        self._refresh_memory_model_display()

        self.distill_on_close_check.SetValue(bool(self.cfg.get("memory_distill_on_close", True)))

        # Thinking
        # Resolve current think_effort tier (with backward-compat for
        # configs that only have the legacy `think` boolean).
        from kin_persistence import think_effort_of
        cur_effort = think_effort_of(self.cfg)
        for tier, rb in self._think_effort_radios:
            rb.SetValue(tier == cur_effort)
        # (Reasoning detail, caching, provider routing, and Ollama
        # keep-alive/preload load inside MoreModelOptionsDialog when it
        # opens; nothing to set here.)
        self._refresh_machine_display()

        # Per-(kin, scope) counter overview on the Memory tab. The
        # old single-counter shape (just keyed by kin) went away in
        # v0.2.34 when per-surface scopes landed; the dialog now
        # renders the full per-scope breakdown rather than a summed
        # one-liner next to the manual distill buttons.
        self._refresh_chat_counters_display()

        # (Per-turn recall values load inside RecallSettingsDialog when
        # the "Memory recall settings…" button opens it.)

        # Telegram
        tg = self.cfg.get("telegram", {}) or {}
        self.tg_token_field.SetValue(tg.get("bot_token", ""))
        self._refresh_tg_users_list()
        self._refresh_tg_groups_list()
        # (show_tool_calls / show_tool_summary and the other message knobs
        # load inside TelegramMessageSettingsDialog when it opens.)
        self.tg_enabled_check.SetValue(bool(tg.get("enabled", False)))

        # Discord
        dc = self.cfg.get("discord", {}) or {}
        self.dc_token_field.SetValue(dc.get("bot_token", ""))
        self.dc_policy_choice.SetSelection(
            1 if (dc.get("policy") or "mention_only") == "always" else 0)
        self._refresh_dc_users_list()
        self.dc_share_check.SetValue(bool(dc.get("share_desktop", False)))
        self.dc_enabled_check.SetValue(bool(dc.get("enabled", False)))

        existing_bot = self.frame.bots.get(self.kin)
        self.tg_status_label.SetValue(
            f"Status: {existing_bot.status_label() if existing_bot else 'Off'}"
        )

        # Voice
        v_cfg = self.cfg.get("voice") or {}
        self.voice_enabled_check.SetValue(bool(v_cfg.get("enabled", False)))
        # Sliders are populated at construction time from cfg; no
        # re-set here. Voice picker fetches asynchronously to avoid
        # blocking dialog open on a network call.
        self._populate_voice_picker(force_refresh=False)

    # --- Name --- #

    def _on_rename_button(self, event):
        """Defer to the frame's rename flow.

        Closes this dialog first because self.kin and self.cfg both
        reference the OLD name — the moment the rename succeeds, the
        directory is gone and any subsequent Save Soul / Save Memory
        from this dialog would write to a phantom location. The user
        can re-open the dialog after the rename to keep editing.
        """
        # EndModal does NOT route through the EVT_CLOSE handler, so the
        # prompt-to-save flow must run explicitly here — without it,
        # unsaved soul/memory edits were silently discarded on Rename
        # (audit M-D1). Cancel in the prompt aborts the rename and
        # keeps the dialog open.
        if not self._maybe_save_dirty():
            return
        self.EndModal(wx.ID_CANCEL)
        # Defer the actual rename so EndModal's cleanup runs first.
        wx.CallAfter(self.frame._on_rename_agent, None)

    # --- Soul --- #

    def _on_soul_changed(self, event):
        if self._suppress_soul_dirty:
            return
        self._mark_soul_dirty()

    def _mark_soul_dirty(self):
        if not self._soul_dirty:
            self._soul_dirty = True
            self.soul_status.SetLabel("● unsaved")

    def _mark_soul_clean(self):
        self._soul_dirty = False
        self.soul_status.SetLabel("")

    def _on_save_soul(self, event):
        save_soul(self.kin, self.soul_editor.GetValue())
        self._mark_soul_clean()
        # Refresh the frame's soul/memory cache so the live token
        # display picks up the new content immediately. No-op if
        # this kin isn't the currently-loaded one.
        self.frame._invalidate_kin_text_cache(self.kin)
        self.frame._set_status(f"Soul saved for: {self.kin}")

    # --- Memory --- #

    def _on_memory_changed(self, event):
        if self._suppress_memory_dirty:
            return
        self._mark_memory_dirty()

    def _mark_memory_dirty(self):
        self._memory_dirty = True

    def _mark_memory_clean(self):
        self._memory_dirty = False

    def _on_save_memory(self, event):
        save_memory(self.kin, self.memory_editor.GetValue())
        self._mark_memory_clean()
        # Refresh the frame's soul/memory cache — same reason as
        # _on_save_soul above.
        self.frame._invalidate_kin_text_cache(self.kin)
        self.memory_status.SetLabel(f"Memory saved {datetime.datetime.now().strftime('%H:%M')}")

    # --- Chat model & distillation model handlers --- #
    #
    # The chat model and distillation model fields are read-only
    # displays paired with picker buttons. The unified
    # ModelBrowserDialog handles all picking (Ollama + OpenRouter,
    # async loading, search/warmth filtering). The actual swap logic
    # for the chat model (voice-history audit + optional warning
    # dialog) still lives on the frame as _change_kin_model — we
    # call into that here for the audited path. The distillation
    # model is a per-kin config knob with no audit (it doesn't speak
    # to the user; voice continuity isn't a concern).

    def _refresh_model_display(self):
        """Repaint the read-only chat-model display from cfg. Empty
        cfg value (rare — new kin without a model picked) renders as
        a steering hint so the user knows what to do.

        Also kicks the think-capability detection so the reasoning
        radios get greyed/enabled to match the new model. Done here
        rather than in _on_browse_models so it fires on dialog open
        too (where _refresh_model_display is also called)."""
        val = (self.cfg.get("model") or "").strip()
        self.model_display.SetValue(
            val or "(no model set — click Change model…)"
        )
        try:
            self._kick_off_think_capability_refresh()
        except Exception:
            pass

    def _refresh_memory_model_display(self):
        """Repaint the read-only distillation-model display from cfg.
        Empty value means 'inherit from chat model' — the common case —
        so render it as the descriptive sentinel rather than as blank
        whitespace."""
        val = (self.cfg.get("memory_model") or "").strip()
        self.mem_model_display.SetValue(val or "(same as chat model)")

    def _on_browse_models(self, event):
        """Open the unified model browser to pick the chat model. On
        confirm, route through frame._change_kin_model for the
        voice-history audit + optional warning dialog. After a
        committed swap, refresh the display and the context-max
        label."""
        if not self.kin:
            return
        try:
            from model_browser import ModelBrowserDialog
        except ImportError as e:
            wx.MessageBox(
                f"model_browser.py not found: {e}",
                "Error", wx.OK | wx.ICON_ERROR,
            )
            return
        current = (self.cfg.get("model") or "").strip()
        current_host = str(self.cfg.get("ollama_host_name", "") or "")
        dlg = ModelBrowserDialog(
            self.frame, current_model=current,
            ollama_host=current_host, show_machine_picker=True)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                chosen = dlg.get_selected_model()
                if not chosen:
                    return
                # Point the shared Ollama-probe host at the chosen machine
                # BEFORE the model swap below — _change_kin_model runs a
                # compatibility probe and _kick_off_ctx_label_refresh reads
                # the model's context length, both via the global host. If
                # the user moved the kin to another box, those must hit the
                # new box, not the previously-active one.
                if not chosen.startswith("openrouter/"):
                    try:
                        import llm_backend as _lb
                        _lb.set_ollama_host(resolve_kin_ollama_host(
                            dlg.get_selected_ollama_host()))
                    except Exception:
                        pass
                # Model swap first (voice-history audit + warning dialog),
                # then the machine save — _save_param reloads from disk so
                # the two writes don't clobber each other regardless of
                # order, but doing the model first keeps self.cfg fresh.
                if (chosen != current
                        and self.frame.current_agent == self.kin):
                    committed = self.frame._change_kin_model(chosen)
                    if committed:
                        self.cfg = load_agent_config(self.kin)
                        self._refresh_model_display()
                        self._kick_off_ctx_label_refresh()
                # Pin the kin to the chosen machine — only meaningful for an
                # Ollama model, and even when the model name is unchanged
                # (the user may have moved the kin to another box running
                # the same model). OpenRouter models ignore the host.
                if not chosen.startswith("openrouter/"):
                    new_host = dlg.get_selected_ollama_host()
                    if new_host != str(self.cfg.get("ollama_host_name", "") or ""):
                        self._save_param("ollama_host_name", new_host)
                self._refresh_machine_display()
        finally:
            dlg.Destroy()

    def _machine_label_for(self, host_value):
        """Friendly label for a stored ollama_host_name value: 'This
        machine' / a registry name / the raw URL when unmatched."""
        host_value = str(host_value or "").strip()
        if not host_value or host_value == THIS_MACHINE_NAME:
            return "This machine (localhost)"
        for name, url in load_ollama_hosts():
            if url == host_value:
                return f"{name} ({url})"
        return host_value

    def _refresh_machine_display(self):
        """Repaint the read-only 'Runs on' field from the kin's current
        ollama_host_name."""
        self.machine_display.SetValue(
            self._machine_label_for((self.cfg or {}).get("ollama_host_name", "")))

    # ── Per-turn memory recall (opens its own dialog) ──────────────────

    def _on_open_recall_settings(self, _event):
        """Open the per-turn memory-recall settings in their own dialog.
        Button-opens-dialog rather than an inline 'advanced' disclosure
        because NVDA skims past a reveal checkbox; this matches the
        model-browser / search-filter pattern used elsewhere. The dialog
        edits the same recall_* keys through this dialog's _save_param,
        so saves are byte-identical to the old inline path."""
        from dialogs.recall_settings import RecallSettingsDialog
        dlg = RecallSettingsDialog(self, self.cfg, self._save_param,
                                   kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_change_memory_model(self, event):
        """Open the model browser to pick a distillation model.

        No voice-change WARNING here: a summarizer never speaks back,
        so warning about its voice would be noise. That was the old
        reason this path skipped the audit ENTIRELY -- which was
        wrong, and wrong because the audit file used to be called
        voice_history.md. The memory model is the one whose
        provenance matters most: what it writes becomes memory.md,
        and memory outlives the swap away from the model that wrote
        it. So the swap is recorded now; only the warning is skipped."""
        if not self.kin:
            return
        try:
            from model_browser import ModelBrowserDialog
        except ImportError as e:
            wx.MessageBox(
                f"model_browser.py not found: {e}",
                "Error", wx.OK | wx.ICON_ERROR,
            )
            return
        current = (self.cfg.get("memory_model") or "").strip()
        # The memory model runs on the kin's own machine (distillation
        # resolves the same per-kin host), so list that box's models —
        # but don't offer a machine picker here: a kin has one machine,
        # set when picking the chat model.
        dlg = ModelBrowserDialog(
            self.frame, current_model=current,
            ollama_host=str(self.cfg.get("ollama_host_name", "") or ""),
            show_machine_picker=False)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                chosen = dlg.get_selected_model()
                if chosen and chosen != current:
                    self._save_param("memory_model", chosen)
                    try:
                        append_model_history(
                            self.kin,
                            f"memory model changed from "
                            f"`{current or '(same as chat model)'}` "
                            f"to `{chosen}`")
                    except Exception:
                        pass
                    self._refresh_memory_model_display()
                elif chosen:
                    self._refresh_memory_model_display()
        finally:
            dlg.Destroy()

    def _on_reset_memory_model(self, event):
        """Clear the per-kin distillation-model override so memory
        distillation falls back to using the chat model. The cfg
        stores an empty string to mean 'inherit from chat'.

        Recorded like any other model change: falling back to the chat
        model IS a change of who writes this kin's memory."""
        previous = (self.cfg.get("memory_model") or "").strip()
        self._save_param("memory_model", "")
        if previous:
            try:
                append_model_history(
                    self.kin,
                    f"memory model changed from `{previous}` "
                    f"to `(same as chat model)`")
            except Exception:
                pass
        self._refresh_memory_model_display()

    def _on_distill_on_close_toggle(self, event):
        if self._loading:
            return
        self._save_param("memory_distill_on_close", self.distill_on_close_check.GetValue())

    def _refresh_chat_counters_display(self):
        """Paint the per-surface counter overview on the Memory tab.
        Reads frame._messages_since_distill (keyed by (kin, scope) as
        of v0.2.34), enumerates the kin's known scopes from cfg, and
        builds a human-readable summary. Scopes:

          - "desktop": unified counter for desktop chat + any
            Telegram surface with share-with-desktop on.
          - "tg:user:<id>": a non-shared DM with that user.
          - "tg:group:<id>": a non-shared group.
          - "room:<name>": a room this kin is in that has
            "Remember this room" on. Rooms without it have no scope at
            all and aren't listed — nothing about them reaches memory.

        Every scope is shown — including those with count 0 — so the
        user can see at a glance which surfaces exist and where they
        stand. The threshold is the same value (memory_distill_every_n)
        for all scopes, set above on this tab.

        Also refreshes the Cancel-walk button's enabled state so a
        walk that completes naturally between counter refreshes flips
        the button to disabled without requiring an explicit event.
        """
        # Walk-button state: tracks the in-memory walk flags, which
        # are owned by the frame and don't fire a UI event of their own.
        self._refresh_walk_controls()
        counters = getattr(self.frame, "_messages_since_distill", {}) or {}
        every_n = int(self.cfg.get("memory_distill_every_n", 0) or 0)
        at_pct = int(self.cfg.get("memory_distill_at_pct", 0) or 0)
        # Read distill_offsets fresh from disk, not from self.cfg. The
        # bookmark advances on disk during distillation (each chunk's
        # _on_distill_done writes it) but self.cfg is a per-dialog
        # snapshot that doesn't auto-refresh. Without this read, the
        # Memory tab shows the bookmark value from when the dialog last
        # loaded/saved — making a just-completed walk look like it
        # didn't run (1,540 msgs undistilled · 713% even though the
        # bookmark on disk has caught up).
        offsets = {}
        advanced_at = {}
        fresh_cfg = None
        offsets_trusted = True
        try:
            fresh_cfg = load_agent_config(self.kin) or {}
            offsets = fresh_cfg.get("distill_offsets") or {}
            advanced_at = fresh_cfg.get("distill_advanced_at") or {}
        except Exception:
            # Deliberately NOT falling back to self.cfg's snapshot. That
            # showed the bookmark from whenever this dialog opened, with
            # nothing marking it stale — and a confidently wrong number is
            # worse than an honest "couldn't read", because the operator
            # has no second instrument to check it against. Say so instead.
            offsets_trusted = False
        walking = getattr(self.frame, "_walking_from_start", None) or {}
        # Read from the config we just loaded rather than calling
        # _walk_scopes_on_disk — that would re-read config.json once per
        # scope, on a display that repaints on every desktop send.
        paused_scopes = set()
        if isinstance(fresh_cfg, dict):
            paused_scopes = {
                s for s in (fresh_cfg.get("distill_walk_scopes") or [])
                if isinstance(s, str) and s
            } - {sk for (kn, sk) in walking.keys() if kn == self.kin}
        tg = (self.cfg.get("telegram") or {})
        user_labels = (tg.get("user_labels") or {})
        allow_from = (tg.get("allow_from") or [])
        user_share = (tg.get("user_share_desktop") or {})
        groups = (tg.get("groups") or {})
        group_share = (tg.get("group_share_desktop") or {})

        # Build labels (display strings) and a parallel keys list
        # (stable scope_keys). The keys list rides with the ListBox so
        # _on_distill_selected_scope can resolve the highlighted row
        # to the scope it represents.
        labels = []
        keys = []
        seen = set()

        def _add_row(scope_key, label):
            # Pull the scope's on-disk conversation once — used for
            # both the real undistilled-message count (messages past
            # the bookmark) and the live %-of-context estimate (what
            # the pct trigger evaluates against). Cached by the backing
            # file's (mtime, size) — see _convo_for_scope_cached — so
            # the per-send / per-Telegram-tick refresh of this overview
            # doesn't re-parse a multi-MB JSONL when nothing changed.
            convo = self._convo_for_scope_cached(scope_key)
            # A bookmark past the end means the conversation was restarted; it
            # re-reads from 0 so the fresh conversation shows as pending
            # instead of a false "0 undistilled". See live_distill_bookmark.
            stored = offsets.get(scope_key, 0)
            bookmark = live_distill_bookmark(stored, len(convo))
            undistilled = len(convo) - bookmark
            # Feed the pct estimate the SAME fresh config the counts use.
            # It reads distill_offsets out of whatever cfg it's handed, so
            # passing self.cfg — a snapshot from whenever this dialog
            # opened — froze the percentage at its open-time value while
            # the message count beside it moved. Two numbers on one line
            # from two different bookmarks, and the stale one made the
            # honest one look wrong. The real trigger never had this
            # problem: it loads config fresh per check.
            pct = None
            if fresh_cfg is not None:
                try:
                    pct = self.frame._undistilled_context_pct(
                        self.kin, scope_key, convo, fresh_cfg)
                except Exception:
                    pct = None

            parts = distill_progress_parts(
                stored=stored,
                bookmark=bookmark,
                total=len(convo),
                trusted=offsets_trusted,
                walking=bool(walking.get((self.kin, scope_key))),
                paused=(scope_key in paused_scopes),
                advanced_at=advanced_at.get(scope_key),
                pct=pct,
                at_pct=at_pct,
            )
            if every_n > 0:
                session_n = int(counters.get((self.kin, scope_key), 0) or 0)
                if session_n >= every_n:
                    parts.append(f"session counter {session_n}/{every_n} ← at threshold")
                else:
                    parts.append(f"session counter {session_n}/{every_n}")
            labels.append(f"{label}: " + " · ".join(parts))
            keys.append(scope_key)
            seen.add(scope_key)

        _add_row(
            "desktop",
            "Desktop (and any Telegram surfaces with share-with-desktop on)",
        )

        for uid in allow_from:
            sid = str(uid)
            shared = bool(user_share.get(sid) or user_share.get(uid))
            if shared:
                continue
            label = user_labels.get(sid) or user_labels.get(uid) or ""
            display = (f"Telegram DM with {label} ({sid})"
                       if label else f"Telegram DM with {sid}")
            _add_row(f"tg:user:{sid}", display)

        for chat_id, entry in groups.items():
            sid = str(chat_id)
            shared = bool(group_share.get(sid) or group_share.get(chat_id))
            if shared:
                continue
            entry = entry or {}
            label = entry.get("label") or ""
            display = (f'Telegram group "{label}" ({sid})'
                       if label else f"Telegram group {sid}")
            _add_row(f"tg:group:{sid}", display)

        # Rooms this kin is in that have "Remember this room" on. The
        # frame owns the enumeration (it reads each room's config); a
        # room without the flag never appears, because it has no scope.
        try:
            room_scopes = self.frame._room_scopes_for_kin(self.kin)
        except Exception:
            room_scopes = []
        for scope_key in room_scopes:
            _add_row(scope_key, f'Room "{scope_key[len("room:"):]}"')

        # Orphan counters for scopes the config no longer lists (e.g.
        # a user removed from allow_from but with a stale counter) —
        # surface them so the operator can see a silent scope.
        for (kn, sk), n in counters.items():
            if kn != self.kin or sk in seen:
                continue
            labels.append(f"(orphan) {sk}: {int(n or 0)}")
            keys.append(sk)

        if every_n <= 0 and at_pct <= 0:
            header = (
                "Auto-distillation is OFF (both triggers are 0). "
                "The numbers below show what's currently pending on "
                "each surface — message count past the bookmark and "
                "the % of the context window that tail represents. "
                "Nothing fires automatically until you set a trigger "
                "above. The buttons below still work for manual "
                "distillation."
            )
        else:
            triggers = []
            if every_n > 0:
                triggers.append(
                    f"a surface's session counter reaches {every_n} messages")
            if at_pct > 0:
                triggers.append(
                    f"a surface's undistilled tail reaches {at_pct}% of "
                    "the context window")
            header = (
                "Auto-distillation fires when "
                + " — or — ".join(triggers)
                + ". Each row below shows the live state: how many "
                "messages are past the bookmark, what % of context "
                "that is, and how close the row is to firing. To push "
                "a single surface along by hand, highlight it below "
                "and hit Distill selected surface now."
            )
        self.chat_counters_summary.SetValue(header)

        # Repaint the ListBox preserving selection by scope_key when
        # possible. The parallel keys list is what
        # _on_distill_selected_scope reads. `labels` is never empty —
        # the unconditional desktop row above guarantees at least one
        # entry (the old empty-list else-branch here was unreachable).
        prev_idx = self.chat_counters_list.GetSelection()
        prev_key = (self._chat_counter_scope_keys[prev_idx]
                    if 0 <= prev_idx < len(self._chat_counter_scope_keys)
                    else None)
        rebuild_listbox(
            self.chat_counters_list, labels,
            keys=keys, saved_key=prev_key, saved_index=prev_idx,
        )
        self._chat_counter_scope_keys = list(keys)

    def _convo_for_scope_cached(self, scope_key):
        """Per-scope conversation list for the counter overview,
        cached by the backing file's (mtime, size) signature (M-D5).
        The frame refreshes the overview on every desktop send and
        every Telegram activity tick while this dialog is open —
        without the cache each tick re-read and re-parsed the full
        conversation.jsonl per scope (multi-MB for long-running kin)
        on the UI thread.

        Backing files: the desktop scope is conversation.jsonl; a room
        scope is that room's conversation.json (rewritten on every room
        turn); the Telegram scopes read telegram_history.json (the bot
        rewrites that file on every persisted turn, so its mtime tracks
        the in-memory state closely enough for a display cache). A stat
        failure just falls through to a fresh uncached read.

        Each scope family must name its OWN backing file — keying a
        room scope off telegram_history.json would freeze the room's
        counts until an unrelated Telegram message happened to land."""
        if scope_key == "desktop":
            backing = agent_dir(self.kin) / "conversation.jsonl"
        elif scope_key.startswith("room:"):
            backing = room_dir(scope_key[len("room:"):]) / "conversation.json"
        else:
            backing = agent_dir(self.kin) / "telegram_history.json"
        try:
            st = backing.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        cached = self._scope_convo_cache.get(scope_key)
        if cached is not None and sig is not None and cached[0] == sig:
            return cached[1]
        try:
            convo = self.frame._convo_for_distill_scope(self.kin, scope_key)
        except Exception:
            convo = []
        if sig is not None:
            self._scope_convo_cache[scope_key] = (sig, convo)
        return convo

    def _on_edit_distill_prompt(self, event):
        current = load_distill_prompt(self.kin)
        dlg = DistillPromptDialog(self, self.kin, current)
        if dlg.ShowModal() == wx.ID_OK:
            save_distill_prompt(self.kin, dlg.get_prompt())
            self.frame._set_status(f"Distillation prompt saved for {self.kin}.")
            if hasattr(self, "prompts_listbox"):
                self._refresh_prompts_listbox()
        dlg.Destroy()

    # ─── Prompts tab ─────────────────────────────────────────────────
    def _build_prompt_specs(self):
        """Build the list of per-kin-editable prompts. Each spec bundles the
        callables the Prompts tab needs so it can treat every prompt the same
        way regardless of where it's stored. slug is bound as a default arg in
        each lambda to dodge the late-binding closure trap."""
        kin = self.kin
        specs = [
            {
                "title": "Base prompt (framing before soul)",
                "get_effective": lambda: load_base_prompt(kin),
                "get_default": lambda: DEFAULT_BASE_PROMPT,
                "is_overridden": lambda: kin_base_prompt_is_overridden(kin),
                "save": lambda text: save_kin_base_prompt(kin, text),
                "clear": lambda: clear_kin_base_prompt(kin),
            },
            {
                "title": "Memory distillation",
                "get_effective": lambda: load_distill_prompt(kin),
                "get_default": lambda: DEFAULT_DISTILL_PROMPT,
                "is_overridden": lambda: distill_prompt_is_overridden(kin),
                "save": lambda text: save_distill_prompt(kin, text),
                "clear": lambda: clear_distill_prompt(kin),
            },
        ]
        # Registry prompts. Skip ones that aren't meaningfully per-kin:
        # rolling_window_marker is inserted where no kin name is in scope;
        # gesture_messages and reach_messages are detector word-lists, not
        # voice prompts; tool_compaction_marker is context bookkeeping.
        skip = {"rolling_window_marker", "gesture_messages", "reach_messages",
                "tool_compaction_marker"}
        for slug, entry in APP_PROMPT_REGISTRY.items():
            if slug in skip:
                continue
            specs.append({
                "title": entry.get("title", slug),
                "get_effective": (lambda s=slug: load_app_prompt(s, kin)),
                "get_default": (lambda e=entry: e.get("default", "")),
                "is_overridden": (lambda s=slug: kin_app_prompt_is_overridden(kin, s)),
                "save": (lambda text, s=slug: save_kin_app_prompt(kin, s, text)),
                "clear": (lambda s=slug: clear_kin_app_prompt(kin, s)),
            })
        self._prompt_specs = specs

    def _refresh_prompts_listbox(self):
        items = []
        for spec in self._prompt_specs:
            try:
                tag = "[your copy]" if spec["is_overridden"]() else "[default]"
            except Exception:
                tag = ""
            items.append(f"{spec['title']} — {tag}")
        rebuild_listbox(self.prompts_listbox, items)

    def _on_edit_kin_prompt(self, event):
        idx = self.prompts_listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._prompt_specs):
            self.frame._set_status("Select a prompt to edit first.")
            return
        spec = self._prompt_specs[idx]
        try:
            current = spec["get_effective"]() or ""
            default = spec["get_default"]() or ""
        except Exception as e:
            self.frame._set_status(f"Couldn't load that prompt: {e}")
            return
        dlg = AppPromptEditDialog(self, self.kin, spec["title"], current, default)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                spec["save"](dlg.get_prompt())
                self.frame._set_status(
                    f"{spec['title']} saved for {self.kin} (this kin only).")
                self._refresh_prompts_listbox()
            except Exception as e:
                self.frame._set_status(f"Couldn't save that prompt: {e}")
        dlg.Destroy()

    def _on_reset_kin_prompt(self, event):
        idx = self.prompts_listbox.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._prompt_specs):
            self.frame._set_status("Select a prompt to reset first.")
            return
        spec = self._prompt_specs[idx]
        if not spec["is_overridden"]():
            self.frame._set_status(
                f"{spec['title']} is already using the shared / default wording.")
            return
        confirm = wx.MessageBox(
            f"Remove {self.kin}'s own copy of \"{spec['title']}\" and go back "
            "to the shared / built-in wording? This kin's customised version "
            "is backed up first.",
            "Reset prompt to default", wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
        if confirm != wx.YES:
            return
        try:
            spec["clear"]()
            self.frame._set_status(
                f"{spec['title']} reset to shared/default for {self.kin}.")
            self._refresh_prompts_listbox()
        except Exception as e:
            self.frame._set_status(f"Couldn't reset that prompt: {e}")

    def _on_distill_all_surfaces(self, event):
        """Fire distillation across every configured scope for this
        kin that has pending content past its bookmark. The frame
        handles in-flight checks, scope enumeration, per-scope
        bite-capping, and queue sequencing — so a huge surface only
        gets ONE bite per press; hit again to take the next round on
        whichever scopes still have pending content. The per-surface
        button below this one is for pushing a single chosen scope;
        this one drains them all."""
        self.frame._distill_all_scopes(self.kin)

    def _on_distill_selected_scope(self, event):
        """Distill the scope highlighted in the Memory tab's counter
        list. Lets the operator manually push a single surface —
        desktop, a non-shared Telegram DM, or a non-shared group —
        instead of only desktop (which is what the Memory tab's
        'Distill now' button does)."""
        idx = self.chat_counters_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._chat_counter_scope_keys):
            self.memory_status.SetLabel(
                "(pick a surface in the list first)")
            return
        scope_key = self._chat_counter_scope_keys[idx]
        if not scope_key:
            return
        if self.frame._is_distill_in_flight(self.kin):
            self.memory_status.SetLabel("(already distilling — wait)")
            return
        convo = self.frame._convo_for_distill_scope(self.kin, scope_key)
        if not convo:
            self.memory_status.SetLabel(
                f"(nothing to distill on {scope_key} — "
                "no conversation yet)")
            return
        self.frame._kick_off_distillation(
            self.kin, convo,
            source_label=f"manual-{scope_key}", scope_key=scope_key)

    def _on_catch_up(self, event):
        """Chain forward from the bookmark until this surface is caught up,
        unattended — "Redistill from start" without the "from start".

        The bookmark is NOT touched, which is the entire difference. See
        MemoryMixin._start_catchup for why this exists at all: nothing else
        could grind a long backlog without either re-billing work already
        done or asking for a button press per bite.

        Confirms first, with the estimate reckoned on the messages actually
        REMAINING rather than the whole conversation — quoting the full
        length here would price a job this doesn't do.
        """
        idx = self.chat_counters_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._chat_counter_scope_keys):
            self.memory_status.SetLabel("(pick a surface in the list first)")
            return
        scope_key = self._chat_counter_scope_keys[idx]
        if not scope_key:
            return
        if self.frame._is_distill_in_flight(self.kin):
            self.memory_status.SetLabel("(already distilling — wait)")
            return
        walking = getattr(self.frame, "_walking_from_start", {}) or {}
        if any(k == self.kin for (k, _sk) in walking.keys()):
            self.memory_status.SetLabel(
                f"(something is already chaining for {self.kin} — wait for "
                f"it, or press Cancel distilling)")
            return
        convo = self.frame._convo_for_distill_scope(self.kin, scope_key)
        if not convo:
            self.memory_status.SetLabel(
                f"(nothing on {scope_key} — no conversation)")
            return
        try:
            _off = (load_agent_config(self.kin) or {}).get(
                "distill_offsets") or {}
            done = _off.get(scope_key)
            done = done if isinstance(done, int) and done > 0 else 0
        except Exception:
            done = 0
        remaining = max(0, len(convo) - done)
        if remaining <= 0:
            self.memory_status.SetLabel(
                f"({scope_key} is already caught up — nothing to do)")
            return

        est_chunks = max(1, (remaining + 74) // 75)
        memory_model = (self.cfg.get("memory_model") or "").strip()
        if not memory_model:
            try:
                from model_utils import strip_model_annotation
                memory_model = strip_model_annotation(
                    self.cfg.get("model", "") or "")
            except Exception:
                memory_model = self.cfg.get("model", "") or ""
        model_short = (memory_model.split("/")[-1]
                       if "/" in memory_model else memory_model)
        try:
            from llm_backend import _estimate_call_cost
            per_call_est = _estimate_call_cost(memory_model, 10000, 500) or 0.0
        except Exception:
            per_call_est = 0.0
        total_est = per_call_est * est_chunks
        if per_call_est > 0:
            cost_line = (
                f"Estimated {est_chunks} distillation calls on {model_short} "
                f"— about ${total_est:.2f} total (~${per_call_est:.3f} each)."
            )
        else:
            cost_line = (
                f"Estimated {est_chunks} distillation calls on {model_short} "
                f"(local model — no per-call charge)."
            )
        if wx.MessageBox(
            f"Catch {self.kin} up on {scope_key}?\n\n"
            f"{remaining:,} messages have not reached memory yet. This works "
            f"forward from where it left off — nothing already distilled is "
            f"done again.\n\n"
            f"{cost_line}\n\n"
            f"It keeps going by itself until the surface is caught up. You "
            f"can close this window; it carries on, and it resumes if you "
            f"quit Hearthkin part-way. Press 'Cancel distilling' to stop it "
            f"— progress made so far is kept.",
            "Catch up on this surface?",
            wx.YES_NO | wx.ICON_QUESTION, self
        ) != wx.YES:
            return

        self.frame._start_catchup(self.kin, scope_key, convo)
        self.memory_status.SetLabel(
            f"Catching {scope_key} up — {remaining:,} messages to go. The "
            f"next chunk fires by itself each time one finishes. You can "
            f"close this window; it keeps going.")
        self._refresh_walk_controls()

    def _on_redistill_from_start(self, event):
        """Reset the selected scope's distill bookmark to 0 and walk
        through the whole conversation in budget-bounded chunks. The
        first chunk is kicked off here; the frame's _on_distill_done
        auto-chains the rest by checking _walking_from_start[(kin,
        scope)] and re-firing as long as the bookmark hasn't reached
        the conversation's end.

        Confirms first with an estimate of chunks + cost on the
        selected memory model — a long conversation on a paid model
        can run into real money, so we don't fire this silently."""
        idx = self.chat_counters_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._chat_counter_scope_keys):
            self.memory_status.SetLabel(
                "(pick a surface in the list first)")
            return
        scope_key = self._chat_counter_scope_keys[idx]
        if not scope_key:
            return
        if self.frame._is_distill_in_flight(self.kin):
            self.memory_status.SetLabel("(already distilling — wait)")
            return
        # Refuse if another walk is in progress on this kin — the
        # auto-chain works one scope at a time per kin (single
        # in-flight gate).
        walking = getattr(self.frame, "_walking_from_start", {}) or {}
        if any(k == self.kin for (k, _sk) in walking.keys()):
            self.memory_status.SetLabel(
                "(another walk-from-start is already running for "
                f"{self.kin} — wait for it to finish)")
            return
        convo = self.frame._convo_for_distill_scope(self.kin, scope_key)
        if not convo:
            self.memory_status.SetLabel(
                f"(nothing on {scope_key} — no conversation)")
            return

        n_msgs = len(convo)
        # Rough estimate: each chunk averages ~75 messages depending on
        # content density. Cost calc uses the memory model's per-call
        # estimate (see _estimate_call_cost in llm_backend) at the
        # typical ~10k in / ~500 out shape we've observed for distills.
        # Doesn't have to be precise — it just has to be enough to
        # avoid surprising the operator with a $5 bill.
        est_chunks = max(1, (n_msgs + 74) // 75)
        memory_model = (self.cfg.get("memory_model") or "").strip()
        if not memory_model:
            try:
                from model_utils import strip_model_annotation
                memory_model = strip_model_annotation(
                    self.cfg.get("model", "") or "")
            except Exception:
                memory_model = self.cfg.get("model", "") or ""
        model_short = (memory_model.split("/")[-1]
                       if "/" in memory_model else memory_model)
        try:
            from llm_backend import _estimate_call_cost
            per_call_est = _estimate_call_cost(memory_model, 10000, 500) or 0.0
        except Exception:
            per_call_est = 0.0
        total_est = per_call_est * est_chunks
        if per_call_est > 0:
            cost_line = (
                f"Estimated {est_chunks} distillation calls on "
                f"{model_short} — about ${total_est:.2f} total "
                f"(~${per_call_est:.3f} per call)."
            )
        else:
            cost_line = (
                f"Estimated {est_chunks} distillation calls on "
                f"{model_short} (local model — no per-call charge)."
            )

        # Index order matches the walk_pacing_choice list built in
        # __init__: Unattended / day / hour / chunk. The raw strings are
        # the actual on-disk contract MemoryMixin._WALK_PACINGS checks
        # against, not just UI labels.
        pacing = ["unattended", "day", "hour",
                 "chunk"][self.walk_pacing_choice.GetSelection()]
        if pacing == "unattended":
            pacing_note = (
                "After each chunk completes, the next one fires "
                "automatically all the way through — you don't have to "
                "click again."
            )
        elif pacing == "chunk":
            pacing_note = (
                "It stops after every single chunk and waits — press "
                "'Continue redistilling' for each next one."
            )
        else:
            pacing_note = (
                f"It keeps chunking through a {pacing} (a big one may take "
                f"several chunks) but stops the moment the next chunk "
                f"would cross into the next {pacing} — press 'Continue "
                f"redistilling' for the next one."
            )

        msg = (
            f"Redistill '{scope_key}' from the start?\n\n"
            f"This resets the distillation bookmark to 0 and walks "
            f"through all {n_msgs:,} messages in chunks, firing "
            f"distillation on each chunk and advancing the bookmark "
            f"after. {pacing_note}\n\n"
            f"{cost_line}\n\n"
            f"Your memory.md is not touched by this — distillation "
            f"writes notes to staging for the kin to review during "
            f"tending. Press OK to start."
        )
        result = wx.MessageBox(
            msg, "Redistill from start?",
            wx.OK | wx.CANCEL | wx.ICON_QUESTION,
            self,
        )
        if result != wx.OK:
            return

        # Remember where the bookmark stood BEFORE the rewind, so Cancel
        # can put it back. Rewinding is what makes a cancelled redistill
        # unstoppable otherwise: it leaves the kin with tens of thousands
        # of undistilled messages, which is far past
        # memory_distill_at_pct, so the ordinary auto-distill trigger
        # picks the same work straight back up — and Cancel has no reach
        # over that, because it isn't a walk. Recorded before the reset
        # and on its own, so a crash between the two loses nothing.
        #
        # Only meaningful for UNATTENDED pacing — see _on_cancel_walk:
        # a paced walk (day/hour/chunk) never restores this on Cancel,
        # since every unit it completed only happened because the
        # operator explicitly continued into it. Recorded here
        # regardless anyway, for symmetry; it's simply never read back
        # for a paced walk's Cancel.
        cfg = load_agent_config(self.kin) or {}
        offsets = dict(cfg.get("distill_offsets") or {})
        _prior = offsets.get(scope_key)
        self.frame._persist_walk_prior(
            self.kin, scope_key, _prior if isinstance(_prior, int) else 0)

        # Reset the bookmark to 0 for this scope. Persist immediately
        # so a Hearthkin crash mid-walk doesn't leave a stale advanced
        # bookmark on disk that contradicts the walk we're about to
        # start.
        cfg = load_agent_config(self.kin) or {}
        offsets = dict(cfg.get("distill_offsets") or {})
        offsets[scope_key] = 0
        cfg["distill_offsets"] = offsets
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

        # Flag the walk on the frame so _on_distill_done auto-chains —
        # in memory AND on disk, so quitting part-way pauses it rather
        # than ending it. Pacing rides along so a quit-and-relaunch
        # resume, or a later Cancel, both see the same choice made here.
        self.frame._start_walk(self.kin, scope_key, pacing=pacing)

        # Kick off the first bite. Subsequent bites are queued by
        # _on_distill_done while the walk flag is set for this scope.
        self.frame._kick_off_distillation(
            self.kin, convo,
            source_label=f"walk-from-start-{scope_key}",
            scope_key=scope_key)
        self.memory_status.SetLabel(
            f"Redistilling {scope_key} from the start — the next chunk "
            f"fires by itself each time one finishes. You can close "
            f"this window; it keeps going.")
        self._refresh_walk_controls()

    def _on_resume_walk(self, event):
        """Continue an unfinished redistill from where it stopped.

        The bookmark is NOT reset — that's the entire difference between
        this and the button above it, and the reason this one exists.
        """
        scopes = self.frame._walk_scopes_on_disk(self.kin)
        if not scopes:
            self.memory_status.SetLabel(
                "(nothing to continue — no unfinished redistill for "
                f"{self.kin})")
            return
        if self.frame._is_distill_in_flight(self.kin):
            self.memory_status.SetLabel("(already distilling — wait)")
            return
        walking = getattr(self.frame, "_walking_from_start", None) or {}
        if any(k == self.kin for (k, _sk) in walking.keys()):
            self.memory_status.SetLabel(
                f"(a redistill is already running for {self.kin})")
            return
        started = []
        for scope_key in scopes:
            convo = self.frame._convo_for_distill_scope(self.kin, scope_key)
            offsets = (load_agent_config(self.kin) or {}).get(
                "distill_offsets") or {}
            bm = live_distill_bookmark(offsets.get(scope_key, 0), len(convo))
            if not convo or bm >= len(convo):
                # Already finished — clear the stale record so the
                # button stops offering work that isn't there.
                self.frame._persist_walk(self.kin, scope_key, False)
                continue
            self.frame._start_walk(self.kin, scope_key)
            started.append((scope_key, len(convo) - bm))
            break   # one walk per kin at a time; the rest stay recorded
        if not started:
            self.memory_status.SetLabel(
                "(that redistill had already finished — nothing left "
                "to do)")
            self._refresh_walk_controls()
            return
        scope_key, remaining = started[0]
        self.memory_status.SetLabel(
            f"Continuing {scope_key} from message {len(convo) - remaining:,} "
            f"— {remaining:,} to go. You can close this window.")
        self.frame._walk_next_chunk(self.kin, scope_key)
        self._refresh_walk_controls()

    def _on_cancel_walk(self, event):
        """Stop whatever distillation is running or queued for this kin
        — a "Redistill selected from start" walk, a "Distill all
        surfaces now" queue draining through several surfaces, or (with
        nothing further to actually stop) a plain one-shot "Distill
        selected surface now". Reported live: an accidental press of
        "Distill all surfaces now" had no way to be stopped short of
        quitting Hearthkin, because this button only ever looked at
        walk state.

        In every case, the currently-running bite (if any) finishes
        normally — nothing in this app kills a model call already
        generating, see CLAUDE.md, "The stop button" — this only ever
        prevents whatever would have come AFTER it.

        Cancels EVERY walk owned by this kin — the redistill-from-start
        guard already enforces one walk per kin at a time, so in
        practice this is one entry, but the loop is safer if that
        invariant ever weakens."""
        walking = getattr(self.frame, "_walking_from_start", None) or {}
        cancelled = []
        for key in list(walking.keys()):
            if key[0] == self.kin:
                # keep_on_disk=False: cancelling is a decision, not an
                # interruption. It must not leave a record that the next
                # launch would silently resume.
                self.frame._end_walk(self.kin, key[1])
                cancelled.append(key[1])  # scope_key
        # Also clear any PAUSED redistill recorded on disk — otherwise
        # Cancel would appear to do nothing when the walk had already
        # stopped on an error, and the next launch would start it again.
        for scope_key in self.frame._walk_scopes_on_disk(self.kin):
            self.frame._persist_walk(self.kin, scope_key, False)
            if scope_key not in cancelled:
                cancelled.append(scope_key)
        # Undo the rewind — but ONLY for an UNATTENDED walk. Stopping the
        # chain isn't enough on its own there: a bookmark left near zero
        # puts the kin miles past memory_distill_at_pct, and the ordinary
        # auto-distill trigger then grinds through the very same history,
        # from the very same place, with no button that reaches it.
        # Cancel has to mean the kin goes back to how it was, not just
        # that this particular mechanism let go of it.
        #
        # A PACED walk (day/hour/chunk) is different in kind: every unit
        # it completed only happened because the operator explicitly
        # continued into it, one at a time. Rewinding that on Cancel
        # would throw away real, deliberately-approved progress — the
        # exact thing pacing exists to let someone keep. So Cancel on a
        # paced walk just stops it, leaving the bookmark exactly where
        # it sits.
        restored = []
        kept = []
        for scope_key in cancelled:
            pacing = self.frame._walk_pacing_on_disk(self.kin, scope_key)
            if pacing != "unattended":
                kept.append(scope_key)
                continue
            back_to = self.frame._restore_walk_bookmark(self.kin, scope_key)
            if back_to is not None:
                restored.append(f"{scope_key} to message {back_to:,}")
        # "Distill all surfaces now" queues the remaining scopes in
        # _distill_queue and drains them one at a time from
        # _on_distill_done as each finishes (see _drain_distill_queue).
        # Clearing it here is the same shape as ending a walk: the
        # scope currently running finishes normally, nothing further in
        # the queue gets a turn.
        queued_scopes = []
        try:
            queue = getattr(self.frame, "_distill_queue", None) or {}
            queued_scopes = list(queue.get(self.kin) or [])
            queue.pop(self.kin, None)
        except Exception:
            pass

        in_flight = False
        try:
            in_flight = self.frame._is_distill_in_flight(self.kin)
        except Exception:
            pass

        if cancelled or queued_scopes:
            parts = []
            spoken_extra = ""
            if cancelled:
                scopes = ", ".join(cancelled)
                parts.append(f"Redistill cancelled ({scopes}).")
                if restored:
                    parts.append(
                        " The bookmark went back to where it was before "
                        f"({'; '.join(restored)}), so nothing else starts "
                        f"re-distilling on its own.")
                    spoken_extra += " Bookmark restored."
                if kept:
                    parts.append(
                        f" Progress on {', '.join(kept)} is kept — "
                        f"nothing already distilled gets re-done.")
                    spoken_extra += " Progress kept."
            if queued_scopes:
                remaining = ", ".join(queued_scopes)
                parts.append(
                    f" The 'distill all surfaces' queue is cleared — "
                    f"{remaining} will not run.")
                spoken_extra += f" {remaining} won't run."
            parts.append(
                " The current chunk (if any) will finish, but nothing "
                "further will fire.")
            self.memory_status.SetLabel("".join(parts))
            try:
                from audio import nvda_speak
                nvda_speak(f"Distilling cancelled.{spoken_extra}")
            except Exception:
                pass
        elif in_flight:
            # Nothing to actually cancel — a plain "Distill selected
            # surface now" is a genuine one-shot with no chain and no
            # queue behind it. Say so plainly rather than the old
            # misleading "(no walk in progress)", which was simply
            # false here: something really is running.
            self.memory_status.SetLabel(
                "A distillation is running for you — nothing is queued "
                "behind it, so it'll finish on its own with nothing "
                "further to cancel.")
        else:
            self.memory_status.SetLabel(
                "(nothing is distilling for this kin right now)")
        self._refresh_walk_controls()

    def _refresh_walk_controls(self):
        """Update the Continue / Cancel buttons for this kin.

        Continue is walk-specific — only a paused walk has a bookmark to
        pick back up from. Cancel is broader: it's enabled whenever
        THERE IS ANYTHING FOR IT TO DO, which is any of three states —
        which matters because the counter list looks identical in all
        of them:

          walking     — a redistill-from-start chain is live.
          paused      — nothing running, but an unfinished redistill is
                        recorded on disk (a chunk errored, or the app
                        was closed).
          plain busy  — a "Distill all surfaces now" queue is draining,
                        or a one-shot "Distill selected surface now" is
                        running. Neither is a walk, but Cancel now
                        reaches both — see _on_cancel_walk.

        Called after starting, cancelling or resuming a redistill, after
        each chunk completes, and from _refresh_chat_counters_display so
        a periodic refresh picks up a redistill that ended on its own.
        """
        walking = getattr(self.frame, "_walking_from_start", None) or {}
        any_walking = any(k[0] == self.kin for k in walking.keys())
        try:
            paused = bool(self.frame._walk_scopes_on_disk(self.kin))
        except Exception:
            paused = False
        try:
            in_flight = self.frame._is_distill_in_flight(self.kin)
        except Exception:
            in_flight = False
        try:
            queue = getattr(self.frame, "_distill_queue", None) or {}
            queued = bool(queue.get(self.kin))
        except Exception:
            queued = False
        try:
            self.cancel_walk_btn.Enable(
                bool(any_walking or paused or in_flight or queued))
            # Enable/disable only — deliberately NOT Show/Hide. This
            # lives on a wx.Notebook page, and Show() on a child of a
            # page that isn't the active one is the case wxWidgets #4343
            # makes unreliable; _refresh_chat_counters_display calls
            # through here while other tabs are up.
            self.resume_walk_btn.Enable(bool(paused and not any_walking))
            # Catch up is offered whenever nothing is already chaining or
            # running for this kin. Not gated on `paused` — unlike Continue,
            # it needs no prior redistill to pick up from; that is the whole
            # point of it.
            self.catchup_btn.Enable(
                not (any_walking or in_flight or queued))
        except (AttributeError, RuntimeError):
            # Dialog being destroyed or button not yet built — safe to skip.
            pass

    def _on_consolidate_now(self, event):
        if self.frame._is_distill_in_flight(self.kin):
            self.memory_status.SetLabel("(busy — wait)")
            return
        self.frame._kick_off_consolidation(self.kin, source_label="manual")

    # --- Telegram --- #

    def _on_tg_token_changed(self, event):
        # Fired on focus-loss and Enter (not per keystroke — L-B43).
        # Skip first so the kill-focus event keeps propagating.
        event.Skip()
        if self._loading:
            return
        token = self.tg_token_field.GetValue()
        if token == (self.cfg.get("telegram") or {}).get("bot_token", ""):
            return  # unchanged — don't rewrite config on every blur
        self._save_telegram_param("bot_token", token)

    # --- Telegram user list handlers --- #
    #
    # The on-disk shape uses three parallel dicts:
    #   allow_from:   [<user_id>, ...]            who can chat at all
    #   user_tools:   {"<user_id>": "bucket"}    per-user tool access
    #   user_labels:  {"<user_id>": "<name>"}    name kin sees for this user
    # Each handler reads cfg, mutates, writes back via _save_telegram_param.
    # _refresh_tg_users_list rebuilds the wx.ListBox from the current cfg
    # shape so the UI mirrors disk after any save.

    def _refresh_tg_users_list(self):
        # Capture previously-selected user_id BEFORE we mutate the
        # parallel _tg_user_ids_in_order list, so rebuild_listbox can
        # restore the selection by stable key (user_id) — without this
        # NVDA's focus drops back to the top of the list after every
        # add / edit / remove.
        _prev_idx = self.tg_users_list.GetSelection()
        _prev_uid = (self._tg_user_ids_in_order[_prev_idx]
                     if 0 <= _prev_idx < len(getattr(self, "_tg_user_ids_in_order", []))
                     else None)

        tg = (self.cfg.get("telegram") or {})
        allow = tg.get("allow_from") or []
        labels = tg.get("user_labels") or {}
        tools = tg.get("user_tools") or {}
        share = tg.get("user_share_desktop") or {}
        mirror = tg.get("user_mirror_to_telegram") or {}
        items = []
        self._tg_user_ids_in_order = []  # parallel list so we can look up by selection index
        for raw_id in allow:
            sid = str(raw_id)
            self._tg_user_ids_in_order.append(sid)
            label = labels.get(sid) or ""
            bucket = tools.get(sid) or "none"
            label_part = f" [{label}]" if label else ""
            extras = []
            if share.get(sid):
                extras.append("shared")
            if mirror.get(sid):
                extras.append("mirrored")
            extras_part = f" · {', '.join(extras)}" if extras else ""
            items.append(f"{sid}{label_part} — tools: {bucket}{extras_part}")
        rebuild_listbox(
            self.tg_users_list, items,
            keys=self._tg_user_ids_in_order,
            saved_key=_prev_uid,
            saved_index=_prev_idx,
        )

    def _on_tg_add_user(self, event):
        dlg = _TelegramUserDialog(self, user_id="", label="", bucket="none",
                                  share_desktop=False, mirror_to_telegram=False,
                                  webcam_permission="ask")
        if dlg.ShowModal() == wx.ID_OK:
            (uid, label, bucket, share_desktop,
             mirror_to_telegram, webcam_perm) = dlg.get_values()
            if not uid:
                wx.MessageBox("User ID can't be empty.", "Add user",
                              wx.OK | wx.ICON_WARNING)
            else:
                tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
                allow = list(tg.get("allow_from") or [])
                labels = dict(tg.get("user_labels") or {})
                tools = dict(tg.get("user_tools") or {})
                share = dict(tg.get("user_share_desktop") or {})
                mirror = dict(tg.get("user_mirror_to_telegram") or {})
                webcam = dict(tg.get("user_webcam_permission") or {})
                if uid not in [str(x) for x in allow]:
                    allow.append(uid)
                labels[uid] = label
                tools[uid] = bucket
                share[uid] = share_desktop
                mirror[uid] = mirror_to_telegram
                webcam[uid] = webcam_perm
                tg["allow_from"] = allow
                tg["user_labels"] = labels
                tg["user_tools"] = tools
                tg["user_share_desktop"] = share
                tg["user_mirror_to_telegram"] = mirror
                tg["user_webcam_permission"] = webcam
                self._commit_telegram_dict(tg)
                self._refresh_tg_users_list()
                # Re-adding a user who was removed earlier? They might
                # still have an orphaned slice in telegram_history.json.
                # If they're now share=True, offer to migrate it in.
                if share_desktop:
                    self._maybe_offer_telegram_migration(uid)
        dlg.Destroy()

    def _on_tg_edit_user(self, event):
        idx = self.tg_users_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_tg_user_ids_in_order", [])):
            wx.MessageBox("Pick a user from the list first.", "Edit user",
                          wx.OK | wx.ICON_INFORMATION)
            return
        uid_old = self._tg_user_ids_in_order[idx]
        tg = (self.cfg.get("telegram") or {})
        labels = tg.get("user_labels") or {}
        tools = tg.get("user_tools") or {}
        share = tg.get("user_share_desktop") or {}
        mirror = tg.get("user_mirror_to_telegram") or {}
        webcam = tg.get("user_webcam_permission") or {}
        dlg = _TelegramUserDialog(
            self,
            user_id=uid_old,
            label=labels.get(uid_old, ""),
            bucket=tools.get(uid_old, "none"),
            share_desktop=bool(share.get(uid_old, False)),
            mirror_to_telegram=bool(mirror.get(uid_old, False)),
            webcam_permission=str(webcam.get(uid_old, "ask")),
        )
        if dlg.ShowModal() == wx.ID_OK:
            (uid_new, label_new, bucket_new, share_new,
             mirror_new, webcam_new) = dlg.get_values()
            share_old = bool((tg.get("user_share_desktop") or {}).get(uid_old, False))
            if not uid_new:
                wx.MessageBox("User ID can't be empty.", "Edit user",
                              wx.OK | wx.ICON_WARNING)
            else:
                tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
                allow = [str(x) for x in (tg.get("allow_from") or [])]
                labels = dict(tg.get("user_labels") or {})
                tools = dict(tg.get("user_tools") or {})
                share = dict(tg.get("user_share_desktop") or {})
                mirror = dict(tg.get("user_mirror_to_telegram") or {})
                webcam = dict(tg.get("user_webcam_permission") or {})
                # If the user_id changed, drop the old entries and add new.
                if uid_new != uid_old:
                    allow = [a for a in allow if a != uid_old]
                    labels.pop(uid_old, None)
                    tools.pop(uid_old, None)
                    share.pop(uid_old, None)
                    mirror.pop(uid_old, None)
                    webcam.pop(uid_old, None)
                if uid_new not in allow:
                    allow.append(uid_new)
                labels[uid_new] = label_new
                tools[uid_new] = bucket_new
                share[uid_new] = share_new
                mirror[uid_new] = mirror_new
                webcam[uid_new] = webcam_new
                tg["allow_from"] = allow
                tg["user_labels"] = labels
                tg["user_tools"] = tools
                tg["user_share_desktop"] = share
                tg["user_mirror_to_telegram"] = mirror
                tg["user_webcam_permission"] = webcam
                self._commit_telegram_dict(tg)
                self._refresh_tg_users_list()
                # Toggling share from off to on for an existing user
                # (or for a re-added user with the same id) — offer to
                # migrate any orphaned per-user history into the kin's
                # main conversation. Otherwise the kin would lose all
                # context from the prior Telegram chat the moment
                # sharing turns on.
                if share_new and not share_old:
                    self._maybe_offer_telegram_migration(uid_new)
        dlg.Destroy()

    def _on_tg_remove_user(self, event):
        idx = self.tg_users_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_tg_user_ids_in_order", [])):
            wx.MessageBox("Pick a user from the list first.", "Remove user",
                          wx.OK | wx.ICON_INFORMATION)
            return
        uid = self._tg_user_ids_in_order[idx]
        confirm = wx.MessageBox(
            f"Remove Telegram user {uid}? They won't be able to chat with "
            f"this kin anymore.",
            "Remove user", wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
        allow = [a for a in (tg.get("allow_from") or []) if str(a) != uid]
        labels = dict(tg.get("user_labels") or {})
        labels.pop(uid, None)
        tools = dict(tg.get("user_tools") or {})
        tools.pop(uid, None)
        share = dict(tg.get("user_share_desktop") or {})
        share.pop(uid, None)
        mirror = dict(tg.get("user_mirror_to_telegram") or {})
        mirror.pop(uid, None)
        webcam = dict(tg.get("user_webcam_permission") or {})
        webcam.pop(uid, None)
        tg["allow_from"] = allow
        tg["user_labels"] = labels
        tg["user_tools"] = tools
        tg["user_share_desktop"] = share
        tg["user_mirror_to_telegram"] = mirror
        tg["user_webcam_permission"] = webcam
        self._commit_telegram_dict(tg)
        self._refresh_tg_users_list()

    # --- Telegram group list handlers --- #
    #
    # Per-group opt-in. Adding an entry to cfg.telegram.groups is what
    # actually lets the bot converse in that chat — without it, the
    # bot only answers /whoami in any group (for chat-ID discovery)
    # and stays silent on normal messages. The on-disk shape:
    #   groups: {"<chat_id>": {"label": "...", "policy": "mention_only"}}
    # Chat IDs are stringified for JSON compatibility. Group chat IDs
    # from Telegram are negative; the ID field accepts the minus sign.

    def _refresh_tg_groups_list(self):
        # Capture before mutating the parallel id list — same pattern as
        # _refresh_tg_users_list above.
        _prev_idx = self.tg_groups_list.GetSelection()
        _prev_gid = (self._tg_group_ids_in_order[_prev_idx]
                     if 0 <= _prev_idx < len(getattr(self, "_tg_group_ids_in_order", []))
                     else None)

        tg = (self.cfg.get("telegram") or {})
        groups = tg.get("groups") or {}
        items = []
        self._tg_group_ids_in_order = []
        for chat_id, entry in groups.items():
            sid = str(chat_id)
            self._tg_group_ids_in_order.append(sid)
            entry = entry or {}
            label = entry.get("label") or ""
            policy = entry.get("policy") or "mention_only"
            label_part = f" [{label}]" if label else ""
            n_muted = len(entry.get("exclude") or [])
            muted_part = f" — {n_muted} muted" if n_muted else ""
            items.append(f"{sid}{label_part} — policy: {policy}{muted_part}")
        rebuild_listbox(
            self.tg_groups_list, items,
            keys=self._tg_group_ids_in_order,
            saved_key=_prev_gid,
            saved_index=_prev_idx,
        )

    def _seen_group_members(self, chat_id):
        """Names+IDs of everyone the kin has heard in this group, for the
        mute picker. Reads from disk, so it works whether or not the bot is
        running. Empty on any error — the dialog falls back to Add-by-ID."""
        if not chat_id:
            return []
        try:
            from telegram_bot import seen_group_members
            return seen_group_members(self.kin, chat_id)
        except Exception:
            return []

    def _on_tg_add_group(self, event):
        dlg = _TelegramGroupDialog(self, chat_id="", label="",
                                   policy="mention_only",
                                   share_desktop=False, exclude=[],
                                   seen_members=[])
        if dlg.ShowModal() == wx.ID_OK:
            chat_id, label, policy, share_desktop, exclude = dlg.get_values()
            if not chat_id:
                wx.MessageBox("Chat ID can't be empty.", "Add group",
                              wx.OK | wx.ICON_WARNING)
            elif not chat_id.startswith("-"):
                wx.MessageBox(
                    "Group chat IDs are negative — Telegram uses the "
                    "sign to distinguish them from user IDs. "
                    "Re-enter with a leading minus sign.",
                    "Add group", wx.OK | wx.ICON_WARNING,
                )
            else:
                tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
                groups = dict(tg.get("groups") or {})
                groups[chat_id] = {"label": label, "policy": policy,
                                   "exclude": exclude}
                tg["groups"] = groups
                share_map = dict(tg.get("group_share_desktop") or {})
                share_map[chat_id] = share_desktop
                tg["group_share_desktop"] = share_map
                self._commit_telegram_dict(tg)
                self._refresh_tg_groups_list()
                # If they're flipping share on for a group that has
                # orphaned segregated history, offer migration parallel
                # to the per-user flow.
                if share_desktop:
                    self._maybe_offer_telegram_group_migration(chat_id)
        dlg.Destroy()

    def _on_tg_edit_group(self, event):
        idx = self.tg_groups_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_tg_group_ids_in_order", [])):
            wx.MessageBox("Pick a group from the list first.", "Edit group",
                          wx.OK | wx.ICON_INFORMATION)
            return
        chat_id_old = self._tg_group_ids_in_order[idx]
        tg = (self.cfg.get("telegram") or {})
        groups = tg.get("groups") or {}
        entry = groups.get(chat_id_old) or {}
        share_map = tg.get("group_share_desktop") or {}
        share_old = bool(share_map.get(chat_id_old, False))
        dlg = _TelegramGroupDialog(
            self,
            chat_id=chat_id_old,
            label=entry.get("label") or "",
            policy=entry.get("policy") or "mention_only",
            share_desktop=share_old,
            exclude=entry.get("exclude") or [],
            seen_members=self._seen_group_members(chat_id_old),
        )
        if dlg.ShowModal() == wx.ID_OK:
            (chat_id_new, label_new, policy_new, share_new,
             exclude_new) = dlg.get_values()
            if not chat_id_new:
                wx.MessageBox("Chat ID can't be empty.", "Edit group",
                              wx.OK | wx.ICON_WARNING)
            elif not chat_id_new.startswith("-"):
                wx.MessageBox(
                    "Group chat IDs are negative — Telegram uses the "
                    "sign to distinguish them from user IDs.",
                    "Edit group", wx.OK | wx.ICON_WARNING,
                )
            else:
                tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
                groups = dict(tg.get("groups") or {})
                share_map = dict(tg.get("group_share_desktop") or {})
                if chat_id_new != chat_id_old:
                    groups.pop(chat_id_old, None)
                    share_map.pop(chat_id_old, None)
                groups[chat_id_new] = {"label": label_new,
                                       "policy": policy_new,
                                       "exclude": exclude_new}
                share_map[chat_id_new] = share_new
                tg["groups"] = groups
                tg["group_share_desktop"] = share_map
                self._commit_telegram_dict(tg)
                self._refresh_tg_groups_list()
                # Newly-on share for a group with existing segregated
                # history? Offer migration — same flow as users.
                if share_new and not share_old:
                    self._maybe_offer_telegram_group_migration(chat_id_new)
        dlg.Destroy()

    def _on_tg_remove_group(self, event):
        idx = self.tg_groups_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_tg_group_ids_in_order", [])):
            wx.MessageBox("Pick a group from the list first.", "Remove group",
                          wx.OK | wx.ICON_INFORMATION)
            return
        chat_id = self._tg_group_ids_in_order[idx]
        confirm = wx.MessageBox(
            f"Remove group {chat_id}? The kin will stop responding "
            f"to normal messages in this chat. The kin's bot can "
            f"still be a member of the group on Telegram's side; "
            f"this just turns off conversation.",
            "Remove group", wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        tg = dict(self.cfg.get("telegram") or DEFAULT_TELEGRAM_CONFIG)
        groups = dict(tg.get("groups") or {})
        groups.pop(chat_id, None)
        tg["groups"] = groups
        # Also drop the group's share-with-desktop entry — mirroring
        # the user-removal path, which pops every parallel map. Leaving
        # it behind (L-B39) meant a re-added group silently inherited
        # the old share setting.
        share_map = dict(tg.get("group_share_desktop") or {})
        share_map.pop(chat_id, None)
        tg["group_share_desktop"] = share_map
        self._commit_telegram_dict(tg)
        self._refresh_tg_groups_list()

    def _maybe_offer_telegram_group_migration(self, chat_id):
        """Parallel to _maybe_offer_telegram_migration but for the
        per-group share-with-desktop toggle. When share just got
        turned on for a group with existing segregated history,
        offer to append that history into conversation.jsonl so the
        kin keeps its memory of prior group conversation when the
        surface unifies. Hands the running bot (if any) to the
        migration so the slice gets popped atomically — a group
        message arriving mid-migration would otherwise race between
        the bot's save and the migration's save and silently
        vanish (same race the user-side migration closes)."""
        from telegram_bot import load_telegram_history
        try:
            histories = load_telegram_history(self.kin) or {}
        except Exception:
            histories = {}
        history_key = f"group:{chat_id}"
        bot = None
        try:
            bot = (getattr(self.frame, "bots", None) or {}).get(self.kin)
        except Exception:
            bot = None
        in_memory = []
        if bot is not None:
            try:
                with bot._histories_lock:
                    in_memory = list(bot._histories.get(history_key) or [])
            except Exception:
                in_memory = []
        file_msgs = histories.get(history_key) or []
        n = max(len(file_msgs), len(in_memory))
        if n <= 0:
            return  # nothing to migrate
        confirm = wx.MessageBox(
            (
                f"Group {chat_id} has {n} existing messages in its "
                f"segregated history. Append them to the kin's main "
                f"conversation so the kin remembers the prior context "
                f"on the desktop too?\n\n"
                f"Yes: messages get appended to conversation.jsonl, "
                f"tagged 'telegram:group:{chat_id}', and removed from "
                f"telegram_history.json.\n"
                f"No: the prior history stays in telegram_history.json "
                f"as an orphan (the kin won't see it from desktop OR "
                f"from group — the new merged path reads from "
                f"conversation.jsonl). You can migrate later by "
                f"toggling share off and back on."
            ),
            "Migrate prior group history?",
            wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        from kin_persistence import migrate_group_history_to_conversation
        migrated, err = migrate_group_history_to_conversation(
            self.kin, chat_id, bot=bot,
        )
        if err:
            wx.MessageBox(
                f"Migrated {migrated} of {n} messages, then hit an error: {err}\n"
                f"The remaining messages stayed in telegram_history.json.",
                "Migration partial",
                wx.OK | wx.ICON_WARNING,
            )
        else:
            wx.MessageBox(
                f"Migrated {migrated} messages into the kin's main "
                f"conversation. The desktop chat will refresh "
                f"automatically.",
                "Migration done",
                wx.OK | wx.ICON_INFORMATION,
            )
        try:
            if self.frame.current_agent == self.kin:
                self.frame._reload_active_kin_conversation_from_disk()
        except Exception:
            pass

    def _maybe_offer_telegram_migration(self, user_id):
        """When share-with-desktop just got turned on for a user,
        check whether they have an orphaned per-user history slice
        in telegram_history.json — and if so, offer to append it
        into the kin's main conversation.jsonl so the kin keeps its
        memory of the prior Telegram conversation.

        Without this, flipping share on silently disconnects the
        user from everything they've said before — the file's still
        on disk but no code path reads it. That's a real continuity
        loss for an operator who's been chatting with their kin from
        their phone for weeks before deciding to unify the surfaces.

        Hands the running bot (if any) to the migration so the slice
        gets popped atomically from the bot's in-memory state — a
        Telegram message arriving mid-migration would otherwise race
        between the bot's save and the migration's save and silently
        vanish."""
        from telegram_bot import load_telegram_history
        try:
            histories = load_telegram_history(self.kin) or {}
        except Exception:
            histories = {}
        # Also count anything in the bot's in-memory dict that hasn't
        # been saved yet (the bot saves on each new message, but an
        # in-flight save could land after our read).
        bot = None
        try:
            bot = (getattr(self.frame, "bots", None) or {}).get(self.kin)
        except Exception:
            bot = None
        in_memory = []
        if bot is not None:
            try:
                with bot._histories_lock:
                    in_memory = list(bot._histories.get(user_id) or [])
                    in_memory.extend(bot._histories.get(str(user_id)) or [])
            except Exception:
                in_memory = []
        file_msgs = histories.get(str(user_id)) or []
        # Upper bound — same message could appear in both sources if
        # the bot saved between our two reads, but the migration will
        # dedupe via pop_user_history. This count is for the confirm
        # prompt; the actual migration uses the atomic pop result.
        n = max(len(file_msgs), len(in_memory))
        if n <= 0:
            return  # nothing to migrate
        confirm = wx.MessageBox(
            (
                f"User {user_id} has {n} existing Telegram messages with "
                f"this kin. Append them to the kin's main conversation "
                f"so the kin remembers the prior context on the desktop "
                f"too?\n\n"
                f"Yes: messages get appended to conversation.jsonl, "
                f"tagged 'telegram:{user_id}', and removed from "
                f"telegram_history.json.\n"
                f"No: the prior history stays in telegram_history.json "
                f"as an orphan (the kin won't see it). You can migrate "
                f"later by toggling share off and back on.\n\n"
                f"Note on ordering: these historical Telegram messages "
                f"don't have timestamps stored — older builds didn't "
                f"capture them. So they'll be appended at the end of "
                f"the desktop conversation in their original Telegram "
                f"order, not interleaved chronologically with desktop "
                f"messages. Any new Telegram messages going forward "
                f"will have timestamps so future migrations can sort "
                f"properly."
            ),
            "Migrate prior Telegram history?",
            wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        from kin_persistence import migrate_telegram_history_to_conversation
        migrated, err = migrate_telegram_history_to_conversation(
            self.kin, user_id, bot=bot,
        )
        if err:
            wx.MessageBox(
                f"Migrated {migrated} of {n} messages, then hit an error: {err}\n"
                f"The remaining messages stayed in telegram_history.json. "
                f"You may want to check the file by hand.",
                "Migration partial",
                wx.OK | wx.ICON_WARNING,
            )
        else:
            wx.MessageBox(
                f"Migrated {migrated} messages into the kin's main "
                f"conversation. The desktop chat will refresh "
                f"automatically.",
                "Migration done",
                wx.OK | wx.ICON_INFORMATION,
            )
        # Force a refresh of the desktop's in-memory conversation so
        # the kin sees the migrated lines on the very next desktop
        # message — without this, the desktop's self.conversation is
        # stale until the user kin-switches manually. No-op if the
        # active kin isn't the one we just migrated for.
        try:
            if self.frame.current_agent == self.kin:
                self.frame._reload_active_kin_conversation_from_disk()
        except Exception:
            pass

    def _on_tg_test_token(self, event):
        token = self.tg_token_field.GetValue().strip()
        if not token:
            self.tg_test_label.SetValue("Empty token.")
            return
        self.tg_test_label.SetValue("Testing...")

        def worker():
            ok, msg = telegram_test_token(token)
            wx.CallAfter(self._on_tg_test_token_done, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tg_test_token_done(self, ok, msg):
        # Liveness guard (L-B40) — the dialog may have been closed
        # while the network call ran; touching a destroyed widget
        # raises RuntimeError.
        if not self:
            return
        self.tg_test_label.SetValue(("✓ " + msg) if ok else ("✗ " + msg))

    def _on_tg_enabled_toggle(self, event):
        if self._loading:
            return
        enabled = self.tg_enabled_check.GetValue()
        self._save_telegram_param("enabled", enabled)
        if enabled:
            if not self.tg_token_field.GetValue().strip():
                self.tg_test_label.SetValue("✗ Set a bot token first.")
                self.tg_enabled_check.SetValue(False)
                self._save_telegram_param("enabled", False)
                return
            self.frame._start_bot_for(self.kin)
        else:
            self.frame._stop_bot_for(self.kin)

    # --- Discord --- #

    def _on_dc_enabled_toggle(self, event):
        if self._loading:
            return
        enabled = self.dc_enabled_check.GetValue()
        self._save_discord_param("enabled", enabled)
        if enabled:
            if not self.dc_token_field.GetValue().strip():
                self.dc_status_label.SetValue("✗ Set a bot token first.")
                self.dc_enabled_check.SetValue(False)
                self._save_discord_param("enabled", False)
                return
            self.frame._start_discord_bot_for(self.kin)
            self.dc_status_label.SetValue("Status: starting…")
        else:
            self.frame._stop_discord_bot_for(self.kin)
            self.dc_status_label.SetValue("Status: stopped.")

    def _on_dc_token_changed(self, event):
        if event:
            event.Skip()
        if self._loading:
            return
        self._save_discord_param(
            "bot_token", self.dc_token_field.GetValue().strip())

    def _on_dc_policy_changed(self, _event):
        if self._loading:
            return
        val = "always" if self.dc_policy_choice.GetSelection() == 1 else "mention_only"
        self._save_discord_param("policy", val)

    def _on_dc_share_toggle(self, _event):
        if self._loading:
            return
        # Forward-only: flipping this changes where NEW turns go; it does
        # not migrate history already written to the other store. The bot
        # reads the flag fresh each message, so it takes effect on the next
        # reply with no restart.
        self._save_discord_param(
            "share_desktop", self.dc_share_check.GetValue())

    # --- Discord people list --- #
    # allow_from is the membership list; user_tools is the per-person bucket.
    # They are separate keys in the config (the bot reads both), so every
    # write here keeps them in step — an ID in allow_from with no user_tools
    # entry falls back to 'none', which is safe but reads as a broken kin.

    def _refresh_dc_users_list(self):
        # Capture the selected ID BEFORE rebuilding the parallel key list, so
        # the selection can be restored by ID rather than by position —
        # otherwise NVDA's focus drops to the top of the list after every
        # add / edit / remove.
        _prev_idx = self.dc_users_list.GetSelection()
        _prev_uid = (self._dc_user_ids_in_order[_prev_idx]
                     if 0 <= _prev_idx < len(
                         getattr(self, "_dc_user_ids_in_order", []))
                     else None)

        dc = (self.cfg.get("discord") or {})
        allow = dc.get("allow_from") or []
        tools = dc.get("user_tools") or {}
        items = []
        self._dc_user_ids_in_order = []
        for raw_id in allow:
            sid = str(raw_id)
            self._dc_user_ids_in_order.append(sid)
            bucket = tools.get(sid) or "none"
            who = "anyone in this bot's servers" if sid == "*" else sid
            items.append(f"{who} — tools: {bucket}")
        rebuild_listbox(
            self.dc_users_list, items,
            keys=self._dc_user_ids_in_order,
            saved_key=_prev_uid,
            saved_index=_prev_idx,
        )

    @staticmethod
    def _dc_valid_user_id(uid):
        """Discord IDs are numeric. "*" is the deliberate open-to-anyone
        value the bot understands, and used to be unenterable because the old
        field kept digits only."""
        return bool(uid) and (uid == "*" or uid.isdigit())

    def _on_dc_add_user(self, event):
        dlg = _DiscordUserDialog(self, user_id="", bucket="none")
        if dlg.ShowModal() == wx.ID_OK:
            uid, bucket = dlg.get_values()
            if not self._dc_valid_user_id(uid):
                wx.MessageBox(
                    "A Discord user ID is a string of digits — turn on "
                    "Developer Mode in Discord, right-click someone and "
                    "choose Copy User ID. Type * instead to let anyone talk "
                    "to this kin.",
                    "Add person", wx.OK | wx.ICON_WARNING)
            else:
                dc = dict(self.cfg.get("discord") or DEFAULT_DISCORD_CONFIG)
                allow = [str(x) for x in (dc.get("allow_from") or [])]
                tools = dict(dc.get("user_tools") or {})
                if uid not in allow:
                    allow.append(uid)
                tools[uid] = bucket
                dc["allow_from"] = allow
                dc["user_tools"] = tools
                self._commit_discord_dict(dc)
                self._refresh_dc_users_list()
        dlg.Destroy()

    def _on_dc_edit_user(self, event):
        idx = self.dc_users_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_dc_user_ids_in_order", [])):
            wx.MessageBox("Pick a person from the list first.", "Edit person",
                          wx.OK | wx.ICON_INFORMATION)
            return
        uid_old = self._dc_user_ids_in_order[idx]
        dc = (self.cfg.get("discord") or {})
        tools = dc.get("user_tools") or {}
        dlg = _DiscordUserDialog(
            self, user_id=uid_old, bucket=tools.get(uid_old, "none"))
        if dlg.ShowModal() == wx.ID_OK:
            uid_new, bucket_new = dlg.get_values()
            if not self._dc_valid_user_id(uid_new):
                wx.MessageBox(
                    "A Discord user ID is a string of digits, or * for "
                    "anyone.", "Edit person", wx.OK | wx.ICON_WARNING)
            else:
                dc = dict(self.cfg.get("discord") or DEFAULT_DISCORD_CONFIG)
                allow = [str(x) for x in (dc.get("allow_from") or [])]
                tools = dict(dc.get("user_tools") or {})
                if uid_new != uid_old:
                    allow = [a for a in allow if a != uid_old]
                    tools.pop(uid_old, None)
                if uid_new not in allow:
                    allow.append(uid_new)
                tools[uid_new] = bucket_new
                dc["allow_from"] = allow
                dc["user_tools"] = tools
                self._commit_discord_dict(dc)
                self._refresh_dc_users_list()
        dlg.Destroy()

    def _on_dc_remove_user(self, event):
        idx = self.dc_users_list.GetSelection()
        if idx < 0 or idx >= len(getattr(self, "_dc_user_ids_in_order", [])):
            wx.MessageBox("Pick a person from the list first.",
                          "Remove person", wx.OK | wx.ICON_INFORMATION)
            return
        uid = self._dc_user_ids_in_order[idx]
        who = "anyone" if uid == "*" else uid
        if wx.MessageBox(
                f"Stop {who} from talking to {self.kin} on Discord?\n\n"
                f"Their past messages in the channel are not touched.",
                "Remove person", wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        dc = dict(self.cfg.get("discord") or DEFAULT_DISCORD_CONFIG)
        dc["allow_from"] = [str(x) for x in (dc.get("allow_from") or [])
                            if str(x) != uid]
        tools = dict(dc.get("user_tools") or {})
        tools.pop(uid, None)
        dc["user_tools"] = tools
        self._commit_discord_dict(dc)
        self._refresh_dc_users_list()

    def _commit_discord_dict(self, dc):
        """Write a whole rebuilt discord sub-dict back and persist. Mirrors
        _commit_telegram_dict — the per-param helper below is for single
        scalar toggles, and using it for a multi-key update would re-read
        and re-save the config once per key."""
        cfg = load_agent_config(self.kin)
        cfg["discord"] = dc
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

    def _save_discord_param(self, key, val):
        if self._loading:
            return
        cfg = load_agent_config(self.kin)
        dc = cfg.setdefault("discord", dict(DEFAULT_DISCORD_CONFIG))
        dc[key] = val
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

    # --- Param save helpers --- #

    def _on_open_sampling_settings(self, _event):
        """Open the per-kin sampling / generation parameters in their own
        dialog. Button-opens-dialog (not an inline disclosure) because
        NVDA skims past reveal checkboxes; matches the recall-settings and
        model-browser pattern. The dialog edits the same generation keys
        through this dialog's _save_param, so saves are byte-identical to
        the old inline sliders."""
        from dialogs.sampling_settings import SamplingSettingsDialog
        dlg = SamplingSettingsDialog(self, self.cfg, self._save_param,
                                     kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _save_param(self, key, val):
        if self._loading:
            return
        # Load-modify-save (DH7c): apply ONLY this key on top of the
        # current on-disk config rather than writing the dialog's
        # whole snapshot back. The frame advances distill_offsets (and
        # other bookkeeping) on disk while this dialog is open — a
        # whole-snapshot write from a stale self.cfg silently regressed
        # those, re-billing already-distilled turns. self.cfg is
        # refreshed from the merged result so widget reads stay
        # coherent.
        cfg = load_agent_config(self.kin)
        cfg[key] = val
        save_agent_config(self.kin, cfg)
        self.cfg = cfg
        # Cap-affecting changes invalidate the cached
        # last_reported_prompt_tokens — that number is the % of cap
        # readout in the status bar, token label, and Usage tab. If
        # num_ctx just changed, the cached value still reflects the
        # OLD cap and would render a misleading percentage until the
        # next send overwrites it. Clearing forces a fall-back to the
        # capped estimate, which uses the new num_ctx. Model changes
        # would similarly invalidate (different tokenizer makes the
        # cached count incomparable), but the model-change path goes
        # through Hearthkin._change_kin_model, which handles its own
        # invalidation.
        if key == "num_ctx":
            try:
                llm_backend.invalidate_last_reported(self.kin)
            except Exception:
                pass

    def _on_open_model_options(self, _event):
        """Open the lower-frequency model knobs (reasoning detail, image
        history, caching, provider routing, watchdog, Ollama keep-alive/
        preload) in their own dialog. Button-opens-dialog, like sampling
        and recall; the dialog edits the same keys through this dialog's
        _save_param, and omits groups that don't apply to the kin's
        current model."""
        from dialogs.model_options import MoreModelOptionsDialog
        dlg = MoreModelOptionsDialog(self, self.cfg, self._save_param,
                                     kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_open_tool_settings(self, _event):
        """Open the lower-frequency tool-behaviour knobs (approval timeout,
        tool history, result cap, max tool calls) in their own dialog,
        leaving the Tools tab focused on trust level + the enable list."""
        from dialogs.tool_settings import ToolSettingsDialog
        dlg = ToolSettingsDialog(self, self.cfg, self._save_param,
                                 self._save_telegram_param, kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_open_park_settings(self, _event):
        """Open the park settings (how this kin plays, and which park it
        tends) in their own dialog. `park` had no UI at all before this —
        it was JSON-only, which is why the first keeper had to be set up by
        hand — and `park_save` is what lets several kin share one park with
        the operator instead of each keeping a private one."""
        from dialogs.park_settings import ParkSettingsDialog
        dlg = ParkSettingsDialog(self, self.cfg, self._save_param,
                                 kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_open_voice_tuning(self, _event):
        """Open the voice tuning sliders (stability / similarity / style /
        speed) in their own dialog, leaving the Voice tab to enable + the
        voice picker. Saves into the voice sub-dict via _save_voice_param."""
        from dialogs.voice_settings import VoiceTuningDialog
        dlg = VoiceTuningDialog(self, self.cfg, self._save_voice_param,
                                kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_open_telegram_settings(self, _event):
        """Open the Telegram message-behaviour knobs (tool-call display,
        summary footer, progress ping, history cap, reply cap) in their own
        dialog, leaving the tab to the token, user/group lists, and the
        run-bot toggle."""
        from dialogs.telegram_settings import TelegramMessageSettingsDialog
        dlg = TelegramMessageSettingsDialog(
            self, self.cfg, self._save_param, self._save_telegram_param,
            kin_name=self.kin)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _kick_off_ctx_label_refresh(self):
        """Async refresh of the 'Model max: N tokens' hint. Used to be
        synchronous, but _lookup_model_max_context for an uncached
        Ollama model hits /api/show on localhost — fast usually, but
        slow enough on a cold daemon (or first call after model
        installation) to noticeably freeze the dialog. Pattern:
        show '(loading…)' immediately on the UI thread, fire a worker
        thread to fetch the real value, repaint via wx.CallAfter.

        Called from __init__ (dialog open) and after a chat-model
        swap via _on_browse_models. If the user re-triggers a refresh
        while one's already in flight, the earlier one's wx.CallAfter
        still runs but its result lands first — UI repaints twice,
        with the later (more recent) cfg model winning. Acceptable
        for a label that's purely informational."""
        model = (self.cfg.get("model") or "").strip()
        if not model:
            self.ctx_model_max_lbl.SetValue("")
            return
        self.ctx_model_max_lbl.SetValue("Model max: (loading…)")

        def worker(target_model=model):
            ctx = None
            try:
                ctx = self.frame._lookup_model_max_context(target_model)
            except Exception:
                ctx = None
            wx.CallAfter(self._apply_ctx_label, target_model, ctx)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_ctx_label(self, model_at_kickoff, ctx):
        """UI-thread callback for _kick_off_ctx_label_refresh. Skips
        the paint if the kin's model has changed since this lookup
        was kicked off — protects against stale results landing after
        a faster newer lookup.

        Also clamps the num_ctx field's upper bound to the model's
        declared max when known. The global 1M ceiling exists to
        support models with that range (MiMo-Pro, Gemini Pro, Qwen
        1M variants); without per-model clamping the operator could
        set num_ctx for Sonnet to e.g. 500k and Hearthkin would
        attempt the send — Anthropic standard tier caps at 200k, so
        the call either fails or bills extended-context premium
        pricing the operator didn't ask for. The clamp prevents the
        accidental case; deliberate extended-context use can still
        be done by raising the field after this auto-clamp (the
        ceiling moves but the operator's typed value sticks)."""
        current = (self.cfg.get("model") or "").strip()
        if current != model_at_kickoff:
            return
        if isinstance(ctx, int) and ctx > 0:
            label = f"Model max: {ctx:,} tokens"
            # Clamp the field to the model's declared max. If the
            # current num_ctx was above it, SetMaxValue returns the
            # clipped value — surface that visibly + via NVDA so the
            # operator knows a config change just happened.
            try:
                clipped_to = self.ctx_spin.SetMaxValue(ctx)
                if clipped_to is not None:
                    label += f"  (capped from prior value to {clipped_to:,})"
                    try:
                        from audio import nvda_speak
                        nvda_speak(
                            f"Context window capped at model max "
                            f"of {clipped_to:,} tokens for this model."
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        elif model_at_kickoff:
            label = "Model max: (unknown)"
            # Unknown model max — fall back to the global 1M ceiling
            # so the operator isn't stuck at whatever the last known
            # model's max was after a swap to an unrecognized one.
            try:
                self.ctx_spin.SetMaxValue(1048576)
            except Exception:
                pass
        else:
            label = ""
        try:
            self.ctx_model_max_lbl.SetValue(label)
        except Exception:
            # Dialog may have been destroyed before the worker landed
            pass

    def _kick_off_think_capability_refresh(self):
        """Async-detect whether the active model has a separate
        reasoning channel, then update the four think-effort radios
        to reflect reality:

          - Capability TRUE (Ollama declares thinking, OR remote
            model whose support we can't auto-detect): all four
            tiers stay enabled. Hint reads as such.
          - Capability FALSE (local Ollama model that doesn't
            declare thinking): grey out Low/Medium/High, force the
            saved value to "off". Hint explains.
          - Capability UNKNOWN: same as TRUE — leave enabled, but
            note in the hint that we can't auto-confirm.

        For Ollama models we hit /api/show on a worker thread (same
        pattern as _kick_off_ctx_label_refresh — fast usually, but
        on a cold daemon can take a beat). For OpenRouter models we
        skip the lookup entirely; remote-model capability isn't in
        any cheap local cache we have."""
        model = (self.cfg.get("model") or "").strip()
        if not model:
            self._apply_think_capability("", None)
            return
        # OpenRouter: don't do a network call. Show a hint that
        # explains the situation; leave radios enabled (Off works
        # explicitly for these models, so the user has real control).
        if model.startswith("openrouter/"):
            self._apply_think_capability(model, "remote")
            return
        # Ollama: query capability on a worker thread.
        self.think_capability_hint.SetValue(
            "(checking model's reasoning support…)"
        )

        def worker(target_model=model):
            supports = None
            try:
                from model_utils import _model_supports_thinking
                supports = _model_supports_thinking(target_model)
            except Exception:
                supports = None
            wx.CallAfter(
                self._apply_think_capability, target_model, supports,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_think_capability(self, model_at_kickoff, supports):
        """UI-thread callback for _kick_off_think_capability_refresh.
        Bails if the kin's model has changed since the lookup
        started, so a slow lookup can't paint stale state over a
        faster newer one.

        `supports` semantics:
          True       → Ollama model declares thinking
          False      → Ollama model does not declare thinking
          "remote"   → OpenRouter model (capability not auto-detected)
          None       → unknown (lookup failed)
        """
        current = (self.cfg.get("model") or "").strip()
        if current != model_at_kickoff and model_at_kickoff:
            return

        try:
            if supports is False:
                # Definite NO: grey out the non-Off tiers and force
                # the saved value to off. Don't surprise the user
                # with a still-armed Medium that does nothing.
                for tier, rb in self._think_effort_radios:
                    rb.Enable(tier == "off")
                self.think_effort_off.SetValue(True)
                if (self.cfg.get("think_effort") or "off") != "off":
                    self._save_param("think_effort", "off")
                # Leave the dependent display widgets enabled even
                # though there's no reasoning to show — disabling
                # them removes them from NVDA's tab cycle (a wxMSW
                # disabled control isn't tab-reachable), making the
                # option look like it vanished. The hint label
                # below explains.
                self.think_capability_hint.SetValue(
                    "(this model has no reasoning channel — "
                    "tiers other than Off do nothing)"
                )
                return

            # All other cases (True, "remote", None): leave radios
            # enabled. Differentiate the hint text so the user knows
            # what's actually happening.
            for _tier, rb in self._think_effort_radios:
                rb.Enable(True)
            # Dependent display widgets stay enabled regardless of
            # effort (see notes above and by _on_think_effort_change).
            if supports is True:
                self.think_capability_hint.SetValue(
                    "(this model has a reasoning channel — all tiers work)"
                )
            elif supports == "remote":
                self.think_capability_hint.SetValue(
                    "(remote model — Off explicitly disables reasoning "
                    "even on models that default to it; other tiers "
                    "are honored if the provider supports them)"
                )
            else:
                self.think_capability_hint.SetValue(
                    "(couldn't detect this model's reasoning support; "
                    "Off is safe to pick if you want to be sure)"
                )
        except Exception:
            # Dialog may have been destroyed mid-flight.
            pass

    def _save_telegram_param(self, key, val):
        if self._loading:
            return
        # Load-modify-save into the telegram sub-dict — same DH7(c)
        # rationale as _save_param.
        cfg = load_agent_config(self.kin)
        tg = cfg.setdefault("telegram", dict(DEFAULT_TELEGRAM_CONFIG))
        tg[key] = val
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

    def _commit_telegram_dict(self, tg):
        """Write a fully-rebuilt telegram sub-dict via load-modify-save —
        the user/group list handlers rebuild several parallel maps at
        once, so they hand the whole sub-dict here instead of going
        key-by-key through _save_telegram_param. Same DH7(c) rationale:
        only the telegram sub-dict is replaced; everything else (e.g.
        distill_offsets advanced on disk while the dialog is open)
        survives untouched."""
        cfg = load_agent_config(self.kin)
        cfg["telegram"] = tg
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

    # --- Voice section handlers --- #

    def _save_voice_param(self, key, val):
        """Per-kin voice setting writer. Mirrors _save_telegram_param's
        nested-dict shape — voice settings live under cfg['voice'].
        Load-modify-save per the same DH7(c) rationale."""
        if self._loading:
            return
        cfg = load_agent_config(self.kin)
        v = cfg.setdefault("voice", {
            "enabled": False,
            "voice_id": "",
            "model_id": "eleven_turbo_v2_5",
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "speed": 1.0,
        })
        v[key] = val
        save_agent_config(self.kin, cfg)
        self.cfg = cfg

    def _on_voice_enabled_toggle(self, _event):
        self._save_voice_param("enabled", bool(self.voice_enabled_check.GetValue()))

    def _on_voice_pick_changed(self, _event):
        sel = self.voice_pick_choice.GetSelection()
        if sel < 0 or sel >= len(self._voice_pick_ids):
            return
        self._save_voice_param("voice_id", self._voice_pick_ids[sel])

    def _on_voice_refresh(self, _event):
        """Re-fetch the voice catalog on user request — the engine
        caches it, so this drops the cache and reloads."""
        self._populate_voice_picker(force_refresh=True, user_triggered=True)

    def _on_voice_preview(self, _event):
        """Play the canonical sample clip for the selected voice.
        Uses the preview_url ElevenLabs returns in /v1/voices — that
        URL points at a pre-rendered MP3 of the voice, so no TTS
        cost. Opens in the user's default browser, which on Windows
        plays it inline via the audio player."""
        sel = self.voice_pick_choice.GetSelection()
        # Index 0 is the "(no voice selected)" sentinel (empty id).
        if (sel < 0 or sel >= len(self._voice_pick_ids)
                or not self._voice_pick_ids[sel]):
            wx.MessageBox(
                "Pick a voice from the list first.",
                "Hearthkin", wx.OK | wx.ICON_INFORMATION,
            )
            return
        try:
            v = self.frame._voice_engine.get_voice(self._voice_pick_ids[sel])
        except Exception as e:
            wx.MessageBox(
                f"Couldn't load voice info: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )
            return
        url = (v or {}).get("preview_url")
        if not url:
            wx.MessageBox(
                "This voice has no preview clip.",
                "Hearthkin", wx.OK | wx.ICON_INFORMATION,
            )
            return
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            wx.MessageBox(
                f"Couldn't open preview: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )

    def _on_voice_test(self, _event):
        """Speak a fixed test sentence using the kin's current voice
        settings, so the user can hear what their saved tuning
        actually sounds like."""
        v_cfg = self.cfg.get("voice") or {}
        if not (v_cfg.get("voice_id") or "").strip():
            wx.MessageBox(
                "Pick a voice from the list first.",
                "Hearthkin", wx.OK | wx.ICON_INFORMATION,
            )
            return
        sentence = (
            f"Hello — this is what I sound like as {self.kin}. "
            "If the speed or expression isn't right, adjust the sliders."
        )
        try:
            self.frame._voice_engine.speak_sentence(sentence, v_cfg)
        except Exception as e:
            wx.MessageBox(
                f"Voice test failed: {e}",
                "Hearthkin", wx.OK | wx.ICON_ERROR,
            )

    def _populate_voice_picker(self, force_refresh=False, user_triggered=False):
        """Fetch the voice catalog (cached unless force_refresh) and
        populate the wx.Choice. Runs on a worker thread so a slow
        ElevenLabs response doesn't freeze the dialog. `user_triggered`
        marks an explicit user action (the Refresh button) vs the
        dialog-open prefetch — failure speech differs (M-D6)."""
        engine = getattr(self.frame, "_voice_engine", None)
        if engine is None:
            self.voice_pick_choice.Set(["(voice engine unavailable)"])
            self.voice_pick_choice.Disable()
            return
        self.voice_refresh_btn.Disable()
        self.voice_pick_choice.Set(["(loading…)"])
        self.voice_pick_choice.Disable()

        def worker():
            try:
                voices = engine.list_voices(force_refresh=force_refresh)
            except Exception as e:
                err_msg = str(e)
                wx.CallAfter(self._on_voice_picker_error, err_msg,
                             user_triggered)
                return
            wx.CallAfter(self._on_voice_picker_loaded, voices)

        threading.Thread(target=worker, daemon=True).start()

    def _on_voice_picker_loaded(self, voices):
        # Liveness guard (L-B40) — dialog may have closed mid-fetch.
        if not self:
            return
        if not voices:
            self.voice_pick_choice.Set(["(no voices in your library)"])
            self.voice_pick_choice.Disable()
            self.voice_refresh_btn.Enable()
            return
        # Sentinel row at index 0 (L-B42): when the kin has no voice
        # picked, the dropdown used to silently land on the first real
        # voice — NVDA announced it as if it were configured. The
        # sentinel maps to voice_id "" so picking it explicitly also
        # clears the setting.
        labels = ["(no voice selected)"]
        ids = [""]
        for v in voices:
            name = v.get("name") or "(unnamed)"
            labels_meta = v.get("labels") or {}
            descriptors = []
            for k in ("gender", "age", "accent", "use_case"):
                val = (labels_meta.get(k) or "").strip()
                if val:
                    descriptors.append(val)
            if descriptors:
                label = f"{name} — {', '.join(descriptors)}"
            else:
                label = name
            labels.append(label)
            ids.append(v.get("voice_id") or "")
        self.voice_pick_choice.Set(labels)
        self._voice_pick_ids = ids
        self.voice_pick_choice.Enable()
        self.voice_refresh_btn.Enable()
        # Restore the kin's current selection if it's still in the
        # list; empty or missing voice_id lands on the sentinel.
        current_id = (self.cfg.get("voice") or {}).get("voice_id") or ""
        if current_id and current_id in ids:
            self.voice_pick_choice.SetSelection(ids.index(current_id))
        else:
            self.voice_pick_choice.SetSelection(0)

    def _on_voice_picker_error(self, err_msg, user_triggered=False):
        # Liveness guard (L-B40) — dialog may have closed mid-fetch.
        if not self:
            return
        # Most common: missing API key. NVDA can't read disabled
        # control content via obj-nav, so just stuffing the error in
        # the dropdown leaves a screen-reader user with "unavailable"
        # and no diagnostic. Surfacing layers:
        #   1. dropdown text (sighted users)
        #   2. main-frame Activity field (NVDA reads on focus)
        #   3. NVDA speech — ONLY when the user explicitly asked for
        #      the voice list (Refresh button) or this kin actually
        #      has voice enabled. The dialog-open prefetch failing on
        #      a keyless install is expected, not announcement-worthy
        #      (M-D6 — it used to force-speak on every Settings open).
        self.voice_pick_choice.Set([f"(error: {err_msg[:80]})"])
        self.voice_pick_choice.Disable()
        self.voice_refresh_btn.Enable()
        try:
            self.frame._set_status(f"Voice list failed: {err_msg}")
        except Exception:
            pass
        voice_enabled = bool((self.cfg.get("voice") or {}).get("enabled"))
        if user_triggered or voice_enabled:
            try:
                from audio import nvda_speak as _nvda_speak
                _nvda_speak(f"Voice list failed: {err_msg}")
            except Exception:
                pass

    # --- Cron section handlers --- #

    def _refresh_heartbeat_note(self):
        """One-line summary of the current heartbeat state so the operator can
        tell at a glance (and via NVDA) without opening the dialog."""
        hb = self.cfg.get("heartbeat") or {}
        if not hb.get("enabled"):
            txt = "Proactive heartbeat: off."
        else:
            dest = hb.get("destination") or {"surface": "desktop"}
            surf = dest.get("surface", "desktop")
            where = ("desktop chat" if surf == "desktop"
                     else f"{surf.replace('_', ' ')} {dest.get('id', '')}".strip())
            txt = (
                f"Proactive heartbeat: ON — every {hb.get('every_minutes', 120)} "
                f"min, {hb.get('active_start', '09:00')}–"
                f"{hb.get('active_end', '22:00')}, reach-outs to {where}."
            )
        try:
            self._heartbeat_note.SetValue(txt)
        except Exception:
            pass

    def _on_heartbeat_settings(self, _event):
        from .heartbeat_settings import HeartbeatSettingsDialog
        dlg = HeartbeatSettingsDialog(
            self, kin=self.kin, heartbeat=self.cfg.get("heartbeat") or {})
        if dlg.ShowModal() == wx.ID_OK:
            self._save_param("heartbeat", dlg.get_heartbeat())
            self._refresh_heartbeat_note()
        dlg.Destroy()

    def _on_cron_list_char(self, event):
        """First-letter / first-digit navigation for the cron list.
        Cron entries display as '[X] HH:MM — prompt'; intercepting
        EVT_CHAR lets us match the typed buffer against the underlying
        time or prompt fields (from cfg.cron_entries) rather than the
        useless '[' prefix.

        Accumulating buffer with 700ms reset between keypresses, same
        as model_browser._on_list_char. Matches case-insensitive
        prefix on EITHER the time string ("08", "08:30") OR the
        prompt body ("daily", "weather"). Wraps. Single-letter
        buffer advances past current selection (Windows-shell
        convention for keyboard list nav); multi-letter buffer
        includes current so a refinement keeps the same hit.

        Non-printable keys fall through to default list nav via
        event.Skip()."""
        keycode = event.GetKeyCode()
        if keycode < 32 or keycode > 126:
            event.Skip()
            return
        ch = chr(keycode).lower()
        now = time.monotonic()
        if now - self._cron_search_last > 0.7:
            self._cron_search_buf = ""
        self._cron_search_buf += ch
        self._cron_search_last = now

        entries = self.cfg.get("cron_entries") or []
        n = self.cron_listbox.GetCount()
        if n == 0 or not entries:
            return
        start = self.cron_listbox.GetSelection()
        if start == wx.NOT_FOUND:
            start = -1
        if len(self._cron_search_buf) == 1:
            offsets = list(range(start + 1, n)) + list(range(0, start + 1))
        else:
            offsets = list(range(start, n)) + list(range(0, max(start, 0)))

        buf = self._cron_search_buf
        for i in offsets:
            if i >= len(entries):
                continue
            entry = entries[i] if isinstance(entries[i], dict) else {}
            from cron_helpers import cron_entry_fire_times
            time_str = (", ".join(cron_entry_fire_times(entry))).lower()
            prompt = str(entry.get("prompt", "") or "").lower().strip()
            if time_str.startswith(buf) or prompt.startswith(buf):
                self.cron_listbox.SetSelection(i)
                try:
                    self.cron_listbox.EnsureVisible(i)
                except Exception:
                    pass
                return

    def _refresh_cron_listbox(self):
        """Repaint the listbox from self.cfg['cron_entries']. Each line
        shows the enabled marker, the time, and a truncated prompt so
        the user can see all entries at a glance without scrolling
        horizontally.

        Cron entries have no stable identifier — order matters and is
        what the model and schtasks key off — so selection is restored
        by index (clamped) rather than by key. Editing or removing an
        entry leaves focus near where it was instead of dropping back
        to entry 1.
        """
        entries = self.cfg.get("cron_entries") or []
        if not isinstance(entries, list):
            entries = []
        labels = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                labels.append(f"{i + 1}: [invalid entry]")
                continue
            enabled = bool(entry.get("enabled", False))
            from cron_helpers import cron_entry_fire_times
            _fires = cron_entry_fire_times(entry)
            time_str = ", ".join(_fires) if _fires else "(no time)"
            prompt = str(entry.get("prompt", "") or "").replace("\n", " ")
            if len(prompt) > 60:
                prompt = prompt[:57] + "..."
            marker = "[X]" if enabled else "[ ]"
            labels.append(f"{marker} {time_str} — {prompt}")
        _prev_idx = self.cron_listbox.GetSelection()
        rebuild_listbox(self.cron_listbox, labels, saved_index=_prev_idx)

    def _on_cron_add(self, event):
        dlg = CronEntryDialog(self, kin=self.kin)
        if dlg.ShowModal() == wx.ID_OK:
            new_entry = dlg.get_entry()
            entries = list(self.cfg.get("cron_entries") or [])
            entries.append(new_entry)
            self._save_param("cron_entries", entries)
            self._refresh_cron_listbox()
            self._sync_cron_schtasks()
        dlg.Destroy()

    def _on_cron_edit(self, event):
        idx = self.cron_listbox.GetSelection()
        entries = list(self.cfg.get("cron_entries") or [])
        if idx < 0 or idx >= len(entries):
            wx.MessageBox(
                "Select an entry to edit.",
                "Edit cron entry",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        existing = entries[idx] if isinstance(entries[idx], dict) else {}
        dlg = CronEntryDialog(self, kin=self.kin, entry=existing)
        if dlg.ShowModal() == wx.ID_OK:
            entries[idx] = dlg.get_entry()
            self._save_param("cron_entries", entries)
            self._refresh_cron_listbox()
            self._sync_cron_schtasks()
        dlg.Destroy()

    def _on_cron_remove(self, event):
        idx = self.cron_listbox.GetSelection()
        entries = list(self.cfg.get("cron_entries") or [])
        if idx < 0 or idx >= len(entries):
            wx.MessageBox(
                "Select an entry to remove.",
                "Remove cron entry",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        confirm = wx.MessageBox(
            f"Remove cron entry {idx + 1}?\n\nThis also removes its "
            f"Windows Task Scheduler entry. The entry can be re-added later.",
            "Confirm remove",
            wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_QUESTION,
            self,
        )
        if confirm != wx.YES:
            return
        entries.pop(idx)
        self._save_param("cron_entries", entries)
        self._refresh_cron_listbox()
        self._sync_cron_schtasks()

    def _on_test_tool_calling(self, event):
        """Ask this kin's model for one real tool call and report back.

        Same shape as the cron test next door, and for the same reason:
        this runs a full inference, which took the UI down with it when
        the cron version ran inline (M-D2). Disable the button, say out
        loud that it started, do the work on a daemon thread, report
        through wx.CallAfter.
        """
        from model_utils import strip_model_annotation
        model = strip_model_annotation(
            (self.cfg.get("model") or "").strip()
            or (self.frame.config.get("model") or "").strip()
        )
        if not model:
            wx.MessageBox(
                "This kin has no model set, so there's nothing to test.",
                "Test tool calling",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self.tool_probe_btn.Disable()
        self.frame._set_status("Testing tool calling on %s…" % model)
        try:
            from audio import nvda_speak
            nvda_speak("Testing tool calling. Running in the background — "
                       "a dialog will report the result.")
        except Exception:
            pass

        def worker():
            try:
                from model_utils import probe_tool_calling
                rec = probe_tool_calling(model, force=True)
            except Exception as e:
                rec = {"ok": None, "called": [], "said": "", "error": str(e)}
            wx.CallAfter(self._on_tool_probe_done, model, rec)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tool_probe_done(self, model, rec):
        """UI-thread completion handler for the tool-calling test.

        The result goes in a read-only box rather than a message box:
        when a model fails, the useful part is the prose it produced
        instead of the call, and that wants arrowing through rather than
        being read once in a single announcement. Parented to this dialog
        while it lives, to nothing after, so the answer arrives either
        way -- same rule as the cron test.
        """
        alive = bool(self)
        if alive:
            try:
                self.tool_probe_btn.Enable()
            except RuntimeError:
                alive = False
        parent = self if alive else None
        try:
            self.frame._set_status("")
        except Exception:
            pass
        from dialogs.tool_probe_result import ToolProbeResultDialog
        dlg = ToolProbeResultDialog(parent, model, rec)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _on_cron_test_now(self, event):
        idx = self.cron_listbox.GetSelection()
        entries = list(self.cfg.get("cron_entries") or [])
        if idx < 0 or idx >= len(entries):
            wx.MessageBox(
                "Select an entry to test.",
                "Test cron entry",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        # The subprocess runs a full LLM inference — up to 120s. It
        # used to run inline in this handler, freezing the whole UI
        # with no repaint and no NVDA feedback (M-D2). Now: disable
        # the button, announce the kickoff, run on a daemon thread,
        # report via wx.CallAfter. cron_helpers picks the right
        # invocation shape for source vs frozen-EXE builds;
        # _no_window_kwargs suppresses the console-window flash.
        self.cron_test_btn.Disable()
        self.frame._set_status(
            f"Testing cron entry {idx + 1} for {self.kin}…")
        try:
            from audio import nvda_speak
            nvda_speak("Testing cron entry. Running in the background — "
                       "a dialog will report the result.")
        except Exception:
            pass
        argv = cron_helpers.cron_invocation_argv_run_now(self.kin, idx)

        def worker():
            try:
                result = subprocess.run(
                    argv, capture_output=True, text=True, timeout=120,
                    check=False, **cron_helpers._no_window_kwargs(),
                )
            except subprocess.TimeoutExpired:
                wx.CallAfter(self._on_cron_test_done, "timeout", None)
                return
            except Exception as e:
                wx.CallAfter(self._on_cron_test_done, "launch", str(e))
                return
            wx.CallAfter(self._on_cron_test_done, "done", result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cron_test_done(self, kind, payload):
        """UI-thread completion handler for the async cron test. The
        result lands in a modal MessageBox (which NVDA announces) —
        parented to this dialog when it's still alive, to nothing
        otherwise, so the outcome reaches the operator either way."""
        alive = bool(self)
        if alive:
            try:
                self.cron_test_btn.Enable()
            except RuntimeError:
                alive = False
        parent = self if alive else None
        if kind == "timeout":
            wx.MessageBox(
                "Cron subprocess took longer than 120 seconds. The wake-up "
                "may have hit the model's own timeout or stalled on a "
                "network call. Check ~/.hearthkin/logs/cron_errors.log.",
                "Test cron — timeout",
                wx.OK | wx.ICON_WARNING,
                parent,
            )
            return
        if kind == "launch":
            wx.MessageBox(
                f"Could not launch cron subprocess:\n\n{payload}",
                "Test cron — launch failed",
                wx.OK | wx.ICON_ERROR,
                parent,
            )
            return
        result = payload
        if result.returncode == 0:
            wx.MessageBox(
                f"Cron wake-up fired. The reply was appended to:\n"
                f"  • {self.kin}'s conversation\n"
                f"  • the journal at memory/journal/<today>.md\n"
                f"  • Telegram (if configured)\n\n"
                f"If Hearthkin was already open for this kin, the wake-up "
                f"was routed through the running app instead.",
                "Test cron — fired",
                wx.OK | wx.ICON_INFORMATION,
                parent,
            )
        else:
            tail = (result.stderr or result.stdout or "").strip()
            if len(tail) > 400:
                tail = tail[:400] + "..."
            wx.MessageBox(
                f"Cron returned exit {result.returncode}.\n\n"
                f"Details in ~/.hearthkin/logs/cron_errors.log.\n\n"
                f"{tail or '(no subprocess output)'}",
                "Test cron — failed",
                wx.OK | wx.ICON_ERROR,
                parent,
            )

    def _sync_cron_schtasks(self):
        """Reconcile Task Scheduler entries with this kin's current
        cron_entries list. Called after every Add/Edit/Remove. No-op on
        non-Windows hosts (we still save the config in case the kin
        moves to a Windows machine later).

        Runs on a daemon thread (M-D3) — schtasks_sync_kin fires 32+
        synchronous subprocesses, a multi-second UI freeze when run
        inline. One sync in flight at a time; a request arriving
        mid-sync is stashed in _cron_sync_latest and the freshest one
        runs when the current sync finishes (latest wins — each sync
        is a full delete-and-recreate reconcile, so intermediate
        states don't need their own pass)."""
        if not cron_helpers.schtasks_supported():
            return
        self._cron_sync_latest = list(self.cfg.get("cron_entries") or [])
        if self._cron_sync_inflight:
            return  # picked up by _on_cron_sync_done when current ends
        self._start_cron_sync_worker()

    def _start_cron_sync_worker(self):
        entries = self._cron_sync_latest
        self._cron_sync_latest = None
        self._cron_sync_inflight = True
        try:
            self.frame._set_status("Syncing scheduled tasks…")
        except Exception:
            pass

        def worker(snapshot=entries, kin=self.kin):
            try:
                ok, errors = cron_helpers.schtasks_sync_kin(kin, snapshot)
            except Exception as e:
                ok, errors = False, [("(sync)", str(e))]
            wx.CallAfter(self._on_cron_sync_done, ok, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cron_sync_done(self, ok, errors):
        self._cron_sync_inflight = False
        # A newer request landed while we were syncing — run it now
        # (even if the dialog has since closed; the worker doesn't
        # touch widgets and the entries are already saved to config,
        # so Task Scheduler should still be reconciled to match).
        if self._cron_sync_latest is not None:
            self._start_cron_sync_worker()
            return
        alive = bool(self)
        if alive:
            try:
                self.frame._set_status("Scheduled tasks synced.")
            except Exception:
                pass
        if not ok and errors:
            # Show the first error only — wall-of-text on a wx.MessageBox
            # is worse than a single clear message; the rest are in the
            # subprocess output, which we don't surface for size reasons.
            task_name, msg = errors[0]
            wx.MessageBox(
                f"Could not register Task Scheduler entry "
                f"{task_name!r}:\n\n{msg}\n\nCron entries are saved to "
                f"the kin's config but may not fire on schedule. You can "
                f"check Task Scheduler manually (taskschd.msc) or retry "
                f"by re-saving the entry.",
                "Schtasks error",
                wx.OK | wx.ICON_WARNING,
                self if alive else None,
            )

    def _on_tool_toggle(self, name):
        """Save the kin's tools allowlist immediately on any checkbox change.
        Lives outside the kin's main config (lives in its own tools.json) so
        a misconfigured tools file can be cleared without touching the soul
        or telegram settings."""
        if self._loading:
            return
        enabled = [
            tname for tname, chk in self._tool_checks.items() if chk.GetValue()
        ]
        try:
            save_kin_tools(self.kin, enabled)
        except Exception as e:
            wx.MessageBox(
                f"Could not save tools allowlist for {self.kin!r}:\n\n{e}",
                "Save failed", wx.OK | wx.ICON_ERROR,
            )

    # --- Close handling --- #

    def _maybe_save_dirty(self):
        """Prompt to save dirty soul/memory. Returns False if user cancels."""
        if self._soul_dirty:
            dlg = wx.MessageDialog(
                self,
                f"The soul file for '{self.kin}' has unsaved changes. Save them?",
                "Unsaved soul",
                wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT | wx.ICON_WARNING,
            )
            r = dlg.ShowModal()
            dlg.Destroy()
            if r == wx.ID_CANCEL:
                return False
            if r == wx.ID_YES:
                save_soul(self.kin, self.soul_editor.GetValue())
                self._mark_soul_clean()
                self.frame._invalidate_kin_text_cache(self.kin)
        if self._memory_dirty:
            dlg = wx.MessageDialog(
                self,
                f"You've edited the memory file for '{self.kin}' but haven't saved. Save it?",
                "Unsaved memory",
                wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT | wx.ICON_WARNING,
            )
            r = dlg.ShowModal()
            dlg.Destroy()
            if r == wx.ID_CANCEL:
                return False
            if r == wx.ID_YES:
                save_memory(self.kin, self.memory_editor.GetValue())
                self._mark_memory_clean()
                self.frame._invalidate_kin_text_cache(self.kin)
        return True

    def _on_close_btn(self, event):
        if not self._maybe_save_dirty():
            return
        self.EndModal(wx.ID_CLOSE)

    def _on_close_evt(self, event):
        # During app shutdown (Inno installer's Restart Manager,
        # Windows logoff/reboot), the wxApp's default OnQueryEndSession
        # walks every TLW and Close()s each. Skip the unsaved-changes
        # prompt and just close — the prompt is a modal dialog with no
        # user available to answer it, which would hang the shutdown
        # cascade and stall the installer indefinitely. Any unsaved
        # soul / memory edits are lost in this path; that's the trade
        # for a clean shutdown. The normal close (via the dialog's
        # Close button or Escape) still prompts.
        if getattr(self.frame, "_quitting", False):
            self.EndModal(wx.ID_CLOSE)
            return
        if not self._maybe_save_dirty():
            if event.CanVeto():
                event.Veto()
                return
        self.EndModal(wx.ID_CLOSE)

    def Destroy(self):
        # Clear the frame's dialog reference
        if getattr(self.frame, "_edit_kin_dialog", None) is self:
            self.frame._edit_kin_dialog = None
        return super().Destroy()
