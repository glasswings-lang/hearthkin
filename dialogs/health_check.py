"""Speed check — why replies are taking as long as they are.

This exists because of a failure mode that stays invisible for weeks. If the
Ollama machine restarts and a different service wins the race for the port,
every performance setting silently stops applying. Nothing reports it. The
only symptom is replies taking minutes instead of seconds, and the only alarm
that ever goes off is someone getting tired of waiting.

Everything needed to catch it is already in `logs/usage.log` — but nobody has
a reason to read a 1 MB text file, and reading it by eye wouldn't surface the
number that matters anyway. So this reads it and says, in words, what's wrong.

The headline is the **cache hit rate**. When a kin's turn can reuse the
context the machine already processed, the wait before the first word is a
couple of seconds. When it can't, the entire prompt is re-read from scratch —
and on a ~20,000-token prompt that is three to four minutes of silence before
anything at all appears. That single ratio is the difference between a
conversation and a correspondence, and a rate down in the teens is enough to
cause it.

Deliberately a diagnosis, not a dashboard: every section states a verdict in
plain language and names the likely cause, because the person reading it needs
to know what to *do*, and shouldn't have to interpret a table to find out.

Accessibility: the whole report is one read-only multiline TextCtrl, so it can
be reached by Tab and read at whatever pace suits. There's a Copy button
because pasting the report to someone who can help is the most likely next
step, and retyping numbers off a screen is a miserable way to ask for help.
"""

import re
import datetime

import wx

# Ollama reports a prefill rate per call. A cache hit shows up as an enormous
# number (thousands of tokens/sec) because almost nothing had to be processed;
# a genuine cold read runs at the model's real prefill speed, which on a large
# local model is double or low-triple digits. Anything above this is reuse.
_WARM_TPS = 1000

# Below this share of warm turns, something is wrong rather than merely busy.
_WARM_TARGET = 0.40

# Separate patterns rather than one big optional-group regex. A lazy `.*?`
# followed by an OPTIONAL group will happily match the group as zero-width and
# skip it — which silently dropped every prefill figure and took the headline
# with it. Small searches can't fail that way.
_LINE = re.compile(r"^(?P<ts>\S+) kin=(?P<kin>\S+) model=(?P<model>\S+)")
_IN = re.compile(r"\bin=(\d+)")
_PREFILL = re.compile(r"prefill=(\d+)tok/([\d.]+)s\((\d+)tps\)")
_GEN = re.compile(r"gen=(\d+)tok/([\d.]+)s\((\d+)tps\)")
_SURFACE = re.compile(r"surface=(\S+)")


def _parse(path, limit=300):
    """Most recent `limit` calls from usage.log, as dicts. Never raises — a
    diagnostic that crashes on a malformed line is worse than useless, because
    it fails exactly when something is already wrong."""
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return rows
    for line in lines[-limit * 2:]:
        m = _LINE.match(line)
        if not m:
            continue
        d = m.groupdict()
        try:
            d["when"] = datetime.datetime.fromisoformat(d["ts"])
        except Exception:
            continue
        s = _SURFACE.search(line)
        d["surface"] = s.group(1) if s else "unknown"
        i = _IN.search(line)
        d["intok"] = int(i.group(1)) if i else None
        p = _PREFILL.search(line)
        d["ptok"] = int(p.group(1)) if p else None
        d["psec"] = float(p.group(2)) if p else None
        d["ptps"] = int(p.group(3)) if p else None
        g = _GEN.search(line)
        d["gtok"] = int(g.group(1)) if g else None
        d["gsec"] = float(g.group(2)) if g else None
        d["gtps"] = int(g.group(3)) if g else None
        rows.append(d)
    return rows[-limit:]


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals) // 2] if vals else None


