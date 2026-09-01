# SPDX-License-Identifier: CC0-1.0

"""dialogs.usage_history - extracted from the former monolithic dialogs.py."""

import datetime
import os
import subprocess
import sys

import wx


class UsageHistoryDialog(wx.Dialog):
    """Browsable view of `~/.hearthkin/logs/usage.log` for users who
    need a NVDA-friendly surface on the cost picture. The raw file is
    grep-friendly but reads as a flat wall to a screen reader; this
    dialog parses it into structured sections.

    Layout (all tab-reachable, top to bottom):
      - Date range radio (Today / Last 24h / Last 7 days / All time)
      - Kin filter dropdown (All kin / one specific kin)
      - Refresh button (re-reads + re-parses the file)
      - Summary text (total calls, tokens in/out, USD spent)
      - By-kin breakdown text (one focusable read-only TextCtrl)
      - By-model breakdown text
      - By-surface breakdown text
      - Recent calls text (last N entries, newest-first)
      - Open raw log file button (power-user shortcut)
      - Close button

    The breakdown lists are pre-formatted multi-line text rather than
    a wx.ListCtrl table because ListCtrl's column-based focus model
    on Windows is wxMSW-flaky for NVDA — multi-line read-only text
    lets arrow keys walk row by row with provider context spoken on
    every line.

    Cost numbers prefer OpenRouter's reported real_cost when present,
    falling back to the catalogue est_cost otherwise. real_cost is
    what the provider actually billed (cache discount and all);
    est_cost is the local estimate. The OpenRouter dashboard remains
    authoritative for invoice reconciliation; this is for "where IS
    my budget going" diagnosis.
    """

    RANGE_TODAY = "today"
    RANGE_24H = "24h"
    RANGE_7D = "7d"
    RANGE_ALL = "all"

    def __init__(self, parent):
        super().__init__(parent, title="Usage history",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(720, 720))
        self._range = self.RANGE_TODAY
        self._kin_filter = ""  # "" = all kin

        outer = wx.BoxSizer(wx.VERTICAL)

        # ─── Top-of-dialog explainer ─────────────────────────────
        header = wx.TextCtrl(
            self,
            value=(
                "Usage history is a per-call log of every model "
                "invocation Hearthkin made — desktop chat, rooms, "
                "Telegram, distillation, cron, all of it. The cost "
                "column is an OpenRouter-pricing-times-tokens "
                "estimate; the OpenRouter dashboard remains the "
                "authoritative billing record. Filter by date range "
                "and kin below, then read the breakdowns to see "
                "where credits went."
            ),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        header.SetName("Usage history explainer")
        header.SetMinSize((-1, 80))
        outer.Add(header, flag=wx.EXPAND | wx.ALL, border=8)

        # ─── Filters ──────────────────────────────────────────────
        # Radio buttons take their name from their own label, so a StaticText
        # here would reach nobody: a tabbing user would hear "Today, radio
        # button" with no idea what is being chosen. Read-only TextCtrl is
        # tab-reachable, so the question is heard before the options.
        range_label = wx.TextCtrl(
            self,
            value="Date range:",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        range_label.SetName("Date range")
        range_label.SetMinSize((-1, 32))
        range_row = wx.BoxSizer(wx.HORIZONTAL)
        self._range_radios = {}
        _ranges = [
            (self.RANGE_TODAY, "&Today"),
            (self.RANGE_24H, "Last &24 hours"),
            (self.RANGE_7D, "Last &7 days"),
            (self.RANGE_ALL, "&All time"),
        ]
        for i, (key, text) in enumerate(_ranges):
            style = wx.RB_GROUP if i == 0 else 0
            rb = wx.RadioButton(self, label=text, style=style)
            rb.SetValue(key == self._range)
            rb.Bind(wx.EVT_RADIOBUTTON,
                    lambda _e, k=key: self._on_range_change(k))
            self._range_radios[key] = rb
            range_row.Add(rb, flag=wx.RIGHT, border=12)
        outer.Add(range_label, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(range_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        kin_label = wx.StaticText(self, label="&Kin filter:")
        self.kin_choice = wx.Choice(self, choices=["(all kin)"])
        self.kin_choice.SetSelection(0)
        self.kin_choice.Bind(wx.EVT_CHOICE, self._on_kin_change)
        kin_row = wx.BoxSizer(wx.HORIZONTAL)
        kin_row.Add(kin_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        kin_row.Add(self.kin_choice, proportion=1,
                    flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=4)
        refresh_btn = wx.Button(self, label="&Refresh")
        refresh_btn.Bind(wx.EVT_BUTTON, lambda _e: self._reload())
        kin_row.Add(refresh_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        outer.Add(kin_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # ─── Summary ──────────────────────────────────────────────
        summary_label = wx.StaticText(self, label="Summary:")
        font = summary_label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        summary_label.SetFont(font)
        self.summary_display = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL | wx.TE_WORDWRAP,
        )
        self.summary_display.SetMinSize((-1, 80))
        self.summary_display.SetName("Summary")
        outer.Add(summary_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.summary_display,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # ─── Breakdowns ───────────────────────────────────────────
        bykin_label = wx.StaticText(self, label="&By kin:")
        bykin_label.SetFont(font)
        self.bykin_display = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.bykin_display.SetMinSize((-1, 90))
        self.bykin_display.SetName("Breakdown by kin")
        outer.Add(bykin_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.bykin_display, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        bymodel_label = wx.StaticText(self, label="By &model:")
        bymodel_label.SetFont(font)
        self.bymodel_display = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.bymodel_display.SetMinSize((-1, 90))
        self.bymodel_display.SetName("Breakdown by model")
        outer.Add(bymodel_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.bymodel_display, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        bysurface_label = wx.StaticText(self, label="By &surface (desktop / room / telegram / distill / cron):")
        bysurface_label.SetFont(font)
        self.bysurface_display = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.bysurface_display.SetMinSize((-1, 90))
        # The label's list of surfaces is never announced (this control has its
        # own name), so name it with them or a listening user never learns what
        # counts as a surface here.
        self.bysurface_display.SetName(
            "Breakdown by surface (desktop / room / telegram / distill / cron)")
        outer.Add(bysurface_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.bysurface_display, proportion=1,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        recent_label = wx.StaticText(self, label="Re&cent calls (newest first):")
        recent_label.SetFont(font)
        self.recent_display = wx.TextCtrl(
            self,
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP,
        )
        self.recent_display.SetMinSize((-1, 140))
        # Sort order is not discoverable by ear, and the label that states it is
        # never announced (this control has its own name), so carry it here.
        self.recent_display.SetName("Recent calls list (newest first)")
        outer.Add(recent_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        outer.Add(self.recent_display, proportion=2,
                  flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # ─── Bottom buttons ───────────────────────────────────────
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        open_log_btn = wx.Button(self, label="&Open raw log file")
        open_log_btn.Bind(wx.EVT_BUTTON, lambda _e: self._open_raw_log())
        close_btn = wx.Button(self, wx.ID_CLOSE, label="C&lose")
        close_btn.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CLOSE))
        close_btn.SetDefault()
        btn_row.Add(open_log_btn, flag=wx.RIGHT, border=8)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(close_btn)
        outer.Add(btn_row, flag=wx.EXPAND | wx.ALL, border=8)

        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CLOSE)
        # Initial load — populates everything from the current
        # usage.log + the default filter (today / all kin).
        self._reload()

    # --- Event handlers -------------------------------------------

    def _on_range_change(self, key):
        self._range = key
        self._reload()

    def _on_kin_change(self, _e):
        idx = self.kin_choice.GetSelection()
        if idx <= 0:
            self._kin_filter = ""
        else:
            # _kin_options[0] is "(all kin)"; subsequent entries are real names
            self._kin_filter = self._kin_options[idx]
        self._reload(rebuild_kin_choices=False)

    def _open_raw_log(self):
        """Open ~/.hearthkin/logs/usage.log in the platform's default
        text viewer. Falls back to a status-bar error on the parent
        frame if launch fails."""
        from kin_persistence import USAGE_LOG_PATH
        try:
            path = USAGE_LOG_PATH
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            wx.MessageBox(f"Couldn't open usage log: {e}",
                          "Open raw log", wx.OK | wx.ICON_WARNING)

    # --- Load + render --------------------------------------------

    def _since_for_range(self):
        """Translate the current `_range` selection into a datetime
        cutoff (UTC-naive local time). RANGE_ALL returns None."""
        now = datetime.datetime.now()
        if self._range == self.RANGE_TODAY:
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._range == self.RANGE_24H:
            return now - datetime.timedelta(hours=24)
        if self._range == self.RANGE_7D:
            return now - datetime.timedelta(days=7)
        return None

    def _reload(self, rebuild_kin_choices=True):
        from kin_persistence import parse_usage_log, aggregate_usage
        rows = parse_usage_log()
        if rebuild_kin_choices:
            self._rebuild_kin_choices(rows)
        agg = aggregate_usage(
            rows,
            since=self._since_for_range(),
            kin_filter=self._kin_filter or None,
        )
        self._render_summary(agg)
        self._render_breakdown(self.bykin_display, agg["by_kin"],
                               "kin", header_label="Kin")
        self._render_breakdown(self.bymodel_display, agg["by_model"],
                               "model", header_label="Model")
        self._render_breakdown(self.bysurface_display, agg["by_surface"],
                               "surface", header_label="Surface")
        self._render_recent(agg["rows"])

    def _rebuild_kin_choices(self, rows):
        """Update the kin filter dropdown from the set of kin names
        seen in the usage log. Preserves the current selection when
        possible."""
        names = sorted({r["kin"] for r in rows if r.get("kin")})
        self._kin_options = ["(all kin)"] + names
        self.kin_choice.Set(self._kin_options)
        if self._kin_filter and self._kin_filter in names:
            self.kin_choice.SetSelection(names.index(self._kin_filter) + 1)
        else:
            self.kin_choice.SetSelection(0)
            self._kin_filter = ""

    def _render_summary(self, agg):
        if agg["total_calls"] == 0:
            self.summary_display.SetValue(
                "No calls in the selected range.\n\n"
                "Either Hearthkin hasn't run a chat in this window, "
                "or this is a brand-new install. Pick a wider range "
                "or chat with a kin to see entries here."
            )
            return
        priced = agg["calls_with_cost"]
        unpriced = agg["total_calls"] - priced
        cost_note = ""
        if unpriced > 0:
            cost_note = (f" ({unpriced} call(s) not priced — Ollama "
                         f"local or model not in catalogue)")
        lines = [
            f"Total calls: {agg['total_calls']}",
            f"Total tokens in:  {agg['total_in']:,}",
            f"Total tokens out: {agg['total_out']:,}",
            f"Total estimated cost: ${agg['total_cost']:.4f}{cost_note}",
        ]
        self.summary_display.SetValue("\n".join(lines))

    def _render_breakdown(self, display, rows, key, header_label):
        if not rows:
            display.SetValue("(no entries in this range)")
            return
        # Pad columns visually but stay screen-reader-friendly —
        # NVDA reads space-separated columns fine, and a fixed-width
        # alignment isn't needed in a proportional font anyway.
        lines = [
            f"{header_label}: <name> · <N calls> · <in tokens> in / "
            f"<out tokens> out · est $X.XXXX"
        ]
        for name, calls, in_tok, out_tok, cost in rows:
            lines.append(
                f"{name}: {calls} call(s) · {in_tok:,} in / "
                f"{out_tok:,} out · est ${cost:.4f}"
            )
        display.SetValue("\n".join(lines))

    def _render_recent(self, rows):
        if not rows:
            self.recent_display.SetValue("(no calls in this range)")
            return
        # Cap at 100 entries for the on-screen list — older entries
        # are still in the raw file but the dialog isn't meant to be
        # an archive browser.
        capped = rows[:100]
        lines = []
        for r in capped:
            ts = r["ts"].strftime("%Y-%m-%d %H:%M:%S")
            # Prefer real_cost (what OR actually billed) over est_cost
            # (local catalogue estimate) so the per-row figure matches
            # the aggregate at the top of the dialog (audit P18).
            eff = r["real_cost"] if r.get("real_cost") is not None else r.get("cost")
            cost = "—" if eff is None else f"${eff:.4f}"
            lines.append(
                f"{ts} · {r['kin']} · {r['model']} · "
                f"{r['in']:,} in / {r['out']:,} out · {cost} · {r['surface']}"
            )
        if len(rows) > len(capped):
            lines.append(f"\n(... {len(rows) - len(capped)} more in this range — "
                         f"see the raw log for the full list)")
        self.recent_display.SetValue("\n".join(lines))


