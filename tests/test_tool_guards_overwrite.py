# SPDX-License-Identifier: CC0-1.0
"""Standalone tests for the file-tool guards (no network).

A kin's write_file replaces a file whole. A scheduled run once used it to add
one entry to a long journal and erased every earlier entry; the kin's own
notes already told it not to. These tests pin the guards that make that a
refusal instead of a rule to remember:

  1. write_file refuses to replace an existing, non-empty file the kin hasn't
     read IN FULL this turn.
  2. Any file it does replace is backed up first (a short rolling backup), and
     an edit that shrinks a file is backed up too.
  3. write_file and edit_file refuse if the file changed on disk after the
     kin read it this turn.

Every refusal has a positive control: the legitimate action right next to it
still works. All content here is invented.
"""

import os
import pathlib
import sys
import tempfile
import time

_HOME = tempfile.mkdtemp(prefix="hk-guards-")
os.environ["HEARTHKIN_HOME"] = _HOME

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools as kin_tools                 # noqa: E402
from tools.write_file import write_file   # noqa: E402
from hearthkin_paths import kin_dir       # noqa: E402

_fails = []
KIN = "Wren"


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def turn():
    """A fresh tool set: one turn, one ledger."""
    _, ex = kin_tools.load_tools(
        ["read_file", "write_file", "edit_file", "note"],
        context={"agent_name": KIN})
    return ex


def put(rel, text):
    p = kin_dir(KIN) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))
    return p


def get(rel):
    return (kin_dir(KIN) / rel).read_bytes().decode("utf-8")


def bump(p, text):
    """Change a file behind the kin's back, making sure the timestamp moves."""
    time.sleep(0.02)
    p.write_bytes(text.encode("utf-8"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))


def backups_of(label):
    d = kin_dir(KIN) / "backups" / "files"
    if not d.exists():
        return []
    return sorted(q.name for q in d.iterdir() if q.name.startswith(label + "."))