def build_report(path, limit=300):
    """The whole report as plain text. Split out from the dialog so it can be
    tested, and so a future scheduled check can post the same words."""
    rows = _parse(path, limit)
    out = []
    add = out.append

    if not rows:
        return ("No usage data yet.\n\n"
                "This fills in once kin have been talking for a while.")

    span_from = rows[0]["when"].strftime("%d %b %H:%M")
    span_to = rows[-1]["when"].strftime("%d %b %H:%M")
    add(f"SPEED CHECK — {len(rows)} calls, {span_from} to {span_to}")
    add("")

    # --- the headline ---------------------------------------------------
    prefilled = [r for r in rows if r["ptps"] is not None]
    warm = [r for r in prefilled if r["ptps"] > _WARM_TPS]
    cold = [r for r in prefilled if r["ptps"] <= _WARM_TPS]
    if prefilled:
        share = len(warm) / len(prefilled)
        cold_secs = _median([r["psec"] for r in cold]) or 0
        warm_secs = _median([r["psec"] for r in warm]) or 0
        if share < _WARM_TARGET:
            add(f"PROBLEM: only {share:.0%} of turns reused context the "
                f"machine had already read.")
            add("")
            add("  When a turn can't reuse it, the whole prompt is processed")
            add("  again from scratch before a single word comes back.")
            add(f"  That is costing about {cold_secs:.0f} seconds of silence")
            add(f"  per turn, against {warm_secs:.0f} seconds when it hits.")
            add("")
            add("  Most likely cause: too few parallel slots on the machine")
            add("  running the model, so each kin's cached context is thrown")
            add("  away by the next kin's turn. Background work counts —")
            add("  heartbeats, scheduled wake-ups and distillation all take a")
            add("  slot while you are typing.")
        else:
            add(f"OK: {share:.0%} of turns reused already-processed context "
                f"({warm_secs:.0f}s vs {cold_secs:.0f}s when they don't).")
        add("")

    # --- what a turn actually costs -------------------------------------
    add("What a turn costs right now")
    if cold:
        add(f"  reading the prompt      {_median([r['psec'] for r in cold]):.0f}s"
            f"   ({_median([r['ptps'] for r in cold])} tokens/sec)")
    if warm:
        add(f"  ...when it's cached     {_median([r['psec'] for r in warm]):.0f}s")
    gen = [r for r in rows if r["gtps"]]
    if gen:
        g_tps = _median([r["gtps"] for r in gen])
        g_sec = _median([r["gsec"] for r in gen])
        add(f"  writing the reply       {g_sec:.0f}s   ({g_tps} tokens/sec)")
        if cold:
            total = (_median([r["psec"] for r in cold]) or 0) + (g_sec or 0)
            add(f"  a typical whole turn    {total/60:.1f} minutes")
    add("")

    # --- where the context is going -------------------------------------
    add("Prompt size per kin  (bigger prompt = longer wait before it speaks)")
    by_kin = {}
    for r in rows:
        by_kin.setdefault(r["kin"], []).append(r["intok"])
    for kin, sizes in sorted(by_kin.items(), key=lambda kv: -(_median(kv[1]) or 0)):
        # A line can be missing any field — a call that errored, an older log
        # format, a truncated write. Skip the number rather than crash: this
        # report is read at the moment something is already wrong, which is
        # the worst possible time for it to be the thing that breaks.
        med = _median(sizes)
        shown = f"{med:>7,}" if med is not None else "      ?"
        add(f"  {kin:<12}{shown} tokens   ({len(sizes)} calls)")
    add("")

    # --- who is competing ------------------------------------------------
    # Which work is competing. This is the section that makes "why was it slow
    # when I was only talking to one kin" answerable: a conversation shares the
    # machine with every heartbeat, scheduled wake-up and distillation run, and
    # each of those evicts somebody's cached context.
    add("What's been using the machine")
    counts = {}
    for r in rows:
        counts[r["surface"]] = counts.get(r["surface"], 0) + 1
    background = 0
    for surface, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = n / len(rows)
        add(f"  {surface:<26}{n:>4} calls  {share:>5.0%}")
        if any(t in surface for t in ("heartbeat", "cron", "distill")):
            background += n
    if background:
        add("")
        add(f"  {background/len(rows):.0%} of all work was background — heartbeats,")
        add("  scheduled wake-ups and memory distillation. Every one of those")
        add("  takes a slot, and takes it while you may be waiting.")
    add("")
    add("Raw data: logs/usage.log")
    return "\n".join(out)


class HealthCheckDialog(wx.Dialog):
    def __init__(self, parent, log_path):
        super().__init__(parent, title="Speed check",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.log_path = log_path

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Label immediately before the field — z-order is where wxMSW/NVDA
        # gets an unlabelled TextCtrl's accessible name from.
        sizer.Add(wx.StaticText(panel, label="&Report:"),
                  flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        self.report_field = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(620, 420))
        sizer.Add(self.report_field, proportion=1,
                  flag=wx.EXPAND | wx.ALL, border=12)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        # Copy first: pasting this to someone who can help is the most likely
        # next step after reading it.
        self.copy_btn = wx.Button(panel, wx.ID_ANY, "Cop&y report")
        self.refresh_btn = wx.Button(panel, wx.ID_ANY, "&Refresh")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "&Close")
        btns.Add(self.copy_btn, flag=wx.RIGHT, border=8)
        btns.Add(self.refresh_btn, flag=wx.RIGHT, border=8)
        btns.AddStretchSpacer()
        btns.Add(self.close_btn)
        sizer.Add(btns, flag=wx.EXPAND | wx.ALL, border=12)

        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda _e: self._refresh())
        self.close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)
        self.SetInitialSize((680, 560))
        self.Layout()
        self._refresh()

    def _refresh(self):
        try:
            text = build_report(self.log_path)
        except Exception as e:
            text = f"Couldn't read the usage log:\n\n{e}"
        self.report_field.SetValue(text)
        self.report_field.SetInsertionPoint(0)
        # Speak only the verdict, not the whole report — the report is right
        # there to be read at whatever pace suits.
        try:
            from audio import nvda_speak
            first = next((ln for ln in text.splitlines()
                          if ln.startswith(("PROBLEM", "OK", "No usage"))), "")
            if first:
                nvda_speak(first)
        except Exception:
            pass

    def _on_copy(self, _event):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(
                    wx.TextDataObject(self.report_field.GetValue()))
                wx.TheClipboard.Close()
                from audio import nvda_speak
                nvda_speak("Report copied")
        except Exception:
            pass

    def _on_close(self, _event):
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
