#!/usr/bin/env python
# SPDX-License-Identifier: CC0-1.0
"""Print what a screen reader would say as you tab through a screen.

    python scripts/narrate_ui.py dialogs/edit_kin.py 317 555
    python scripts/narrate_ui.py hearthkin.pyw --func _build_chat_tab

Why this can work at all: in wxPython, tab order is *widget creation order*,
not sizer order. So the source already contains the running order of the
screen, and a control's announced name is derivable too -- a button's is its
label, a text field's is its SetName or, failing that, the StaticText created
just before it. That's enough to write down the script of what you'd hear.

What it's FOR: hand the output -- and nothing else, no code, no docs -- to a
reader who has never seen this app, and ask what the screen is and what they'd
do. They can't fill in the blanks from context the way the author does, or the
way a user does after years of practice. Whatever confuses them is roughly what
confuses someone on their first day, which is the one perspective nobody on the
project can get back.

This is a reading aid, not an emulator. It does NOT run wx, does NOT run NVDA,
and is wrong in at least these ways:

- It can't know what's hidden. Hide() / Show() at runtime, notebook page
  switches, and enable-state changes are invisible here. Controls hidden at
  construction are marked, but anything toggled later is not.
- Real NVDA verbosity depends on settings, and it announces things this can't
  know (position in group, "1 of 12", value changes, live regions).
- StaticText is not focusable on wxMSW, so it's listed only where it acts as a
  buddy label. A StaticText that names nothing is called out as unreachable --
  that's a finding, not an omission.
- Reading order for a sighted user follows the sizer; a tabbing user follows
  this. Where those disagree, this file is the one that matters and the other
  one is the one people check.

Trust it for order and naming. Don't trust it for state.
"""

import argparse
import ast
import os
import re
import sys

# Controls that take focus when you Tab. StaticText does not.
FOCUSABLE = {
    "wx.Button", "wx.BitmapButton", "wx.ToggleButton",
    "wx.TextCtrl", "wx.ComboBox", "wx.Choice", "wx.ListBox",
    "wx.CheckListBox", "wx.CheckBox", "wx.RadioButton", "wx.Slider",
    "wx.SpinCtrl", "wx.SearchCtrl", "wx.ListCtrl", "wx.TreeCtrl",
    "_IntField",
}

ROLE = {
    "wx.Button": "button",
    "wx.BitmapButton": "button",
    "wx.ToggleButton": "toggle button",
    "wx.TextCtrl": "edit",
    "wx.ComboBox": "combo box",
    "wx.Choice": "combo box",
    "wx.ListBox": "list box",
    "wx.CheckListBox": "check list box",
    "wx.CheckBox": "check box",
    "wx.RadioButton": "radio button",
    "wx.Slider": "slider",
    "wx.SpinCtrl": "spin button",
    "wx.SearchCtrl": "search edit",
    "wx.ListCtrl": "list",
    "wx.TreeCtrl": "tree view",
    "_IntField": "edit",
}


