#!/usr/bin/env python
"""audit_ui — ask WINDOWS what a screen reader announces, instead of guessing.

    python scripts/audit_ui.py                 # audit every registered screen
    python scripts/audit_ui.py sound_cues      # one screen
    python scripts/audit_ui.py --self-test     # prove the detector fires

WHY THIS EXISTS, AND WHY narrate_ui.py IS NOT ENOUGH

`scripts/narrate_ui.py` reads source with an AST and infers what NVDA would
say. Inference was the best available answer at the time and it found real
bugs, but it is wrong in two directions that matter, and both were demonstrated
on 2026-07-27:

  * IT FALSELY CLEARS. It reported "FINDINGS: none" on a dialog that hid two
    controls depending on state, because static source cannot know runtime
    Show/Hide. It also cannot know that a method was renamed and three call
    sites left behind — running the dialog raised AttributeError immediately.

  * ITS MODEL OF NAMING IS WRONG. It treats `SetName()` as the name a screen
    reader announces. Measured against oleacc on this machine:

        TextCtrl + SetName("X")            -> NO ACCESSIBLE NAME AT ALL
        TextCtrl(name="X") in constructor  -> NO ACCESSIBLE NAME AT ALL
        TextCtrl after a StaticText        -> the StaticText's text
        StaticText + SetName("X")          -> the StaticText wins
        Button(label="V") + SetName("X")   -> "V" wins
        Choice + SetName("X"), no label    -> NO ACCESSIBLE NAME AT ALL

    So `SetName` is decorative on wxMSW. It sets wxWidgets' internal window
    identifier, which is not the MSAA name. Every report that printed a
    SetName value as "what NVDA announces" was fiction — and confident
    fiction is worse than a gap, because a gap gets investigated.

    This matches the documented OLEACC rule: for an unnamed control it takes
    the IMMEDIATELY preceding sibling in child-window order, and uses it only
    if it is a static text. In wxWidgets, child order is creation order —
    which is why "create the label before the field" is the house rule, and
    why a label separated from its field by any other control silently stops
    working.

WHAT THIS TOOL DOES INSTEAD

Builds the real dialog, walks the real widget tree, and asks oleacc for each
control's accessible name — the same interface a screen reader reads. No
inference. Where it says a control has no name, Windows genuinely offers none
and the listener hears a bare role: "edit".

It can also drive a screen through several states and check the tab order does
not change shape between them, because tab order is how these screens are
read: a control that appears and vanishes rearranges the map mid-task and
nothing announces that it moved.

WHAT IT STILL CANNOT TELL YOU

Whether the words are any good. "Edit room" was a correctly-named control
whose noun was wrong, and no amount of introspection catches that. Hand the
output to someone with no knowledge of the app and ask them what they think
each control does — their confusion is the instrument.
"""

import argparse
import ctypes
import os
import sys
from ctypes import POINTER, byref, c_void_p, wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Never touch real kin state just to look at a dialog.
os.environ.setdefault("HEARTHKIN_HOME",
                      os.path.join(os.environ.get("TEMP", "."), "hk-audit-ui"))

_oleacc = ctypes.windll.oleacc if os.name == "nt" else None


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


_IID_IAccessible = _GUID(
    0x618736e0, 0x3c3d, 0x11cf,
    (ctypes.c_ubyte * 8)(0x81, 0x0c, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71))
_OBJID_CLIENT = -4


class _VARIANT_I4(ctypes.Structure):
    """Just enough VARIANT to carry CHILDID_SELF."""
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD),
                ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                ("lVal", ctypes.c_long), ("pad", ctypes.c_long)]


# IAccessible vtable: IUnknown 0-2, IDispatch 3-6, accParent 7,
# accChildCount 8, accChild 9, accName 10, accValue 11, accDescription 12,
# accRole 13.
_SLOT_NAME = 10
_SLOT_ROLE = 13


def accessible_name(hwnd):
    """The name Windows exposes for this window, or None when there is none.

    None is a finding rather than an error: a control with no accessible name
    is announced by role alone, which tells the listener nothing about what it
    is for.
    """
    if _oleacc is None:
        return None
    pacc = c_void_p()
    hr = _oleacc.AccessibleObjectFromWindow(
        wintypes.HWND(hwnd), ctypes.c_long(_OBJID_CLIENT),
        byref(_IID_IAccessible), byref(pacc))
    if hr != 0 or not pacc:
        return None
    vt = ctypes.cast(pacc, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, _VARIANT_I4,
                               POINTER(ctypes.c_wchar_p))
    v = _VARIANT_I4()
    v.vt = 3          # VT_I4
    v.lVal = 0        # CHILDID_SELF
    out = ctypes.c_wchar_p()
    hr = proto(vt[_SLOT_NAME])(pacc, v, byref(out))
    name = out.value if hr == 0 else None
    ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)(vt[2])(pacc)   # Release
    return name or None


