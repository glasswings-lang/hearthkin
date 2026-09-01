# SPDX-License-Identifier: CC0-1.0
"""Guard test: no control in the main window claims a keyboard shortcut the
menu bar has already taken.

On Windows the menu bar wins Alt+<letter>, always. So a button or radio in the
main window labelled with "&K" while the menu bar has a "&Kin" menu does not
get a shortcut that merely *conflicts* — it gets no shortcut at all, forever.
The menu opens instead. The label goes on advertising a key that has never once
fired, and a keyboard user learns it, presses it, lands in a menu they didn't
ask for, and has to Escape back out. For an NVDA user that's a derail, not a
cosmetic glitch.

This is not hypothetical and it is not a rule nobody knew. On 2026-07-17 the
main window had three of these:

    "Talk with a &kin"  Alt+K  ->  swallowed by the "&Kin" menu
    "Talk in a &room"   Alt+R  ->  swallowed by the "&Room" menu
    "&Talk"             Alt+T  ->  swallowed by the "&Tools" menu

...while the Take photo button, fifty lines above the Talk button, carried a
comment stating the rule exactly: "NOT Alt+T (which conflicts with the Tools
menu -- menu wins, the button mnemonic would be dead)." The rule was known,
written down, and broken in the same file. CLAUDE.md meanwhile recorded an
"Alt+R three-way conflict resolved" in which two working mnemonics were moved
aside to protect the room radio's Alt+R -- a key it could never receive.

That is what this test is for. A comment cannot fail; a test can. Knowing the
rule demonstrably wasn't enough.

Scope, and why it's drawn here:

- Only the MAIN WINDOW's own controls are checked, because only they sit under
  the menu bar. Dialogs (Preferences, per-kin settings, the model browser) have
  no menu bar of their own, so their mnemonics can't be shadowed by one -- a
  dialog is free to use Alt+K.
- So each function that builds mnemonic labels must be classified as building
  frame widgets or dialog widgets. A NEW builder that's in neither list fails
  this test on purpose: whoever adds it has to say which it is, and that's a
  ten-second decision at authoring time versus a dead key nobody notices for
  a year.

What this does NOT check (be honest about the blind spots):

- Collisions *among* the main window's own controls. Several are legitimate --
  "&New kin" and "Co&ntinue round" both claim Alt+N but are never visible at
  the same time (kin mode vs room mode). Deciding that from source means
  knowing which panel is showing, which this test can't do. Those are still
  worth auditing by hand.
- Anything inside a dialog, for the same reason.
- Whether a mnemonic is *good* -- only whether it can physically fire.

Run:  python tests/test_mnemonics.py
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FRAME_FILE = os.path.join(ROOT, "hearthkin.pyw")

# Functions whose widgets are children of the main frame, and therefore live
# under the menu bar. These are the ones that can be shadowed.
FRAME_BUILDERS = {
    "_build_header",
    "_build_chat_tab",
}

# Functions whose widgets belong to a dialog. A dialog has no menu bar, so
# Alt+K there is nobody else's. Listed explicitly rather than inferred so that
# a new builder can't quietly default into "not checked".
DIALOG_BUILDERS = {
    "_build_prefs_tab",
    "_build_semantic_memory_row",
    "_confirm_model_swap",
    "_build_dialog_from_tab_builder",
}

_fails = []


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label + (("\n      " + detail) if detail else ""))


def mnemonic_of(label):
    """Return the lowercased mnemonic letter in a wx label, or None.

    "&Send" -> "s".  "Kin s&ettings..." -> "e".  "R&&D" (a literal ampersand)
    -> None. wx uses "&&" to mean a real "&" character, same as Win32.
    """
    i = 0
    while i < len(label) - 1:
        if label[i] == "&":
            if label[i + 1] == "&":
                i += 2          # escaped literal "&", not a mnemonic
                continue
            return label[i + 1].lower()
        i += 1
    return None


def enclosing_function(funcs, line):
    """Innermost function containing this line."""
    best = None
    for start, end, name in funcs:
        if start <= line <= end and (best is None or start > best[0]):
            best = (start, name)
    return best[1] if best else "<module>"


def main():
    # Since the 2026-07 modularisation the frame's menu/header/tab builders live
    # in frame/*.py (menus_mixin.py etc.), not hearthkin.pyw. Parse the
    # concatenated frame source so every builder and every menubar.Append is
    # seen. Line numbers below are relative to this concatenation (only used in
    # failure messages), which is fine — the pass/fail logic is self-consistent.
    import glob
    _frame_files = [os.path.join(ROOT, "hearthkin.pyw")] + sorted(
        glob.glob(os.path.join(ROOT, "frame", "*.py")))
    src = "\n".join(open(p, encoding="utf-8").read() for p in _frame_files)
    tree = ast.parse(src)

    funcs = [
        (n.lineno, n.end_lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    # --- what the menu bar claims -------------------------------------
    # menubar.Append(some_menu, "&Kin")  ->  "k"
    menu_letters = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "Append"):
            continue
        if not (isinstance(f.value, ast.Name) and f.value.id == "menubar"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                m = mnemonic_of(arg.value)
                if m:
                    menu_letters[m] = arg.value

    check(
        "menu bar mnemonics were found at all (the parse works)",
        len(menu_letters) >= 3,
        f"found {menu_letters!r} -- if this is empty the test below is "
        f"vacuously green and proves nothing",
    )
    print(f"      menu bar owns: "
          f"{', '.join(f'Alt+{k.upper()} ({v})' for k, v in sorted(menu_letters.items()))}")

    # --- every mnemonic label, and who builds it ----------------------
    # Kept apart because they're classified differently. A `label=` at
    # construction tells you which window the widget belongs to, so its
    # function can be called a builder. A later SetLabel usually happens in an
    # event handler, which builds nothing -- asking someone to file `_on_talk`
    # under FRAME_BUILDERS would be the test telling its own small lie.
    ctor_labels = []    # (line, text, func) -- from label="..."
    set_labels = []     # (line, text, func) -- from .SetLabel("...")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # label="&Send"
        for kw in node.keywords:
            if kw.arg == "label" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str) and "&" in kw.value.value:
                ctor_labels.append((kw.lineno, kw.value.value,
                                    enclosing_function(funcs, kw.lineno)))
        # widget.SetLabel("Stop &talking") -- a label set later is just as
        # visible as one set at construction, and this is how the Talk button
        # flipped back to a dead Alt+T while recording.
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("SetLabel", "SetLabelText"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and "&" in arg.value:
                    set_labels.append((node.lineno, arg.value,
                                       enclosing_function(funcs, node.lineno)))

    # --- new builders must be classified ------------------------------
    builders = {func for _, _, func in ctor_labels}
    unclassified = sorted(builders - FRAME_BUILDERS - DIALOG_BUILDERS)
    check(
        "every builder of mnemonic labels is classified frame-or-dialog",
        not unclassified,
        "unclassified: " + ", ".join(unclassified) +
        "\n      Add each to FRAME_BUILDERS (widget is a child of the main "
        "frame, under the menu bar) or DIALOG_BUILDERS (widget belongs to a "
        "dialog, which has no menu bar). If it's a frame builder its "
        "mnemonics get checked below.",
    )

    # --- the actual rule ----------------------------------------------
    # Constructor labels: checked when the widget is a frame child. Dialog
    # widgets are skipped -- no menu bar above them, nothing to shadow.
    #
    # SetLabel calls: checked wherever they are, unless they sit inside a
    # known dialog builder. An event handler that relabels a frame button is
    # the case that actually bit (the Talk button flipping to "Stop &talking"
    # mid-recording), so the default has to be "check it". A false positive
    # here costs someone one line in DIALOG_BUILDERS; a false negative costs a
    # dead key nobody finds for a year.
    dead = []
    for line, text, func in ctor_labels + set_labels:
        if func in DIALOG_BUILDERS:
            continue
        m = mnemonic_of(text)
        if m and m in menu_letters:
            dead.append(
                f"hearthkin.pyw:{line}  {text!r} claims Alt+{m.upper()}, "
                f"but the menu bar's {menu_letters[m]!r} owns it -- the menu "
                f"opens and this shortcut never fires  (in {func})"
            )

    check(
        "no main-window control claims a shortcut the menu bar already owns",
        not dead,
        "\n      ".join(dead) +
        ("\n      Fix by removing the '&' (a dead mnemonic is worse than none "
         "-- it teaches a key that opens the wrong thing) or by relabelling "
         "the control so its mnemonic is a letter the menu bar doesn't have."
         if dead else ""),
    )

    print()
    if _fails:
        print(f"{len(_fails)} FAILURE(S):")
        for f in _fails:
            print("  - " + f)
        return 1
    print("test_mnemonics.py: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