def dotted(node):
    """ast node -> 'wx.Button' / '_IntField' / None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # label=("Room name (fixed...)" if locked else "Room name:") -- a
    # conditional label is a normal wx pattern and both branches are right
    # here in the source. Reporting it as unknowable would hide a real label
    # behind the tool's own laziness.
    if isinstance(node, ast.IfExp):
        a, b = const_str(node.body), const_str(node.orelse)
        if a is not None and b is not None:
            return f"{a}  [or, conditionally]  {b}"
    # "a" "b" implicit concat, or a parenthesised multi-line string
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            else:
                out.append("<...>")
        return "".join(out)
    return None


def kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def strip_mnemonic(text):
    """'Save &memory' -> 'Save memory'. '&&' is a literal ampersand."""
    return re.sub(r"&(.)", r"\1", text)


def style_words(call):
    """Pull TE_READONLY / TE_MULTILINE etc. out of a style= expression."""
    st = kwarg(call, "style")
    if st is None:
        return set()
    src = ast.dump(st)
    found = set()
    for flag in ("TE_READONLY", "TE_MULTILINE", "TE_WORDWRAP",
                 "LB_SINGLE", "CB_READONLY", "TE_PASSWORD", "RB_GROUP"):
        if flag in src:
            found.add(flag)
    return found


# Classes whose third positional arg is a label: wx.Button(parent, id, label).
# wx.TextCtrl's third positional is its *value*, not a label -- don't confuse
# them, or every explainer field gets read as its own name.
POSITIONAL_LABEL = {
    "wx.Button", "wx.BitmapButton", "wx.ToggleButton",
    "wx.StaticText", "wx.CheckBox", "wx.RadioButton",
}


class Widget:
    def __init__(self, line, cls, call):
        self.line = line
        self.cls = cls
        self.call = call
        self.label = None
        self.name = None          # SetName(...)
        self.value = None         # value= for read-only explainer fields
        self.hidden = False
        self.disabled = False
        self.var = None           # assigned variable / attribute name
        # True when a label/name exists but is computed (a variable, an
        # f-string, a loop). The text is unknowable from source, but it is
        # NOT missing -- reporting it as absent is how a linter earns being
        # ignored.
        self.dynamic_label = False
        self.dynamic_name = False
        self.via_helper = False   # built inside a nested helper -> order unknown

        lab = kwarg(call, "label")
        if lab is None and cls in POSITIONAL_LABEL and len(call.args) >= 3:
            lab = call.args[2]     # wx.Button(parent, wx.ID_CLOSE, "&Close")
        if lab is not None:
            self.label = const_str(lab)
            if self.label is None:
                self.dynamic_label = True

        nm = kwarg(call, "name")     # _IntField takes name=
        if nm is not None:
            self.name = const_str(nm)
            if self.name is None:
                self.dynamic_name = True

        val = kwarg(call, "value")
        if val is not None:
            self.value = const_str(val)
        self.styles = style_words(call)

    @property
    def focusable(self):
        return self.cls in FOCUSABLE

    @property
    def role(self):
        r = ROLE.get(self.cls, "?")
        if self.cls == "wx.TextCtrl":
            bits = []
            if "TE_MULTILINE" in self.styles:
                bits.append("multi line")
            if "TE_READONLY" in self.styles:
                bits.append("read only")
            if bits:
                return "edit, " + ", ".join(bits)
        return r


def enclosing_function(funcs, line):
    """Innermost function containing this line, or '<module>'."""
    best = _enclosing(funcs, line)
    return best[1] if best else "<module>"


def enclosing_function_id(funcs, line):
    """Innermost function's start line -- a stable unique id for that function.

    Not its NAME: half the classes in dialogs/ define `__init__`, so scoping a
    variable by name puts every dialog's locals in one bucket. A probe with two
    classes both having `__init__` and both with a local `intro` caught this
    the first time round, when scoping-by-name looked like it worked because
    the file I tested happened to use two differently-named methods.
    """
    best = _enclosing(funcs, line)
    return best[0] if best else 0


def _enclosing(funcs, line):
    best = None
    for start, end, name in funcs:
        if start <= line <= end and (best is None or start > best[0]):
            best = (start, name)
    return best


def outermost_function(funcs, line):
    """The OUTERMOST function containing this line -- i.e. the builder method,
    not a helper nested inside it.

    This is the right unit for "are these two widgets on the same screen".
    A dialog that builds rows through a local `def row(label, make_field)` and
    a `lambda: _IntField(...)` creates the label inside `row` and the field
    inside the lambda -- two different functions, one runtime sequence, and
    the label really is the field's buddy. Keying on the INNERMOST function
    severed exactly those pairs and reported four properly-labelled fields in
    tool_settings.py as unnamed. Their own code comment explains they order
    label-before-field deliberately for this reason; the tool called it broken.

    Sibling methods (_build_chat_tab vs _build_prefs_tab) have no common
    enclosing function, so they still separate -- which is the leak this was
    added to stop.
    """
    best = None
    for start, end, name in funcs:
        if start <= line <= end and (best is None or start < best[0]):
            best = (start, name)
    return best[1] if best else "<module>"


def collect(tree, lo, hi, funcs):
    """Widgets in creation order, plus SetName/Hide/Disable applied after.

    Walks every construction CALL, not just assignments. A widget built inline
    inside a sizer add -- `outer.Add(wx.StaticText(panel, label="&Source:"))` --
    is constructed exactly when that line runs, so it takes its place in tab
    order like any other, and as a StaticText it's a buddy label for whatever
    comes next. Collecting only assignments missed those entirely and then
    reported the fields they name as unnamed: 40-odd false alarms in one sweep.

    Variables are tracked per enclosing FUNCTION, not per file. Two methods
    each with a local named `intro` are two different widgets; keying them by
    bare name let the second one's SetName land on the first, and the first was
    then reported unnamed while its real SetName sat three lines below it
    (model_browser.py's two `intro` locals). Anything with `self.` is
    file-scoped for real, so it keeps the bare name.
    """
    widgets = []
    by_var = {}

    def scope_key(var, line):
        if var.startswith("self."):
            return var          # genuinely one widget per class attribute
        return (enclosing_function_id(funcs, line), var)

    # Which construction calls are the value of an assignment, so a widget can
    # be given its variable name (needed to attach later SetName/Hide/Disable).
    var_of_call = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        t = node.targets[0]
        if isinstance(t, ast.Name):
            var_of_call[id(node.value)] = t.id
        elif isinstance(t, ast.Attribute):
            # The receiver's real name, not a hardcoded "self." -- an attribute
            # assigned as `s.field = ...` was being filed under "self.field"
            # while its later `s.field.SetName(...)` looked up "s.field", so
            # the name silently never attached. Every dialog here happens to
            # use `self`, which is exactly why this would have sat unnoticed.
            var_of_call[id(node.value)] = dotted(t) or ("self." + t.attr)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not (lo <= node.lineno <= hi):
            continue
        cls = dotted(node.func)
        if cls not in ROLE and cls != "wx.StaticText":
            continue
        w = Widget(node.lineno, cls, node)
        w.var = var_of_call.get(id(node))
        # Built inside a helper nested in the builder (a `def row(...)` or a
        # `lambda: _IntField(...)`)? Then this ONE source line is N widgets at
        # runtime, and sorting by line number cannot reconstruct the order --
        # tool_settings.py builds four rows through one `row()` helper, so all
        # four labels share line 45 and land in a clump nowhere near their
        # fields. Order for such a screen is unknowable from source. Flagged so
        # the report can say so instead of inventing a sequence.
        w.via_helper = (funcs and outermost_function(funcs, node.lineno)
                        != enclosing_function(funcs, node.lineno))
        widgets.append(w)
        if w.var:
            by_var[scope_key(w.var, node.lineno)] = w

    widgets.sort(key=lambda w: w.line)

    # post-construction calls
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and lo <= node.lineno <= hi):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        target = dotted(f.value)
        if not target:
            continue
        key = scope_key(target, node.lineno)
        if key not in by_var:
            continue
        w = by_var[key]
        if f.attr == "SetName" and node.args:
            s = const_str(node.args[0])
            if s:
                w.name = s
            else:
                # A computed SetName -- `display.SetName(label_text.rstrip(":"))`.
                # The text is unknowable here, but the control IS named, and
                # that distinction is load-bearing: falling through to the
                # preceding StaticText used to invent a name the user never
                # hears AND hide the fact that the StaticText reaches nobody.
                # It concealed the Connections privacy/cost paragraph
                # completely -- the tool reported that text as a field's name,
                # so the screen looked fine while the text was unreachable.
                w.dynamic_name = True
        elif f.attr == "Hide":
            w.hidden = True
        elif f.attr == "Disable":
            w.disabled = True
        elif f.attr in ("SetLabel", "SetLabelText") and node.args:
            s = const_str(node.args[0])
            if s:
                w.label = s
    return widgets


def narrate(widgets, funcs=()):
    """Yield (line, spoken, notes) in tab order, plus findings."""
    out = []
    findings = []
    pending_label = None      # last StaticText, candidate buddy label
    pending_func = None

    for w in widgets:
        # A buddy label can't reach across builders. Treating the file as one
        # flat stream had a StaticText in _build_prefs_tab blamed for stealing
        # the buddy slot of a control in _build_chat_tab -- different screens,
        # built at different times, never adjacent to anything. The findings
        # happened to be true and the stated reasons were nonsense, which is
        # its own kind of lie: it sends the next reader to the wrong line.
        this_func = outermost_function(funcs, w.line) if funcs else None
        if funcs and pending_label is not None and this_func != pending_func:
            pending_label = None
        pending_func = this_func
        if w.cls == "wx.StaticText":
            # Two StaticTexts before one field: the NEAREST wins the buddy
            # slot and the earlier one names nothing. This is the quietest
            # version of the bug -- hint text sitting above a "Label:" line
            # reads perfectly on screen and is announced to no one -- and it
            # hid at least five real losses (both "how to find your Telegram
            # ID" hints, cron's retry note, an Ollama address hint) while this
            # check didn't exist. A clean run isn't proof of a clean screen if
            # the tool can't see this shape.
            if pending_label and not pending_label.dynamic_label:
                findings.append(
                    f"line {pending_label.line}: StaticText "
                    f"{strip_mnemonic(pending_label.label)!r} is never "
                    f"announced -- another StaticText "
                    f"({strip_mnemonic(w.label)!r}, line {w.line}) is created "
                    f"before the next control, and a field's buddy label is "
                    f"the NEAREST preceding StaticText, so this one labels "
                    f"nothing. If it's explanatory, the house pattern is a "
                    f"read-only wx.TextCtrl (tab-reachable)."
                    if w.label else
                    f"line {pending_label.line}: StaticText "
                    f"{strip_mnemonic(pending_label.label)!r} is never "
                    f"announced -- a later StaticText (line {w.line}) takes "
                    f"the buddy slot before the next control")
            if w.label or w.dynamic_label:
                pending_label = w
            continue

        if not w.focusable:
            pending_label = None
            continue

        notes = []
        # Controls that carry their own label. On wxMSW the visible label IS
        # the accessible name for these, so a StaticText placed before one
        # labels nothing at all.
        SELF_LABELLING = ("wx.Button", "wx.BitmapButton", "wx.CheckBox",
                          "wx.RadioButton", "wx.ToggleButton")
        # How the name is resolved, in the order wxMSW/NVDA resolves it.
        if w.cls in SELF_LABELLING:
            if w.dynamic_label:
                spoken = "<computed at runtime>"
            else:
                spoken = strip_mnemonic(w.label or "")
            if w.name and w.label:
                notes.append(
                    f"SetName({w.name!r}) is IGNORED on a {ROLE[w.cls]} -- "
                    f"the visible label is the accessible name on wxMSW")
            if not spoken:
                findings.append(f"line {w.line}: {w.cls} with no label -- "
                                f"NVDA will announce it as blank")
            used_buddy = False
        else:
            if w.dynamic_name:
                spoken = "<computed at runtime>"
                used_buddy = False
            elif w.name:
                spoken = w.name
                used_buddy = False
            elif pending_label:
                spoken = ("<computed at runtime>" if pending_label.dynamic_label
                          else strip_mnemonic(pending_label.label))
                used_buddy = True
            else:
                spoken = ""
                used_buddy = False
                if w.via_helper:
                    # Its label is almost certainly built by the same helper,
                    # on a source line that sorts elsewhere. Claiming "unnamed"
                    # here would be the tool's blind spot dressed up as a bug
                    # in the app -- and this exact shape had it accusing four
                    # correctly-labelled fields whose own comment explains they
                    # order label-before-field on purpose.
                    notes.append(
                        "name not resolvable: built via a helper, so its label "
                        "may exist on a source line that sorts elsewhere")
                else:
                    findings.append(
                        f"line {w.line}: {w.cls} has no SetName and no "
                        f"StaticText before it -- NVDA announces this as "
                        f"unnamed")

        # A StaticText that was skipped over: its text reaches nobody who
        # tabs. Two ways this happens, and they need different advice.
        if pending_label and not used_buddy and not pending_label.dynamic_label:
            said = strip_mnemonic(pending_label.label)
            # Does the control's own name already carry the text? A "(0 = off)"
            # dropped from the label is real information loss; "Distillation
            # model:" vs SetName "Distillation model" is not.
            # "%" on screen vs "percent" in the SetName is the same word to a
            # listener, so don't report it as lost text. Normalise the few
            # symbols that get spelled out rather than read as symbols.
            def _norm(s):
                s = s.rstrip(":").strip().lower()
                for sym, word in (("%", "percent"), ("&", "and")):
                    s = s.replace(sym, word)
                return " ".join(s.split())
            carried = _norm(w.name or spoken or "")
            lost = _norm(said)
            # Word-subset, not prefix. "Name:" vs SetName "Kin name" carries
            # every word and loses nothing, but a prefix test calls that a loss
            # because the words sit in the other order. Findings that are
            # plainly wrong to a reader are how a tool teaches people to skim
            # past the ones that aren't.
            cw = set(re.findall(r"[a-z0-9]+", carried))
            lw = set(re.findall(r"[a-z0-9]+", lost))
            missing = lw - cw
            near = bool(cw) and (not missing or not (cw - lw))
            if w.cls in SELF_LABELLING:
                findings.append(
                    f"line {pending_label.line}: StaticText {said!r} is never "
                    f"announced -- the next control is a {ROLE[w.cls]} "
                    f"({w.var or w.cls}, line {w.line}), which uses its own "
                    f"label as its name, so this text labels nothing and a "
                    f"tabbing user never hears it. If it's explanatory, the "
                    f"house pattern is a read-only wx.TextCtrl (tab-reachable).")
            elif missing and near:
                findings.append(
                    f"line {pending_label.line}: StaticText {said!r} is never "
                    f"announced; {w.var or w.cls} says {carried!r} instead -- "
                    f"INFORMATION LOST: {sorted(missing)} "
                    f"reaches sighted users only.")
            elif not near:
                findings.append(
                    f"line {pending_label.line}: StaticText {said!r} is never "
                    f"announced -- the next control ({w.var or w.cls}, line "
                    f"{w.line}) has its own SetName ({carried!r}), so this "
                    f"text is unreachable by keyboard")

        state = []
        if w.cls == "wx.CheckBox":
            state.append("not checked")
        if w.disabled:
            state.append("unavailable")
        if w.hidden:
            state.append("HIDDEN at construction")

        line = f"{spoken}, {w.role}"
        if state:
            line += ", " + ", ".join(state)
        out.append((w.line, line, notes, w))
        pending_label = None

    # trailing StaticText with nothing after it
    if pending_label and not pending_label.dynamic_label:
        findings.append(
            f"line {pending_label.line}: StaticText "
            f"{strip_mnemonic(pending_label.label)!r} is the last thing built "
            f"-- it labels nothing and is never announced")
    return out, findings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("start", nargs="?", type=int, default=1)
    ap.add_argument("end", nargs="?", type=int, default=10**9)
    ap.add_argument("--func", help="narrate this function instead of a line range")
    ap.add_argument("--plain", action="store_true",
                    help="print only the spoken script (what you'd hand a fresh reader)")
    args = ap.parse_args()

    # These labels are full of em-dashes and ellipses; a Windows console
    # defaults to cp1252 and would mojibake the very text under review.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = args.file if os.path.isabs(args.file) else os.path.join(root, args.file)
    tree = ast.parse(open(path, encoding="utf-8").read())

    # Function spans, so widget variables can be scoped to the method that
    # built them (two methods each with a local `intro` are two widgets).
    #
    # Lambdas count. `row("Tool history kept:", lambda: _IntField(...))` builds
    # a real widget inside a lambda, and without lambdas in this list its
    # innermost and outermost enclosing function both resolve to the method --
    # so it doesn't look helper-built, and the tool goes back to confidently
    # reporting it unnamed.
    funcs = [
        (n.lineno, n.end_lineno, getattr(n, "name", "<lambda>"))
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]

    lo, hi = args.start, args.end
    if args.func:
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name == args.func:
                lo, hi = n.lineno, n.end_lineno
                break
        else:
            print(f"no function named {args.func!r} in {args.file}")
            return 1

    widgets = collect(tree, lo, hi, funcs)
    spoken, findings = narrate(widgets, funcs)

    helper_built = sum(1 for w in widgets if getattr(w, "via_helper", False))
    if helper_built:
        print(f"!! ORDER IS UNRELIABLE FOR THIS SCREEN. {helper_built} of "
              f"{len(widgets)} widgets are built inside a helper function or "
              f"lambda.\n!! A helper called N times is ONE source line, and "
              f"this tool orders by source line, so it cannot reconstruct the "
              f"real\n!! sequence. Read the order below as a guess. Naming "
              f"findings for helper-built widgets are suppressed.\n")

    if args.plain:
        print(f"Tabbing through this screen, in order, you hear:\n")
        for i, (_line, text, _notes, _w) in enumerate(spoken, 1):
            print(f"{i:2}. {text}")
        return 0

    print(f"# {args.file} lines {lo}-{hi}")
    print(f"# {len(spoken)} tab stops\n")
    print("TAB ORDER (what NVDA announces):\n")
    for i, (line, text, notes, w) in enumerate(spoken, 1):
        print(f"{i:2}. {text}")
        print(f"    ({args.file}:{line})")
        for n in notes:
            print(f"    ! {n}")
    if findings:
        print(f"\nFINDINGS ({len(findings)}):\n")
        for f in findings:
            print(f"  - {f}")
    else:
        print("\nFINDINGS: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