# ── which screens can be built, and how ─────────────────────────────────────
#
# An explicit registry rather than introspection. Guessing constructor
# arguments works for about half of them and fails silently for the rest,
# which is the exact failure mode this tool exists to replace.

def _registry():
    from dialogs.import_history import ImportHistoryDialog
    from dialogs.sound_cues import SoundCuesDialog
    from dialogs.confirm_close import ConfirmCloseDialog
    from dialogs.health_check import HealthCheckDialog

    return {
        "import_history": {
            "build": lambda: ImportHistoryDialog(None),
            # (label, callable) pairs -- each puts the screen in a state a
            # person can reach, so tab-order stability is checked for real.
            "states": [
                ("several files", lambda d: d._set_sources(["a.txt", "b.txt"])),
                ("one file", lambda d: d._set_sources(["only.txt"])),
                ("no files", lambda d: d._set_sources([])),
            ],
        },
        "sound_cues": {
            "build": lambda: SoundCuesDialog(
                None, {"reply_chime": True}, lambda: None),
        },
        "confirm_close": {
            "build": lambda: ConfirmCloseDialog(
                None, ["Opal is part-way through a reply in the main window"]),
        },
        "health_check": {
            "build": lambda: HealthCheckDialog(None, "nonexistent.log"),
        },
    }


# wx gives every control a default name. `TextCtrl.GetName()` is "text" unless
# someone set one, `Choice.GetName()` is "choice", and so on. Reporting those as
# "a SetName that is being ignored" is noise about code nobody wrote, and a
# checker that cries wolf gets ignored on the day it is right.
_WX_DEFAULT_NAMES = {
    "text", "choice", "listbox", "checklistbox", "combobox", "panel", "button",
    "radiobutton", "checkbox", "statictext", "dialog", "frame", "notebook",
    "listctrl", "slider", "gauge", "scrolledpanel", "staticbox",
    "check", "radio", "message", "group", "spin",
}


def _is_real_setname(widget):
    name = (widget.GetName() or "").strip()
    if not name:
        return False
    return name.lower() not in _WX_DEFAULT_NAMES


def _heard_via_value(widget):
    """True when a control has no name but DOES expose text as its value.

    An unnamed read-only TextCtrl holding a paragraph is not silent: Windows
    exposes the text as accValue and a screen reader speaks it. That is the
    house "read-only field as a header" pattern and it works. Flagging it
    identically to an empty unnamed input -- which really does announce as a
    bare "edit" with nothing to say -- makes the report useless for telling
    the two apart, and the second one is the only real barrier.
    """
    import wx
    return isinstance(widget, wx.TextCtrl) and bool((widget.GetValue() or "").strip())


def _unreachable_readonly(dlg):
    """Read-only text that Tab cannot reach.

    A SINGLE-LINE read-only wx.TextCtrl is not keyboard-focusable on wxMSW --
    wx refuses it focus because there is nothing to scroll. The house pattern
    is "use a read-only TextCtrl rather than a StaticText so the text lands in
    tab order", and it only delivers that when the control is multiline.

    Reported as a NOTE rather than a finding, because it is sometimes exactly
    right: a field that updates live is deliberately kept single-line so that
    focus can never be inside it when it repaints. Repainting a multiline
    TextCtrl resets its caret to the top and throws a screen reader back to
    the first line. Only a person who knows whether that text changes can say
    which case a given field is.
    """
    import wx
    out = []
    for c in _all_children(dlg):
        if not isinstance(c, wx.TextCtrl) or c.IsEditable():
            continue
        if c.CanAcceptFocusFromKeyboard():
            continue
        text = (c.GetValue() or "").strip()
        if text:
            out.append(text.replace("\n", " ")[:70])
    return out


def _focusable(dlg):
    """Every control that can genuinely take keyboard focus, in child order.

    Child order is what OLEACC's label lookup walks, and in wxWidgets it is
    creation order — so this is also the order a Tab key visits.
    """
    out = []

    def walk(w):
        for c in w.GetChildren():
            if c.CanAcceptFocusFromKeyboard():
                out.append(c)
            walk(c)
    walk(dlg)
    return out


def _all_children(dlg):
    out = []

    def walk(w):
        for c in w.GetChildren():
            out.append(c)
            walk(c)
    walk(dlg)
    return out