def main():
    kin_dir(KIN).mkdir(parents=True, exist_ok=True)
    journal = "## Day one\n\nThe herons came back.\n\n## Day two\n\nRain all day.\n"

    # --- Guard 1: the incident itself ---------------------------------- #
    put("memory/journal.md", journal)
    ex = turn()
    r = ex["write_file"]({"path": "memory/journal.md",
                          "content": "## Day three\n\nFog.\n"})
    check("unread overwrite is refused", "refused" in r and "haven't read" in r)
    check("refusal points to note / edit_file", "note" in r and "edit_file" in r)
    check("refused write left the file untouched", get("memory/journal.md") == journal)
    check("refusal says nothing was changed", "Nothing was changed" in r)

    # Positive control: read it all, then the same write goes through.
    r = ex["read_file"]({"path": "memory/journal.md"})
    check("read returns the journal", "The herons came back." in r)
    r = ex["write_file"]({"path": "memory/journal.md",
                          "content": journal + "\n## Day three\n\nFog.\n"})
    check("write after a full read succeeds", r.startswith("write_file: wrote"))
    check("its content landed", "Fog." in get("memory/journal.md"))

    # --- Guard 2: the replaced version was kept ------------------------- #
    b = backups_of("memory__journal.md")
    check("one backup made of the replaced file", len(b) == 1)
    check("write result names the backup", "backups/files/" in r)
    bak = kin_dir(KIN) / "backups" / "files" / b[0] if b else None
    check("backup holds the old journal exactly",
          bak is not None and bak.read_bytes().decode("utf-8") == journal)

    # A kin can rewrite a file it wrote itself this turn without re-reading.
    r = ex["write_file"]({"path": "memory/journal.md",
                          "content": get("memory/journal.md") + "\n## Day four\n\nSun.\n"})
    check("second write of its own file needs no re-read", r.startswith("write_file: wrote"))

    # --- A new turn forgets: yesterday's read is not today's ------------ #
    ex2 = turn()
    r = ex2["write_file"]({"path": "memory/journal.md", "content": "oops\n"})
    check("a read from an earlier turn doesn't count", "refused" in r)
    check("still untouched", "Day four" in get("memory/journal.md"))

    # --- Creating and emptiness need no read ---------------------------- #
    r = ex2["write_file"]({"path": "drafts/new-poem.md", "content": "a small poem\n"})
    check("creating a new file needs no read", r.startswith("write_file: wrote"))
    put("drafts/empty.md", "")
    r = ex2["write_file"]({"path": "drafts/empty.md", "content": "now not empty\n"})
    check("filling an empty file needs no read", r.startswith("write_file: wrote"))

    # --- Partial reads don't license a whole-file write ----------------- #
    long_text = "".join(f"line {i}: a sentence about tides\n" for i in range(1, 201))
    put("memory/long.md", long_text)
    ex3 = turn()
    ex3["read_file"]({"path": "memory/long.md", "start_line": 150, "line_count": 51})
    r = ex3["write_file"]({"path": "memory/long.md", "content": "short\n"})
    check("tail-only read is refused", "refused" in r and "PART" in r)
    check("long file intact", get("memory/long.md") == long_text)
    # Positive control: a line read from line 1 to the end counts as whole.
    ex3["read_file"]({"path": "memory/long.md", "start_line": 1})
    r = ex3["write_file"]({"path": "memory/long.md", "content": long_text + "line 201\n"})
    check("read from line 1 to the end licenses the write", r.startswith("write_file: wrote"))

    # A whole-file read cut off by the 64K cap is NOT a whole read.
    big = "x" * 70000 + "\nthe end\n"
    put("memory/big.md", big)
    ex4 = turn()
    ex4["read_file"]({"path": "memory/big.md"})
    r = ex4["write_file"]({"path": "memory/big.md", "content": "small\n"})
    check("a capped (truncated) read doesn't count as whole", "refused" in r)
    check("big file intact", get("memory/big.md") == big)

    # --- Guard 3: changed since read ------------------------------------ #
    p = put("memory/shared.md", "first version\n")
    ex5 = turn()
    ex5["read_file"]({"path": "memory/shared.md"})
    bump(p, "someone else's newer version\n")
    r = ex5["write_file"]({"path": "memory/shared.md", "content": "kin's version\n"})
    check("write after an outside change is refused", "refused" in r and "changed since" in r)
    check("outside change survived", get("memory/shared.md") == "someone else's newer version\n")
    r = ex5["edit_file"]({"path": "memory/shared.md", "old_string": "newer",
                          "new_string": "older"})
    check("edit after an outside change is refused", "refused" in r and "changed since" in r)
    # Positive control: read again, then both work.
    ex5["read_file"]({"path": "memory/shared.md"})
    r = ex5["edit_file"]({"path": "memory/shared.md", "old_string": "newer",
                          "new_string": "newest"})
    check("edit after re-reading works", "replaced 1 occurrence" in r)
    r = ex5["write_file"]({"path": "memory/shared.md", "content": "settled\n"})
    check("write after its own edit (file already read whole) works",
          r.startswith("write_file: wrote"))

    # edit_file without any read still works -- exact-once match is its guard.
    put("memory/list.md", "- apples\n- pears\n")
    ex6 = turn()
    r = ex6["edit_file"]({"path": "memory/list.md", "old_string": "- pears",
                          "new_string": "- pears\n- plums"})
    check("edit without a read still works", "replaced 1 occurrence" in r)
    check("appending edit made no backup", backups_of("memory__list.md") == [])
    # ...but an edit that takes more away than it adds is backed up.
    r = ex6["edit_file"]({"path": "memory/list.md",
                          "old_string": "- apples\n- pears\n- plums",
                          "new_string": "- figs"})
    check("shrinking edit is backed up", len(backups_of("memory__list.md")) == 1)
    check("shrinking edit result names the backup", "backups/files/" in r)

    # note (append) is never blocked.
    r = ex6["note"]({"path": "memory/journal.md", "content": "A quiet evening."}) \
        if "note" in ex6 else ""
    check("note still appends without a read",
          "A quiet evening." in get("memory/journal.md"))

    # --- Backups roll: only the newest 5 are kept ----------------------- #
    put("memory/hourly.md", "v0\n")
    ex7 = turn()
    for i in range(1, 9):
        ex7["read_file"]({"path": "memory/hourly.md"})
        ex7["write_file"]({"path": "memory/hourly.md", "content": f"v{i}\n"})
    b = backups_of("memory__hourly.md")
    check("backups pruned to 5", len(b) == 5)
    newest = (kin_dir(KIN) / "backups" / "files" / b[-1]).read_text(encoding="utf-8") if b else ""
    check("newest backup is the version just before the last write", newest == "v7\n")

    # --- The model can't supply or see the ledger ----------------------- #
    schemas, ex8 = kin_tools.load_tools(["write_file", "read_file", "edit_file"],
                                        context={"agent_name": KIN})
    props = [set(((s.get("function") or {}).get("parameters") or {})
                 .get("properties", {}).keys()) for s in schemas]
    check("read_ledger is not in any schema", all("read_ledger" not in pr for pr in props))
    r = ex8["write_file"]({"path": "memory/journal.md", "content": "x\n",
                           "read_ledger": None})
    check("a model-sent read_ledger can't switch the guard off", "refused" in r)

    # --- Callers outside a kin turn are unchanged ----------------------- #
    p = put("memory/operator.md", "operator's file\n")
    r = write_file(str(p), "replaced by the app\n", agent_name=KIN)
    check("direct call without a ledger is unguarded (app/scripts)",
          r.startswith("write_file: wrote"))

    # --- The other door: saving a file from a fenced block in a reply --- #
    import authoring_bridge
    AW = authoring_bridge.AuthoringWrite
    spring = "## Spring\n\nPlanted beans.\n"
    summer = "## Summer\n\nTomatoes.\n"
    put("memory/garden.md", spring)
    turn()  # a new turn: nothing read yet
    res = authoring_bridge.commit_authoring_writes(KIN, [AW("memory/garden.md", summer, "fence")])
    check("fenced overwrite of an unread file is refused", bool(res) and not res[0][1])
    check("fenced refusal suggests append:", bool(res) and "append:" in str(res[0][2]))
    check("garden untouched", get("memory/garden.md") == spring)
    # append: is never refused.
    res = authoring_bridge.commit_authoring_writes(KIN, [AW("memory/garden.md", summer, "append")])
    g = get("memory/garden.md")
    check("append: fence still adds", bool(res) and res[0][1]
          and "Tomatoes" in g and "Planted beans" in g)
    # Positive control: read it whole this turn, then a fenced replace lands.
    exb = turn()
    exb["read_file"]({"path": "memory/garden.md"})
    res = authoring_bridge.commit_authoring_writes(KIN, [AW("memory/garden.md", summer, "fence")])
    check("fenced replace after a full read lands", bool(res) and res[0][1]
          and get("memory/garden.md") == summer)
    check("fenced replace was backed up", len(backups_of("memory__garden.md")) == 1)
    # A new file from a fence needs no read.
    res = authoring_bridge.commit_authoring_writes(
        KIN, [AW("drafts/letter.md", "Dear river,\n", "fence")])
    check("fenced new file needs no read", bool(res) and res[0][1])
    # Tool-less memory path is not blocked (no read tool exists there), but backs up.
    put("memory/people.md", "Juniper likes tea.\n")
    res = authoring_bridge.commit_authoring_writes(
        KIN, [AW("memory/people.md", "Juniper likes coffee now.\n", "fence")], guard=False)
    check("tool-less path still saves", bool(res) and res[0][1])
    check("tool-less replace was backed up", len(backups_of("memory__people.md")) == 1)

    # --- Backups stay out of memory search ------------------------------ #
    from tools.memory_search import memory_search
    r = memory_search("herons", agent_name=KIN)
    check("memory search doesn't surface backup copies", ".bak" not in r)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
