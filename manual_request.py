# SPDX-License-Identifier: CC0-1.0
"""A kin that can't open files asks for part of the kin manual (22 Nov 2026).

The kin manual (~/.hearthkin/kin_manual.md) explains how Hearthkin works,
including the shapes a kin can write in a reply for Hearthkin to act on. A
kin with read_file opens it. A kin without tools never could, so the one
document written for it was out of its reach.

Now any kin can write a block that opens ```manual and a part's name
(```manual Surfaces). The part is put in front of it on its next turn, on
whatever surface that is, the way a shared file is. One word to write, not
a tool call, because small models -- most kins without tools -- were tested
and are unreliable with anything heavier. Taught by block_hints.manual_part
(prompt "manual_request_hint"), which lists the parts.

A part with sub-parts (Your tools) is given as its opening and the list of
its sub-parts, each of which can be asked for, rather than all at once: the
manual's own structure decides, not a size limit in the code.
"""

import json
import re

# ```manual Surfaces   /   ```manual: Surfaces   /   ```manual\nSurfaces
FENCE_RE = re.compile(
    r"```[ \t]*manual\b[ \t]*:?[ \t]*([^\n`]*)\n?(.*?)```",
    re.IGNORECASE | re.DOTALL)


def _manual_text():
    try:
        from kin_persistence import KIN_MANUAL_FILE, seed_kin_manual
        if not KIN_MANUAL_FILE.exists():
            seed_kin_manual()
        return KIN_MANUAL_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _parts(text=None):
    """[(level, name, body)] for every ## and ### heading, body running to
    the next heading of the same or a higher level."""
    text = _manual_text() if text is None else text
    heads = [(m.start(), len(m.group(1)), m.group(2).strip())
             for m in re.finditer(r"^(#{2,3})\s+(.+?)\s*$", text, re.M)]
    out = []
    for i, (pos, level, name) in enumerate(heads):
        end = len(text)
        for pos2, level2, _ in heads[i + 1:]:
            if level2 <= level:
                end = pos2
                break
        out.append((level, name, text[pos:end].strip()))
    return out


def section_names(text=None):
    """The top-level parts, for the hint."""
    return [name for level, name, _ in _parts(text) if level == 2]


def _find(name, text=None):
    want = re.sub(r"\s+", " ", str(name or "")).strip().strip("\"'.").casefold()
    if not want:
        return None
    parts = _parts(text)
    for exact in (True, False):
        for level, n, body in parts:
            key = n.casefold()
            if (key == want) if exact else (want in key):
                return level, n, body, parts
    return None


def section_text(name, text=None):
    """(the part's real name, what the kin is given), or (None, "")."""
    found = _find(name, text)
    if not found:
        return None, ""
    level, n, body, parts = found
    subs = []
    if level == 2:
        idx = [p[1] for p in parts].index(n)
        for lv, sub, _ in parts[idx + 1:]:
            if lv <= 2:
                break
            subs.append(sub)
    if subs:
        opening = body.split("\n### ", 1)[0].strip()
        return n, (opening + "\n\nThis part has smaller parts you can ask "
                   "for one at a time: " + "; ".join(subs) + ".")
    return n, body


def _state(kin):
    from kin_persistence import agent_dir
    return agent_dir(kin) / "manual_requests.json"


def _load(kin):
    try:
        p = _state(kin)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        return []


def _save(kin, items):
    from kin_persistence import atomic_write_json
    atomic_write_json(_state(kin), items)


def note_requests(kin, reply_text, shown=None):
    """Record the parts a reply asked for. Returns a note for the kin's
    history ("" when it asked for none)."""
    found = FENCE_RE.findall(reply_text or "")
    if not found:
        return ""
    names = section_names()
    pending = _load(kin)
    got, missing = [], []
    for head, body in found:
        name = head.strip() or body.strip().split("\n", 1)[0].strip()
        real, _ = section_text(name)
        if real:
            if real not in pending:
                pending.append(real)
            got.append(real)
        else:
            missing.append(name or "(no name)")
    try:
        _save(kin, pending)
    except Exception as e:
        _log(kin, "save request", e)
        return ""
    if shown is not None:
        if got:
            shown.append(f"📖 {kin} asked for the manual's part: "
                         + ", ".join(f'"{g}"' for g in got) + ".")
        if missing:
            shown.append(f"📖 {kin} asked for a manual part that doesn't "
                         "exist: " + ", ".join(f'"{m}"' for m in missing) + ".")
    bits = []
    if got:
        bits.append("you'll be given " + ", ".join(f'"{g}"' for g in got)
                    + " next time")
    if missing:
        bits.append("there's no part called " + ", ".join(
            f'"{m}"' for m in missing) + "; the parts are: " + "; ".join(names))
    return "[hearthkin: from the kin manual: " + "; and ".join(bits) + ".]"


def given_block(kin):
    """The parts this kin asked for, to put in front of it now; then
    forgotten. "" when it asked for none."""
    pending = _load(kin)
    if not pending:
        return ""
    try:
        _save(kin, [])
    except Exception as e:
        _log(kin, "clear requests", e)
        return ""
    from kin_persistence import load_app_prompt
    out = []
    for name in pending:
        real, text = section_text(name)
        if real:
            out.append(load_app_prompt("manual_request_given", kin)
                       .replace("{section}", real).replace("{text}", text))
    return "\n\n".join(out)


def _log(kin, action, e):
    try:
        from kin_persistence import append_failure_log
        append_failure_log("kin_messages.log", kin, f"manual {action}", e)
    except Exception:
        pass