def audit(name, spec, plain=False):
    import wx
    problems = []
    dlg = spec["build"]()
    try:
        children = _all_children(dlg)
        stops = _focusable(dlg)

        print(f"\n{'=' * 68}\n{name}\n{'=' * 68}")
        if plain:
            print("Tabbing through this screen, in order, you hear:\n")
        else:
            print(f"{len(stops)} tab stops. What Windows reports for each:\n")

        for i, c in enumerate(stops, 1):
            acc = accessible_name(c.GetHandle())
            role = type(c).__name__
            if acc:
                print(f" {i:>2}. {acc}   [{role}]")
                continue
            if _heard_via_value(c):
                # Unnamed, but its own text is the value, so it IS spoken.
                # Working as intended; say so rather than counting it wrong.
                shown = (c.GetValue() or "").replace("\n", " ")[:52]
                print(f" {i:>2}. (no name, heard as its text: {shown!r})   [{role}]")
                continue
            print(f" {i:>2}. (NO NAME, AND NOTHING TO SAY)   [{role}]")
            extra = ""
            if _is_real_setname(c):
                extra = (f" It has SetName({c.GetName()!r}), which does nothing "
                         f"on wxMSW.")
            problems.append(
                f"{role} has no accessible name and no text — announced as a "
                f"bare role, so there is no way to learn what it is for.{extra} "
                f"Give it a StaticText created IMMEDIATELY before it."
            )

        # A real SetName that is being relied on and doing nothing.
        for c in stops:
            if not _is_real_setname(c):
                continue
            sn = c.GetName()
            acc = accessible_name(c.GetHandle())
            if acc and acc.strip() != sn.strip():
                problems.append(
                    f"{type(c).__name__}: SetName({sn!r}) is ignored — Windows "
                    f"announces {acc!r} instead. Anything that lives only in "
                    f"the SetName never reaches the listener."
                )

        # Read-only text Tab cannot reach. A note, not a finding — see the
        # docstring on _unreachable_readonly for why only a person can judge it.
        unreachable = _unreachable_readonly(dlg)
        if unreachable:
            print(f"\n NOTE — read-only text Tab cannot reach ({len(unreachable)}):")
            print("   Single-line read-only fields are not keyboard-focusable on")
            print("   wxMSW. Correct if the text updates live (focus can never be")
            print("   inside it when it repaints); a gap if it just sits there.")
            for t in unreachable:
                print(f"     {t!r}")

        # A StaticText is never focusable, so the ONLY way it reaches a screen
        # reader is by becoming the accessible name of the control after it. If
        # that control already has its own name -- a button, checkbox or radio
        # carries its label -- or is another StaticText, then the text is on
        # screen and silent. Reported as a note rather than a finding: a purely
        # decorative caption beside something already named is a legitimate
        # choice, and only a person can say which is which. The fix, where it
        # matters, is the house pattern -- a read-only multiline TextCtrl,
        # which IS focusable and whose text is spoken as its value.
        silent_text = []
        for parent in {c.GetParent() for c in children}:
            kids = list(parent.GetChildren())
            for j, c in enumerate(kids):
                if not isinstance(c, wx.StaticText):
                    continue
                label = strip_mn(c.GetLabel()).strip()
                if not label:
                    continue
                nxt = kids[j + 1] if j + 1 < len(kids) else None
                if nxt is None:
                    silent_text.append((label, "nothing follows it"))
                    continue
                nxt_acc = accessible_name(nxt.GetHandle())
                if isinstance(nxt, wx.StaticText):
                    silent_text.append((label, "another StaticText follows it"))
                elif nxt_acc and nxt_acc.strip() != label:
                    silent_text.append(
                        (label, f"the next control is already named "
                                f"{nxt_acc.strip()!r}"))
        if silent_text:
            print(f"\n NOTE — StaticText that is never announced "
                  f"({len(silent_text)}):")
            print("   On screen but silent: a StaticText only reaches a screen")
            print("   reader by naming the control after it. Fine for decoration,")
            print("   a gap if it is explanatory text someone needs.")
            for label, why in silent_text:
                print(f"     {label[:64]!r} — {why}")

        # Tab order must not change shape between states.
        states = spec.get("states") or []
        if states:
            baseline = [type(c).__name__ for c in _focusable(dlg)]
            print(f"\n Tab-order stability across {len(states)} states:")
            for label, drive in states:
                try:
                    drive(dlg)
                except Exception as e:
                    problems.append(f"driving to state {label!r} raised {e!r}")
                    continue
                now = [type(c).__name__ for c in _focusable(dlg)]
                same = now == baseline
                print(f"   {label:<18}{len(now):>3} stops   "
                      f"{'unchanged' if same else 'CHANGED'}")
                if not same:
                    problems.append(
                        f"tab order changes shape in state {label!r} "
                        f"({len(baseline)} -> {len(now)} stops). A control that "
                        f"appears or vanishes rearranges the map mid-task and "
                        f"nothing announces that it moved."
                    )
    finally:
        dlg.Destroy()

    if problems:
        print(f"\n FINDINGS ({len(problems)}):")
        for p in dict.fromkeys(problems):
            print(f"   - {p}")
    else:
        print("\n FINDINGS: none")
    return problems


def strip_mn(text):
    return (text or "").replace("&", "")


def self_test():
    """Prove the detector fires on known-bad screens before trusting a clean
    report from it. A "FINDINGS: none" from a checker nobody has shown a real
    fault to is not evidence — that is how the previous tool cleared a dialog
    that hid two controls.
    """
    import wx
    app = wx.App()
    fails = []

    def one(label, build, expect):
        f = wx.Frame(None)
        p = wx.Panel(f)
        build(p)
        found = []
        for c in p.GetChildren():
            if c.CanAcceptFocusFromKeyboard() and not accessible_name(c.GetHandle()):
                found.append(type(c).__name__)
        got = bool(found)
        ok = got == expect
        print(f"  {'PASS' if ok else 'FAIL'} {label}"
              f"  (unnamed: {found or 'none'})")
        if not ok:
            fails.append(label)
        f.Destroy()

    print("positive controls — the detector must SEE these:")
    one("a TextCtrl named only by SetName is caught",
        lambda p: [wx.TextCtrl(p).SetName("invisible to NVDA")], True)
    one("a Choice named only by SetName is caught",
        lambda p: [wx.Choice(p, choices=["a"]).SetName("also invisible")], True)
    one("a label separated from its field by another control is caught",
        lambda p: [wx.StaticText(p, label="Label:"), wx.Button(p, label="B"),
                   wx.TextCtrl(p)], True)

    print("\nnegative controls — the detector must NOT flag these:")
    one("a StaticText immediately before its field is fine",
        lambda p: [wx.StaticText(p, label="Good label:"), wx.TextCtrl(p)], False)
    one("a Button carries its own visible label",
        lambda p: [wx.Button(p, label="Do the thing")], False)

    # The classification checks. These are what stop the report crying wolf,
    # and a report that cries wolf is one nobody reads on the day it is right.
    print("\nclassification — the detector must tell these apart:")
    _RO = wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_NO_VSCROLL

    f = wx.Frame(None)
    p = wx.Panel(f)
    header = wx.TextCtrl(p, value="Target kin (where history lands):", style=_RO)
    empty_input = wx.TextCtrl(p)
    plain = wx.TextCtrl(p)
    named = wx.TextCtrl(p)
    named.SetName("a name someone actually set")

    checks = [
        ("an unnamed read-only header counts as heard, not silent",
         _heard_via_value(header), True),
        ("an unnamed EMPTY input does not count as heard",
         _heard_via_value(empty_input), False),
        ("wx's default name 'text' is not mistaken for a real SetName",
         _is_real_setname(plain), False),
        ("a name someone really set IS recognised",
         _is_real_setname(named), True),
    ]
    for label, got, expect in checks:
        ok = got == expect
        print(f"  {'PASS' if ok else 'FAIL'} {label}")
        if not ok:
            fails.append(label)

    # And the tab-reachability note must fire on a single-line read-only field
    # while staying quiet about the multiline one beside it.
    f2 = wx.Frame(None)
    p2 = wx.Panel(f2)
    wx.TextCtrl(p2, value="Ready.", style=wx.TE_READONLY)          # unreachable
    wx.TextCtrl(p2, value="reachable because multiline", style=_RO)
    found = _unreachable_readonly(f2)
    ok = found == ["Ready."]
    print(f"  {'PASS' if ok else 'FAIL'} single-line read-only is reported "
          f"unreachable, multiline is not  (found: {found})")
    if not ok:
        fails.append("unreachable-readonly detection")

    f.Destroy()
    f2.Destroy()
    app.Destroy()
    print()
    if fails:
        print(f"SELF-TEST FAILED: {', '.join(fails)}")
        return 1
    print("SELF-TEST PASSED — the detector fires on real faults and stays "
          "quiet on correct code.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("screen", nargs="?", help="one registered screen, or all")
    ap.add_argument("--plain", action="store_true",
                    help="prose form, for handing to someone unfamiliar with "
                         "the app — their confusion is the instrument")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the detector fires before trusting it")
    args = ap.parse_args()

    if os.name != "nt":
        print("This tool reads Windows accessibility APIs; nothing to do here.")
        return 0
    if args.self_test:
        return self_test()

    import wx
    app = wx.App()
    reg = _registry()
    names = [args.screen] if args.screen else sorted(reg)
    total = 0
    for n in names:
        if n not in reg:
            print(f"unknown screen {n!r}. known: {', '.join(sorted(reg))}")
            return 2
        total += len(audit(n, reg[n], plain=args.plain))
    app.Destroy()
    print(f"\n{'=' * 68}\n{total} finding(s) across {len(names)} screen(s).")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
